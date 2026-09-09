from pathlib import Path

from computer_use.artifacts import load_artifact
from computer_use.evidence import EvidenceRecorder
from computer_use.models import Condition, Observation, RunStatus, TargetSpec
from computer_use.policy import default_policy
from computer_use.replay import ReplayEngine

ROOT = Path(__file__).resolve().parents[1]


class FakeSurface:
    def __init__(self, not_found: bool = False) -> None:
        self._url = "about:blank"
        self.not_found = not_found
        self.values: dict[str, str] = {}

    @property
    def url(self) -> str:
        return self._url

    async def navigate(self, url: str, timeout_ms: int) -> None:
        self._url = url

    async def fill(self, target: TargetSpec, value: str, timeout_ms: int) -> None:
        self.values["member_id"] = value

    async def click(self, target: TargetSpec, timeout_ms: int) -> None:
        return None

    async def extract(self, target: TargetSpec, timeout_ms: int) -> str:
        selector = target.candidates[0]
        if getattr(selector, "selector", "") == "#savings-balance":
            return "$12,345.67"
        return "Active"

    async def condition_met(self, condition: Condition) -> bool:
        if condition.kind == "text_visible" and condition.text == "No member found":
            return self.not_found
        return not self.not_found

    async def observe(self, screenshot_path: Path | None = None) -> Observation:
        raise NotImplementedError


async def test_replay_success(tmp_path: Path) -> None:
    artifact = load_artifact(ROOT / "evidence/capabilities/member-read-savings.yaml")
    evidence = EvidenceRecorder(tmp_path, ["10001"])
    result = await ReplayEngine(FakeSurface(), default_policy(), evidence).run(
        artifact, {"member_id": "10001"}
    )
    assert result.status == RunStatus.SUCCESS
    assert result.outputs == {"balance": 12345.67, "member_status": "Active"}
    assert "10001" not in evidence.log_path.read_text()


async def test_replay_business_outcome(tmp_path: Path) -> None:
    artifact = load_artifact(ROOT / "evidence/capabilities/member-read-savings.yaml")
    result = await ReplayEngine(
        FakeSurface(not_found=True), default_policy(), EvidenceRecorder(tmp_path, ["99999"])
    ).run(artifact, {"member_id": "99999"})
    assert result.status == RunStatus.BUSINESS_OUTCOME
    assert result.code == "MEMBER_NOT_FOUND"


async def test_replay_rejects_invalid_input(tmp_path: Path) -> None:
    artifact = load_artifact(ROOT / "evidence/capabilities/member-read-savings.yaml")
    result = await ReplayEngine(
        FakeSurface(), default_policy(), EvidenceRecorder(tmp_path)
    ).run(artifact, {"member_id": "abc"})
    assert result.status == RunStatus.FAILURE
    assert result.category == "invalid_input"
