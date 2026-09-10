from __future__ import annotations

from pathlib import Path

from computer_use.discovery import DiscoveryEngine, _infer_single_interactive_frame
from computer_use.errors import TargetNotFound
from computer_use.evidence import EvidenceRecorder
from computer_use.models import (
    BusinessOutcomeSpec,
    Condition,
    CssLocator,
    DiscoveryDecision,
    Observation,
    TargetSpec,
    TextCondition,
)
from computer_use.policy import default_policy


class StaleTargetSurface:
    url = "http://127.0.0.1:8000/demo"

    async def navigate(self, url: str, timeout_ms: int) -> None:
        """Store the requested test URL; the timeout is intentionally unused."""
        self.url = url

    async def fill(self, target: TargetSpec, value: str, timeout_ms: int) -> None:
        """Accept a synthetic fill without changing fake surface state."""
        return None

    async def click(self, target: TargetSpec, timeout_ms: int) -> None:
        """Simulate a click whose target disappeared before execution."""
        raise TargetNotFound("role: timed out")

    async def extract(self, target: TargetSpec, timeout_ms: int) -> str:
        """Return an empty synthetic extraction result."""
        return ""

    async def condition_met(self, condition: Condition) -> bool:
        """Report every synthetic checkpoint as satisfied."""
        return True

    async def observe(self, screenshot_path: Path | None = None) -> Observation:
        """Return a minimal account-page observation for discovery tests."""
        return Observation(url=self.url, title="Test", text="Savings Account", interactive_elements=[])


class StaleThenCompleteProvider:
    def __init__(self) -> None:
        """Initialize the number of synthetic decisions issued."""
        self.calls = 0

    async def decide(self, goal: str, observation: dict[str, object]) -> DiscoveryDecision:
        """Return a stale click first and completion on the next observation."""
        self.calls += 1
        if self.calls == 1:
            return DiscoveryDecision(
                kind="click",
                reason="Click the stale search button",
                target=TargetSpec(
                    candidates=[CssLocator(kind="css", selector="#search")]
                ),
            )
        return DiscoveryDecision(kind="complete", reason="Result is now visible")


class UnexpectedProvider:
    async def decide(self, goal: str, observation: dict[str, object]) -> DiscoveryDecision:
        """Fail if discovery incorrectly calls the model after a business outcome."""
        raise AssertionError("The model must not be called after a terminal business outcome")


class NotFoundSurface(StaleTargetSurface):
    async def condition_met(self, condition: Condition) -> bool:
        """Match only the member-not-found condition under test."""
        return condition.kind == "text_visible" and condition.text == "No member found"


class AccountSurface(StaleTargetSurface):
    async def click(self, target: TargetSpec, timeout_ms: int) -> None:
        """Accept synthetic clicks on the account surface."""
        return None

    async def extract(self, target: TargetSpec, timeout_ms: int) -> str:
        """Return the balance or status fixture selected by ``target``."""
        candidate = target.candidates[0]
        if isinstance(candidate, CssLocator) and candidate.selector == "#savings-balance":
            return "$12,345.67"
        return "Active"

    async def condition_met(self, condition: Condition) -> bool:
        """Match only the successful savings-account checkpoint."""
        return condition.kind == "text_visible" and condition.text == "Savings Account"

    async def observe(self, screenshot_path: Path | None = None) -> Observation:
        """Return account text and extractable nodes for model-decision tests."""
        return Observation(
            url=self.url,
            title="Member Detail",
            text="Status Active Savings Account Current Balance $12,345.67",
            interactive_elements=[],
            extractable_elements=[
                {
                    "tag": "td",
                    "id": "member-status",
                    "text": "Active",
                    "frame": "legacy-main",
                },
                {
                    "tag": "td",
                    "id": "savings-balance",
                    "text": "$12,345.67",
                    "frame": "legacy-main",
                },
            ],
        )


class ExtractThenCompleteProvider:
    def __init__(self) -> None:
        """Collect observations supplied to the scripted provider."""
        self.observations: list[dict[str, object]] = []

    async def decide(self, goal: str, observation: dict[str, object]) -> DiscoveryDecision:
        """Request both outputs before allowing discovery to complete."""
        self.observations.append(observation)
        call = len(self.observations)
        if call == 1:
            return DiscoveryDecision(kind="complete", reason="Page is visible")
        if call == 2:
            return DiscoveryDecision(
                kind="extract",
                reason="Read balance",
                target=TargetSpec(
                    frame="legacy-main",
                    candidates=[CssLocator(kind="css", selector="#savings-balance")],
                ),
                output="balance",
            )
        if call == 3:
            return DiscoveryDecision(
                kind="extract",
                reason="Read status",
                target=TargetSpec(
                    frame="legacy-main",
                    candidates=[CssLocator(kind="css", selector="#member-status")],
                ),
                output="member_status",
            )
        return DiscoveryDecision(kind="complete", reason="Required outputs captured")


class EscalateThenCompleteProvider:
    def __init__(self) -> None:
        """Initialize the number of escalation decisions issued."""
        self.calls = 0

    async def decide(self, goal: str, observation: dict[str, object]) -> DiscoveryDecision:
        """Request handoff once, then complete after the executor resumes."""
        self.calls += 1
        if self.calls == 1:
            return DiscoveryDecision(kind="escalate", reason="Operator must clear a dialog")
        return DiscoveryDecision(kind="complete", reason="Operator cleared the dialog")


class RecordingInterventions:
    def __init__(self) -> None:
        """Initialize storage for captured intervention requests."""
        self.requests: list[dict[str, str | None]] = []

    async def request(self, **request: str | None) -> None:
        """Capture one intervention request and immediately simulate resume."""
        self.requests.append(request)


async def test_discovery_reobserves_after_target_disappears(tmp_path: Path) -> None:
    """Verify a stale target causes re-observation rather than artifact corruption."""
    provider = StaleThenCompleteProvider()
    evidence = EvidenceRecorder(tmp_path)
    engine = DiscoveryEngine(StaleTargetSurface(), default_policy(), evidence, provider)

    artifact = await engine.run(
        "Look up a member",
        {},
        "http://127.0.0.1:8000/demo",
        "Savings Account",
        None,
    )

    assert provider.calls == 2
    assert len(artifact.steps) == 1
    assert "discovery_stale_target" in evidence.log_path.read_text()


async def test_discovery_stops_before_model_call_for_business_outcome(tmp_path: Path) -> None:
    """Verify a known terminal outcome stops discovery before another model call."""
    outcome = BusinessOutcomeSpec(
        code="MEMBER_NOT_FOUND",
        condition=TextCondition(
            kind="text_visible",
            text="No member found",
            frame="legacy-main",
        ),
        message="No member exists for the supplied identifier.",
    )
    evidence = EvidenceRecorder(tmp_path, ["99999"])
    engine = DiscoveryEngine(
        NotFoundSurface(),
        default_policy(),
        evidence,
        UnexpectedProvider(),
    )

    artifact = await engine.run(
        "Look up member 99999",
        {"member_id": "99999"},
        "http://127.0.0.1:8000/demo",
        "Savings Account",
        "legacy-main",
        [outcome],
    )

    assert artifact.business_outcomes == [outcome]
    assert artifact.steps[0].kind == "navigate"
    log = evidence.log_path.read_text()
    assert "discovery_business_outcome" in log
    assert "MEMBER_NOT_FOUND" in log
    assert "99999" not in log


def test_discovery_infers_only_available_interactive_frame() -> None:
    """Verify a target inherits the only observed interactive frame."""
    decision = DiscoveryDecision(
        kind="fill",
        reason="Enter member number",
        target=TargetSpec(
            candidates=[CssLocator(kind="css", selector="#member-number")]
        ),
        value="10001",
    )
    observation = {
        "interactive_elements": [
            {
                "tag": "input",
                "name": "Member Number",
                "frame": "legacy-main",
            },
            {
                "tag": "button",
                "name": "Search Records",
                "frame": "legacy-main",
            },
        ]
    }

    inferred = _infer_single_interactive_frame(decision, observation)

    assert inferred == "legacy-main"
    assert decision.target is not None
    assert decision.target.frame == "legacy-main"


async def test_discovery_captures_required_outputs_before_completion(tmp_path: Path) -> None:
    """Verify premature completion is rejected until all required outputs exist."""
    provider = ExtractThenCompleteProvider()
    evidence = EvidenceRecorder(tmp_path)
    engine = DiscoveryEngine(AccountSurface(), default_policy(), evidence, provider)

    artifact = await engine.run(
        "Read member balance and status",
        {"member_id": "10001"},
        "http://127.0.0.1:8000/demo",
        "Savings Account",
        "legacy-main",
        required_outputs={"balance", "member_status"},
    )

    assert set(artifact.outputs) == {"balance", "member_status"}
    assert artifact.outputs["balance"].type == "money"
    extract_steps = [step for step in artifact.steps if step.kind == "extract"]
    assert [step.output for step in extract_steps] == ["balance", "member_status"]
    assert extract_steps[0].transform == "usd"
    assert provider.observations[0]["discovery_progress"] == {
        "completed_actions": ["navigate"],
        "captured_outputs": [],
        "required_outputs": ["balance", "member_status"],
    }
    assert provider.observations[2]["discovery_progress"]["captured_outputs"] == [
        "balance"
    ]
    assert "discovery_premature_completion" in evidence.log_path.read_text()


async def test_discovery_hands_off_and_resumes_same_loop(tmp_path: Path) -> None:
    """Verify model escalation resumes the original discovery loop and evidence."""
    provider = EscalateThenCompleteProvider()
    interventions = RecordingInterventions()
    evidence = EvidenceRecorder(tmp_path)
    engine = DiscoveryEngine(
        AccountSurface(),
        default_policy(),
        evidence,
        provider,
        interventions,
    )

    artifact = await engine.run(
        "Read member balance",
        {},
        "http://127.0.0.1:8000/demo",
        "Savings Account",
        "legacy-main",
    )

    assert artifact.capability_id == "discovered.member-workflow"
    assert provider.calls == 2
    assert interventions.requests == [
        {
            "reason": "Operator must clear a dialog",
            "capability_id": "discovered.member-workflow",
            "goal": "Read member balance",
            "current_step": "discovered-00",
            "kind": "manual_recovery",
        }
    ]
    assert "discovery_resumed_after_handoff" in evidence.log_path.read_text()
