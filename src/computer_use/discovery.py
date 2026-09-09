from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from time import monotonic
from typing import Any, Protocol

from openai import AsyncOpenAI
from pydantic import ValidationError

from computer_use.errors import TargetNotFound
from computer_use.evidence import EvidenceRecorder
from computer_use.models import (
    CapabilityArtifact,
    ClickStep,
    DiscoveryDecision,
    ExtractStep,
    FillStep,
    NavigateStep,
    OutputSpec,
    ParameterSpec,
    RiskLevel,
    TextCondition,
)
from computer_use.policy import PolicyEngine
from computer_use.surface import Surface

logger = logging.getLogger(__name__)


class DecisionProvider(Protocol):
    async def decide(self, goal: str, observation: dict[str, Any]) -> DiscoveryDecision: ...


SYSTEM_PROMPT = """You operate a synthetic back-office web application.
Return one JSON action at a time. Allowed kinds: navigate, fill, click, extract, complete, escalate.
For target actions, include a robust TargetSpec using role/name or label first and CSS only as fallback.
Use the frame name shown in the observation. Never invent credentials or bypass policy.
The goal is complete only after requested values have been extracted and the success state is visible.
Return only JSON matching the provided schema."""

MAX_DECISION_ATTEMPTS = 2


class OpenRouterDecisionProvider:
    def __init__(self, model: str, api_key: str) -> None:
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers={"X-Title": "Computer-Use Automation System"},
        )
        self.model = model

    async def decide(self, goal: str, observation: dict[str, Any]) -> DiscoveryDecision:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": json.dumps({"goal": goal, "observation": observation}, indent=2),
            }
        ]
        screenshot_path = observation.get("screenshot_path")
        if screenshot_path:
            encoded = base64.b64encode(Path(screenshot_path).read_bytes()).decode()
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}
            )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "discovery_decision",
                "strict": True,
                "schema": DiscoveryDecision.model_json_schema(),
            },
        }
        for attempt in range(MAX_DECISION_ATTEMPTS):
            attempt_number = attempt + 1
            logger.info(
                "Waiting for OpenRouter response (model=%s, attempt=%d/%d)",
                self.model,
                attempt_number,
                MAX_DECISION_ATTEMPTS,
            )
            started_at = monotonic()
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format=response_format,
                    temperature=0,
                    extra_body={"provider": {"require_parameters": True}},
                )
            except Exception as error:
                logger.error(
                    "OpenRouter request failed after %.1fs (%s)",
                    monotonic() - started_at,
                    type(error).__name__,
                )
                raise
            logger.info("OpenRouter responded in %.1fs", monotonic() - started_at)
            raw = response.choices[0].message.content
            if not raw:
                raise RuntimeError("The model returned an empty decision")
            try:
                return DiscoveryDecision.model_validate_json(raw)
            except ValidationError as error:
                issues = [
                    {
                        "location": ".".join(str(part) for part in issue["loc"]),
                        "message": issue["msg"],
                    }
                    for issue in error.errors(include_input=False, include_url=False)
                ]
                if attempt == MAX_DECISION_ATTEMPTS - 1:
                    summary = "; ".join(
                        f"{issue['location']}: {issue['message']}" for issue in issues
                    )
                    raise RuntimeError(
                        "OpenRouter returned an invalid discovery decision after "
                        f"{MAX_DECISION_ATTEMPTS} attempts: {summary}"
                    ) from error
                logger.warning(
                    "Decision failed schema validation; requesting one correction (%s)",
                    ", ".join(issue["location"] for issue in issues),
                )
                messages.extend(
                    [
                        {"role": "assistant", "content": raw},
                        {
                            "role": "user",
                            "content": (
                                "Correct the previous response so it matches the required JSON schema. "
                                f"Validation errors: {json.dumps(issues)}"
                            ),
                        },
                    ]
                )
        raise AssertionError("Decision attempt loop exited unexpectedly")


class DiscoveryEngine:
    def __init__(
        self,
        surface: Surface,
        policy: PolicyEngine,
        evidence: EvidenceRecorder,
        provider: DecisionProvider,
    ) -> None:
        self.surface = surface
        self.policy = policy
        self.evidence = evidence
        self.provider = provider

    async def run(
        self,
        goal: str,
        inputs: dict[str, str],
        start_url: str,
        checkpoint_text: str,
        checkpoint_frame: str | None,
    ) -> CapabilityArtifact:
        recorded: list[NavigateStep | FillStep | ClickStep | ExtractStep] = []
        outputs: dict[str, OutputSpec] = {}
        initial = NavigateStep(id="open-target", url=start_url)
        self.policy.check_step(initial)
        logger.info("Navigating to discovery start page: %s", start_url)
        await self.surface.navigate(start_url, initial.timeout_ms)
        logger.info("Start page loaded")
        recorded.append(initial)
        for index in range(self.policy.config.max_steps):
            screenshot = self.evidence.run_dir / f"discovery-{index:02d}.png"
            logger.info(
                "Discovery step %d/%d: capturing page observation",
                index + 1,
                self.policy.config.max_steps,
            )
            observation = await self.surface.observe(screenshot)
            safe_observation = self.evidence.redact(observation.model_dump(mode="json"))
            decision = await self.provider.decide(goal, safe_observation)
            logger.info("Discovery step %d: model selected %s", index + 1, decision.kind)
            self.evidence.record("discovery_decision", index=index, decision=decision.model_dump())
            step_id = f"discovered-{index:02d}"
            if decision.kind == "complete":
                logger.info("Model declared completion; verifying checkpoint")
                checkpoint = TextCondition(
                    kind="text_visible", text=checkpoint_text, frame=checkpoint_frame
                )
                if not await self.surface.condition_met(checkpoint):
                    raise RuntimeError("Model declared completion before the checkpoint was satisfied")
                logger.info("Discovery checkpoint verified")
                risk_order = {
                    RiskLevel.READ_ONLY: 0,
                    RiskLevel.REVERSIBLE: 1,
                    RiskLevel.IRREVERSIBLE: 2,
                }
                return CapabilityArtifact(
                    capability_id="discovered.member-workflow",
                    capability_version="0.1.0",
                    title="Discovered member workflow",
                    description=goal,
                    risk=max(
                        (step.risk for step in recorded),
                        default=RiskLevel.READ_ONLY,
                        key=risk_order.__getitem__,
                    ),
                    target_product="northstar-core-training",
                    inputs={
                        name: ParameterSpec(
                            type="string",
                            description=f"Invocation value for {name}",
                            sensitive=True,
                        )
                        for name in inputs
                    },
                    outputs=outputs,
                    steps=recorded,
                    checkpoint=[checkpoint],
                )
            if decision.kind == "escalate":
                raise RuntimeError(f"Discovery requested human intervention: {decision.reason}")
            try:
                if decision.kind == "navigate" and decision.url:
                    step = NavigateStep(id=step_id, url=decision.url)
                    self.policy.check_step(step, self.surface.url)
                    logger.info("Applying navigate action")
                    await self.surface.navigate(step.url, step.timeout_ms)
                elif decision.kind == "fill" and decision.target and decision.value is not None:
                    value = decision.value
                    template = value
                    for name, supplied in inputs.items():
                        if value == supplied:
                            template = f"{{{{ inputs.{name} }}}}"
                    step = FillStep(
                        id=step_id,
                        target=decision.target,
                        value=template,
                        sensitive=True,
                    )
                    self.policy.check_step(step, self.surface.url)
                    logger.info("Applying fill action (value hidden)")
                    await self.surface.fill(step.target, value, step.timeout_ms)
                elif decision.kind == "click" and decision.target:
                    step = ClickStep(id=step_id, target=decision.target)
                    self.policy.check_step(step, self.surface.url)
                    logger.info("Applying click action")
                    await self.surface.click(step.target, step.timeout_ms)
                elif decision.kind == "extract" and decision.target and decision.output:
                    step = ExtractStep(id=step_id, target=decision.target, output=decision.output)
                    self.policy.check_step(step, self.surface.url)
                    logger.info("Applying extract action (output=%s)", decision.output)
                    await self.surface.extract(step.target, step.timeout_ms)
                    outputs[decision.output] = OutputSpec(
                        type="string", description=f"Discovered output {decision.output}"
                    )
                else:
                    raise RuntimeError(f"Incomplete model decision: {decision.model_dump()}")
            except TargetNotFound as error:
                logger.warning(
                    "Discovery step %d: target disappeared; re-observing the page",
                    index + 1,
                )
                self.evidence.record(
                    "discovery_stale_target",
                    index=index,
                    action=decision.kind,
                    error=str(error),
                )
                continue
            recorded.append(step)
            logger.info("Discovery step %d: action completed", index + 1)
        raise RuntimeError("Discovery reached the maximum step count")
