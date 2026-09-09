from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Protocol

from openai import AsyncOpenAI

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


class DecisionProvider(Protocol):
    async def decide(self, goal: str, observation: dict[str, Any]) -> DiscoveryDecision: ...


SYSTEM_PROMPT = """You operate a synthetic back-office web application.
Return one JSON action at a time. Allowed kinds: navigate, fill, click, extract, complete, escalate.
For target actions, include a robust TargetSpec using role/name or label first and CSS only as fallback.
Use the frame name shown in the observation. Never invent credentials or bypass policy.
The goal is complete only after requested values have been extracted and the success state is visible.
Return only JSON matching the provided schema."""


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
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = response.choices[0].message.content
        if not raw:
            raise RuntimeError("The model returned an empty decision")
        return DiscoveryDecision.model_validate_json(raw)


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
        await self.surface.navigate(start_url, initial.timeout_ms)
        recorded.append(initial)
        for index in range(self.policy.config.max_steps):
            screenshot = self.evidence.run_dir / f"discovery-{index:02d}.png"
            observation = await self.surface.observe(screenshot)
            safe_observation = self.evidence.redact(observation.model_dump(mode="json"))
            decision = await self.provider.decide(goal, safe_observation)
            self.evidence.record("discovery_decision", index=index, decision=decision.model_dump())
            step_id = f"discovered-{index:02d}"
            if decision.kind == "complete":
                checkpoint = TextCondition(
                    kind="text_visible", text=checkpoint_text, frame=checkpoint_frame
                )
                if not await self.surface.condition_met(checkpoint):
                    raise RuntimeError("Model declared completion before the checkpoint was satisfied")
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
            if decision.kind == "navigate" and decision.url:
                step = NavigateStep(id=step_id, url=decision.url)
                self.policy.check_step(step, self.surface.url)
                await self.surface.navigate(step.url, step.timeout_ms)
            elif decision.kind == "fill" and decision.target and decision.value is not None:
                value = decision.value
                template = value
                for name, supplied in inputs.items():
                    if value == supplied:
                        template = f"{{{{ inputs.{name} }}}}"
                step = FillStep(id=step_id, target=decision.target, value=template, sensitive=True)
                self.policy.check_step(step, self.surface.url)
                await self.surface.fill(step.target, value, step.timeout_ms)
            elif decision.kind == "click" and decision.target:
                step = ClickStep(id=step_id, target=decision.target)
                self.policy.check_step(step, self.surface.url)
                await self.surface.click(step.target, step.timeout_ms)
            elif decision.kind == "extract" and decision.target and decision.output:
                step = ExtractStep(id=step_id, target=decision.target, output=decision.output)
                self.policy.check_step(step, self.surface.url)
                await self.surface.extract(step.target, step.timeout_ms)
                outputs[decision.output] = OutputSpec(
                    type="string", description=f"Discovered output {decision.output}"
                )
            else:
                raise RuntimeError(f"Incomplete model decision: {decision.model_dump()}")
            recorded.append(step)
        raise RuntimeError("Discovery reached the maximum step count")
