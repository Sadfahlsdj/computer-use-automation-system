from pathlib import Path

from computer_use.artifacts import load_artifact
from computer_use.errors import TargetNotFound
from computer_use.evidence import EvidenceRecorder
from computer_use.models import Condition, Observation, RiskLevel, RunStatus, TargetSpec
from computer_use.policy import default_policy
from computer_use.replay import ReplayEngine

ROOT = Path(__file__).resolve().parents[1]


class FakeSurface:
    def __init__(self, not_found: bool = False) -> None:
        """Initialize deterministic fake state, optionally in a not-found outcome."""
        self._url = "about:blank"
        self.not_found = not_found
        self.values: dict[str, str] = {}

    @property
    def url(self) -> str:
        """Return the fake surface's current location."""
        return self._url

    async def navigate(self, url: str, timeout_ms: int) -> None:
        """Store the requested test URL without real navigation."""
        self._url = url

    async def fill(self, target: TargetSpec, value: str, timeout_ms: int) -> None:
        """Capture the supplied member ID without touching a browser."""
        self.values["member_id"] = value

    async def click(self, target: TargetSpec, timeout_ms: int) -> None:
        """Accept a synthetic click without changing fake state."""
        return None

    async def extract(self, target: TargetSpec, timeout_ms: int) -> str:
        """Return the balance or status fixture selected by ``target``."""
        selector = target.candidates[0]
        if getattr(selector, "selector", "") == "#savings-balance":
            return "$12,345.67"
        return "Active"

    async def condition_met(self, condition: Condition) -> bool:
        """Return the configured business or success outcome for ``condition``."""
        if condition.kind == "text_visible" and condition.text == "No member found":
            return self.not_found
        return not self.not_found

    async def observe(self, screenshot_path: Path | None = None) -> Observation:
        """Reject observation because deterministic replay must not request it."""
        raise NotImplementedError


class RecordingInterventions:
    def __init__(self) -> None:
        """Initialize storage for intervention requests."""
        self.requests: list[dict[str, str | None]] = []

    async def request(self, **request: str | None) -> None:
        """Capture an intervention and immediately simulate returned control."""
        self.requests.append(request)


class RecoverableSurface(FakeSurface):
    def __init__(self) -> None:
        """Initialize a fake surface that fails its first click only."""
        super().__init__()
        self.failed_once = False

    async def click(self, target: TargetSpec, timeout_ms: int) -> None:
        """Raise once to trigger handoff, then accept the retried click."""
        if not self.failed_once:
            self.failed_once = True
            raise TargetNotFound("button temporarily unavailable")


async def test_replay_success(tmp_path: Path) -> None:
    """Verify deterministic replay returns typed balance and status outputs."""
    artifact = load_artifact(ROOT / "evidence/capabilities/member-read-savings.yaml")
    evidence = EvidenceRecorder(tmp_path, ["10001"])
    result = await ReplayEngine(FakeSurface(), default_policy(), evidence).run(
        artifact, {"member_id": "10001"}
    )
    assert result.status == RunStatus.SUCCESS
    assert result.outputs == {"balance": 12345.67, "member_status": "Active"}
    assert "10001" not in evidence.log_path.read_text()


async def test_replay_business_outcome(tmp_path: Path) -> None:
    """Verify member-not-found is returned as a business outcome, not failure."""
    artifact = load_artifact(ROOT / "evidence/capabilities/member-read-savings.yaml")
    result = await ReplayEngine(
        FakeSurface(not_found=True), default_policy(), EvidenceRecorder(tmp_path, ["99999"])
    ).run(artifact, {"member_id": "99999"})
    assert result.status == RunStatus.BUSINESS_OUTCOME
    assert result.code == "MEMBER_NOT_FOUND"


async def test_replay_rejects_invalid_input(tmp_path: Path) -> None:
    """Verify invocation values are validated before browser actions begin."""
    artifact = load_artifact(ROOT / "evidence/capabilities/member-read-savings.yaml")
    result = await ReplayEngine(
        FakeSurface(), default_policy(), EvidenceRecorder(tmp_path)
    ).run(artifact, {"member_id": "abc"})
    assert result.status == RunStatus.FAILURE
    assert result.category == "invalid_input"


async def test_replay_hands_off_for_irreversible_step_then_resumes(tmp_path: Path) -> None:
    """Verify one approved irreversible step resumes and completes replay."""
    artifact = load_artifact(ROOT / "evidence/capabilities/member-read-savings.yaml")
    artifact.steps[2].risk = RiskLevel.IRREVERSIBLE
    interventions = RecordingInterventions()

    result = await ReplayEngine(
        FakeSurface(),
        default_policy(),
        EvidenceRecorder(tmp_path, ["10001"]),
        interventions,
    ).run(artifact, {"member_id": "10001"})

    assert result.status == RunStatus.SUCCESS
    assert interventions.requests[0]["current_step"] == artifact.steps[2].id
    assert "requires human confirmation" in str(interventions.requests[0]["reason"])


async def test_replay_hands_off_and_retries_failed_target(tmp_path: Path) -> None:
    """Verify target failure routes to an operator before one post-handoff retry."""
    artifact = load_artifact(ROOT / "evidence/capabilities/member-read-savings.yaml")
    interventions = RecordingInterventions()

    result = await ReplayEngine(
        RecoverableSurface(),
        default_policy(),
        EvidenceRecorder(tmp_path, ["10001"]),
        interventions,
    ).run(artifact, {"member_id": "10001"})

    assert result.status == RunStatus.SUCCESS
    assert len(interventions.requests) == 1
    assert "temporarily unavailable" in str(interventions.requests[0]["reason"])
