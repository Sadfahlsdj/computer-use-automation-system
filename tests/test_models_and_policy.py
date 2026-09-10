from pathlib import Path

import pytest

from computer_use.artifacts import load_artifact
from computer_use.errors import PolicyViolation
from computer_use.models import (
    ClickStep,
    DiscoveryDecision,
    RiskLevel,
    RoleLocator,
    TargetSpec,
)
from computer_use.policy import PolicyEngine, default_policy

ROOT = Path(__file__).resolve().parents[1]


def test_example_artifact_is_valid() -> None:
    artifact = load_artifact(ROOT / "evidence/capabilities/member-read-savings.yaml")
    assert artifact.schema_version == "1.0"
    assert artifact.capability_id == "member.read-savings-balance"
    assert set(artifact.outputs) == {"balance", "member_status"}


def test_irreversible_step_requires_approval() -> None:
    policy: PolicyEngine = default_policy()
    step = ClickStep(
        id="commit",
        risk=RiskLevel.IRREVERSIBLE,
        target=TargetSpec(
            candidates=[RoleLocator(kind="role", role="button", name="Create Account")]
        ),
    )
    with pytest.raises(PolicyViolation, match="requires human confirmation"):
        policy.check_step(step, "http://127.0.0.1:8000/demo")
    policy.approve("commit")
    policy.check_step(step, "http://127.0.0.1:8000/demo")


def test_policy_blocks_external_origin() -> None:
    with pytest.raises(PolicyViolation, match="Origin is not allowed"):
        default_policy().check_url("https://example.com/demo")


@pytest.mark.parametrize(
    ("decision", "missing"),
    [
        ({"kind": "navigate", "reason": "go"}, "url"),
        ({"kind": "fill", "reason": "fill"}, "target, value"),
        ({"kind": "click", "reason": "click"}, "target"),
        ({"kind": "extract", "reason": "read"}, "target, output"),
    ],
)
def test_discovery_decision_requires_fields_for_action_kind(
    decision: dict[str, str],
    missing: str,
) -> None:
    with pytest.raises(ValueError, match=missing):
        DiscoveryDecision.model_validate(decision)
