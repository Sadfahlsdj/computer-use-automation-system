from __future__ import annotations

from pathlib import Path

from computer_use.discovery import DiscoveryEngine
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
        self.url = url

    async def fill(self, target: TargetSpec, value: str, timeout_ms: int) -> None:
        return None

    async def click(self, target: TargetSpec, timeout_ms: int) -> None:
        raise TargetNotFound("role: timed out")

    async def extract(self, target: TargetSpec, timeout_ms: int) -> str:
        return ""

    async def condition_met(self, condition: Condition) -> bool:
        return True

    async def observe(self, screenshot_path: Path | None = None) -> Observation:
        return Observation(url=self.url, title="Test", text="Savings Account", interactive_elements=[])


class StaleThenCompleteProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, goal: str, observation: dict[str, object]) -> DiscoveryDecision:
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
        raise AssertionError("The model must not be called after a terminal business outcome")


class NotFoundSurface(StaleTargetSurface):
    async def condition_met(self, condition: Condition) -> bool:
        return condition.kind == "text_visible" and condition.text == "No member found"


async def test_discovery_reobserves_after_target_disappears(tmp_path: Path) -> None:
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
