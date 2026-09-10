from __future__ import annotations

import asyncio
import base64
import json
import logging
from contextlib import suppress
from pathlib import Path
from time import monotonic
from typing import Any, Protocol

from openai import AsyncOpenAI
from pydantic import ValidationError

from computer_use.errors import TargetNotFound
from computer_use.evidence import EvidenceRecorder
from computer_use.models import (
    BusinessOutcomeSpec,
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
Every decision requires reason. Use target (not target_spec) and value (not text) for fill actions.
Each target candidate requires a kind and the fields for that kind: role uses role/name, label and text
use value, and css uses selector. Do not use HTML input types such as input as ARIA roles.
Navigate requires url; fill requires target and value; click requires target; extract requires target and output.
Use the frame name shown in the observation. Never invent credentials or bypass policy.
The goal is complete only after requested values have been extracted and the success state is visible.
Use the exact required output names from discovery_progress. Prefer ID-based CSS candidates from
extractable_elements when extracting page values.
Return only JSON matching the provided schema."""

MAX_DECISION_ATTEMPTS = 2
DEFAULT_REQUEST_TIMEOUT_SECONDS = 120.0
REQUEST_HEARTBEAT_SECONDS = 15.0


def _infer_missing_locator_kinds(payload: dict[str, Any]) -> list[int]:
    target = payload.get("target")
    if not isinstance(target, dict):
        return []
    candidates = target.get("candidates")
    if not isinstance(candidates, list):
        return []
    repaired: list[int] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict) or candidate.get("kind"):
            continue
        if candidate.get("selector") is not None:
            candidate["kind"] = "css"
        elif candidate.get("role") is not None and candidate.get("name") is not None:
            candidate["kind"] = "role"
        else:
            continue
        repaired.append(index)
    return repaired


def _infer_missing_extract_output(
    payload: dict[str, Any],
    observation: dict[str, Any],
) -> str | None:
    if payload.get("kind") != "extract" or payload.get("output"):
        return None
    progress = observation.get("discovery_progress", {})
    if not isinstance(progress, dict):
        return None
    required = {str(name) for name in progress.get("required_outputs", [])}
    captured = {str(name) for name in progress.get("captured_outputs", [])}
    remaining = sorted(required - captured)
    if len(remaining) == 1:
        return remaining[0]
    if not remaining:
        return None

    target_text = json.dumps(payload.get("target", {}), sort_keys=True).casefold()
    decision_text = json.dumps(
        {"reason": payload.get("reason", ""), "target": payload.get("target", {})},
        sort_keys=True,
    ).casefold()
    scores: dict[str, int] = {}
    for output in remaining:
        terms = output.casefold().replace("_", " ").split()
        score = sum(term in decision_text for term in terms)
        if terms and terms[-1] in target_text:
            score += 10
        scores[output] = score
    best_score = max(scores.values(), default=0)
    winners = [name for name, score in scores.items() if score == best_score]
    return winners[0] if best_score > 0 and len(winners) == 1 else None


def _infer_single_interactive_frame(
    decision: DiscoveryDecision,
    observation: dict[str, Any],
) -> str | None:
    if decision.target is None or decision.target.frame is not None:
        return None
    elements = observation.get("interactive_elements", [])
    if not isinstance(elements, list) or not elements:
        return None
    frame_names = {
        element.get("frame")
        for element in elements
        if isinstance(element, dict) and element.get("frame")
    }
    has_top_level_control = any(
        isinstance(element, dict) and not element.get("frame") for element in elements
    )
    if len(frame_names) != 1 or has_top_level_control:
        return None
    inferred = str(frame_names.pop())
    decision.target.frame = inferred
    return inferred


def _build_artifact(
    goal: str,
    inputs: dict[str, str],
    recorded: list[NavigateStep | FillStep | ClickStep | ExtractStep],
    outputs: dict[str, OutputSpec],
    checkpoint: TextCondition,
    business_outcomes: list[BusinessOutcomeSpec],
) -> CapabilityArtifact:
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
        business_outcomes=business_outcomes,
        checkpoint=[checkpoint],
    )


class OpenRouterDecisionProvider:
    def __init__(
        self,
        model: str,
        api_key: str,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        heartbeat_seconds: float = REQUEST_HEARTBEAT_SECONDS,
    ) -> None:
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers={"X-Title": "Computer-Use Automation System"},
            max_retries=0,
            timeout=request_timeout_seconds,
        )
        self.model = model
        self.request_timeout_seconds = request_timeout_seconds
        self.heartbeat_seconds = heartbeat_seconds

    async def _request(self, **kwargs: Any) -> Any:
        request = asyncio.create_task(self.client.chat.completions.create(**kwargs))
        started_at = monotonic()
        try:
            async with asyncio.timeout(self.request_timeout_seconds):
                while True:
                    done, _ = await asyncio.wait({request}, timeout=self.heartbeat_seconds)
                    if request in done:
                        return request.result()
                    logger.info(
                        "Still waiting for OpenRouter (%.0fs elapsed, %.0fs timeout)",
                        monotonic() - started_at,
                        self.request_timeout_seconds,
                    )
        except TimeoutError as error:
            raise RuntimeError(
                "OpenRouter did not respond within "
                f"{self.request_timeout_seconds:.0f}s; retry the discovery run or choose another model"
            ) from error
        finally:
            if not request.done():
                request.cancel()
                with suppress(asyncio.CancelledError):
                    await request

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
                response = await self._request(
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
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    return DiscoveryDecision.model_validate_json(raw)
                if isinstance(payload, dict):
                    repaired_candidates = _infer_missing_locator_kinds(payload)
                    if repaired_candidates:
                        logger.warning(
                            "Inferred missing locator kind from candidate fields (candidates=%s)",
                            ",".join(str(index) for index in repaired_candidates),
                        )
                    inferred_output = _infer_missing_extract_output(payload, observation)
                    if inferred_output:
                        payload["output"] = inferred_output
                        logger.warning(
                            "Inferred missing extract output from discovery context (output=%s)",
                            inferred_output,
                        )
                    return DiscoveryDecision.model_validate(payload)
                return DiscoveryDecision.model_validate_json(raw)
            except ValidationError as error:
                issues = [
                    {
                        "location": ".".join(str(part) for part in issue["loc"])
                        or "<decision>",
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
        business_outcomes: list[BusinessOutcomeSpec] | None = None,
        required_outputs: set[str] | None = None,
    ) -> CapabilityArtifact:
        recorded: list[NavigateStep | FillStep | ClickStep | ExtractStep] = []
        outputs: dict[str, OutputSpec] = {}
        configured_outcomes = business_outcomes or []
        required = required_outputs or set()
        self.business_outcome_code: str | None = None
        checkpoint = TextCondition(
            kind="text_visible", text=checkpoint_text, frame=checkpoint_frame
        )
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
            for outcome in configured_outcomes:
                if await self.surface.condition_met(outcome.condition):
                    self.business_outcome_code = outcome.code
                    logger.info(
                        "Terminal business outcome recognized (code=%s)",
                        outcome.code,
                    )
                    self.evidence.record(
                        "discovery_business_outcome",
                        index=index,
                        code=outcome.code,
                    )
                    return _build_artifact(
                        goal,
                        inputs,
                        recorded,
                        outputs,
                        checkpoint,
                        configured_outcomes,
                    )
            safe_observation = self.evidence.redact(observation.model_dump(mode="json"))
            safe_observation["discovery_progress"] = {
                "completed_actions": [step.kind for step in recorded],
                "captured_outputs": sorted(outputs),
                "required_outputs": sorted(required),
            }
            decision = await self.provider.decide(goal, safe_observation)
            inferred_frame = _infer_single_interactive_frame(decision, safe_observation)
            if inferred_frame:
                logger.info(
                    "Discovery step %d: inferred target frame %s",
                    index + 1,
                    inferred_frame,
                )
            logger.info("Discovery step %d: model selected %s", index + 1, decision.kind)
            self.evidence.record("discovery_decision", index=index, decision=decision.model_dump())
            step_id = f"discovered-{index:02d}"
            if decision.kind == "complete":
                missing_outputs = sorted(required - outputs.keys())
                if missing_outputs:
                    logger.warning(
                        "Model declared completion before required outputs were captured (%s)",
                        ", ".join(missing_outputs),
                    )
                    self.evidence.record(
                        "discovery_premature_completion",
                        index=index,
                        missing_outputs=missing_outputs,
                    )
                    continue
                logger.info("Model declared completion; verifying checkpoint")
                if not await self.surface.condition_met(checkpoint):
                    raise RuntimeError("Model declared completion before the checkpoint was satisfied")
                logger.info("Discovery checkpoint verified")
                return _build_artifact(
                    goal,
                    inputs,
                    recorded,
                    outputs,
                    checkpoint,
                    configured_outcomes,
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
                    is_money = "balance" in decision.output.casefold()
                    step = ExtractStep(
                        id=step_id,
                        target=decision.target,
                        output=decision.output,
                        transform="usd" if is_money else "text",
                    )
                    self.policy.check_step(step, self.surface.url)
                    logger.info("Applying extract action (output=%s)", decision.output)
                    await self.surface.extract(step.target, step.timeout_ms)
                    outputs[decision.output] = OutputSpec(
                        type="money" if is_money else "string",
                        description=f"Discovered output {decision.output}",
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
