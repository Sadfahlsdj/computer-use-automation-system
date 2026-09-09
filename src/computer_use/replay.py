from __future__ import annotations

import re
from typing import Any

from computer_use.errors import AutomationError, CheckpointFailed
from computer_use.evidence import EvidenceRecorder
from computer_use.models import (
    CapabilityArtifact,
    RunResult,
    RunStatus,
)
from computer_use.policy import PolicyEngine
from computer_use.surface import Surface


def _render(template: str, inputs: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in inputs:
            raise ValueError(f"Missing input: {key}")
        return str(inputs[key])

    return re.sub(r"\{\{\s*inputs\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", replace, template)


def _transform(value: str, transform: str) -> str | float:
    if transform == "usd":
        return float(value.replace("$", "").replace(",", "").strip())
    return value


def validate_inputs(artifact: CapabilityArtifact, inputs: dict[str, Any]) -> None:
    unknown = set(inputs) - set(artifact.inputs)
    missing = set(artifact.inputs) - set(inputs)
    if unknown:
        raise ValueError(f"Unknown inputs: {sorted(unknown)}")
    if missing:
        raise ValueError(f"Missing inputs: {sorted(missing)}")
    for name, spec in artifact.inputs.items():
        value = inputs[name]
        if spec.type == "string" and not isinstance(value, str):
            raise ValueError(f"Input {name} must be a string")
        if spec.pattern and re.fullmatch(spec.pattern, str(value)) is None:
            raise ValueError(f"Input {name} does not match its required pattern")


class ReplayEngine:
    def __init__(self, surface: Surface, policy: PolicyEngine, evidence: EvidenceRecorder) -> None:
        self.surface = surface
        self.policy = policy
        self.evidence = evidence

    async def _business_outcome(self, artifact: CapabilityArtifact) -> RunResult | None:
        for outcome in artifact.business_outcomes:
            if await self.surface.condition_met(outcome.condition):
                self.evidence.record("business_outcome", code=outcome.code)
                return RunResult(
                    status=RunStatus.BUSINESS_OUTCOME,
                    code=outcome.code,
                    message=outcome.message,
                    evidence_id=self.evidence.evidence_id,
                )
        return None

    async def run(self, artifact: CapabilityArtifact, inputs: dict[str, Any]) -> RunResult:
        try:
            validate_inputs(artifact, inputs)
        except ValueError as error:
            return RunResult(
                status=RunStatus.FAILURE,
                category="invalid_input",
                message=str(error),
                evidence_id=self.evidence.evidence_id,
            )
        self.evidence.record(
            "run_started",
            capability_id=artifact.capability_id,
            inputs=inputs,
        )
        outputs: dict[str, Any] = {}
        current_step: str | None = None
        try:
            if len(artifact.steps) > self.policy.config.max_steps:
                raise ValueError("Artifact exceeds the configured maximum step count")
            for step in artifact.steps:
                current_step = step.id
                self.policy.check_step(step, self.surface.url if self.surface.url != "about:blank" else None)
                self.evidence.record("step_started", step_id=step.id, action=step.kind)
                attempts = min(getattr(step, "retries", 0), self.policy.config.max_retries_per_step) + 1
                for attempt in range(attempts):
                    try:
                        if step.kind == "navigate":
                            await self.surface.navigate(_render(step.url, inputs), step.timeout_ms)
                        elif step.kind == "fill":
                            await self.surface.fill(step.target, _render(step.value, inputs), step.timeout_ms)
                        elif step.kind == "click":
                            await self.surface.click(step.target, step.timeout_ms)
                        else:
                            raw = await self.surface.extract(step.target, step.timeout_ms)
                            outputs[step.output] = _transform(raw, step.transform)
                        break
                    except Exception:
                        if attempt + 1 >= attempts:
                            raise
                self.evidence.record("step_completed", step_id=step.id)
                outcome = await self._business_outcome(artifact)
                if outcome:
                    return outcome
            unmet = [
                condition.model_dump(mode="json")
                for condition in artifact.checkpoint
                if not await self.surface.condition_met(condition)
            ]
            if unmet:
                raise CheckpointFailed(f"Unmet checkpoint conditions: {unmet}")
            self.evidence.record("run_completed", outputs=outputs)
            return RunResult(
                status=RunStatus.SUCCESS,
                outputs=outputs,
                message="Capability replay completed",
                evidence_id=self.evidence.evidence_id,
            )
        except AutomationError as error:
            self.evidence.record(
                "run_failed",
                step_id=current_step,
                category=error.category,
                error=str(error),
            )
            return RunResult(
                status=RunStatus.FAILURE,
                category=error.category,
                message=str(error),
                step_id=current_step,
                retryable=error.retryable,
                evidence_id=self.evidence.evidence_id,
            )
        except Exception as error:
            self.evidence.record("run_failed", step_id=current_step, category="application_error", error=str(error))
            return RunResult(
                status=RunStatus.FAILURE,
                category="application_error",
                message=str(error),
                step_id=current_step,
                evidence_id=self.evidence.evidence_id,
            )
