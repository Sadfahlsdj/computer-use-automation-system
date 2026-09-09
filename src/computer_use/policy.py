from __future__ import annotations

from urllib.parse import urlparse

from computer_use.errors import PolicyViolation
from computer_use.models import PolicyConfig, RiskLevel, Step


class PolicyEngine:
    def __init__(self, config: PolicyConfig) -> None:
        self.config = config
        self._approved_steps: set[str] = set()

    def approve(self, step_id: str) -> None:
        self._approved_steps.add(step_id)

    def check_url(self, url: str) -> None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.config.allowed_origins:
            raise PolicyViolation(f"Origin is not allowed: {origin}")
        if not any(parsed.path.startswith(prefix) for prefix in self.config.allowed_path_prefixes):
            raise PolicyViolation(f"Path is not allowed: {parsed.path}")

    def check_step(self, step: Step, current_url: str | None = None) -> None:
        if step.kind not in self.config.allowed_actions:
            raise PolicyViolation(f"Action is not allowed: {step.kind}")
        if step.risk in self.config.require_confirmation_for and step.id not in self._approved_steps:
            raise PolicyViolation(f"Step {step.id!r} requires human confirmation")
        if step.kind == "navigate":
            self.check_url(step.url)
        elif current_url:
            self.check_url(current_url)


def default_policy(origin: str = "http://127.0.0.1:8000") -> PolicyEngine:
    return PolicyEngine(
        PolicyConfig(
            allowed_origins=[origin],
            allowed_path_prefixes=["/demo"],
            allowed_actions={"navigate", "fill", "click", "extract"},
            require_confirmation_for={RiskLevel.IRREVERSIBLE},
        )
    )
