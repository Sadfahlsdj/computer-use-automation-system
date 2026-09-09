from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RiskLevel(StrEnum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"


class RoleLocator(StrictModel):
    kind: Literal["role"] = "role"
    role: str
    name: str
    exact: bool = True


class LabelLocator(StrictModel):
    kind: Literal["label"] = "label"
    value: str
    exact: bool = True


class TextLocator(StrictModel):
    kind: Literal["text"] = "text"
    value: str
    exact: bool = True


class CssLocator(StrictModel):
    kind: Literal["css"] = "css"
    selector: str


LocatorSpec = Annotated[
    RoleLocator | LabelLocator | TextLocator | CssLocator,
    Field(discriminator="kind"),
]


class TargetSpec(StrictModel):
    frame: str | None = None
    candidates: list[LocatorSpec]
    visible: bool = True
    unique: bool = True


class NavigateStep(StrictModel):
    kind: Literal["navigate"] = "navigate"
    id: str
    url: str
    risk: RiskLevel = RiskLevel.READ_ONLY
    timeout_ms: int = 10_000


class FillStep(StrictModel):
    kind: Literal["fill"] = "fill"
    id: str
    target: TargetSpec
    value: str
    sensitive: bool = False
    risk: RiskLevel = RiskLevel.REVERSIBLE
    timeout_ms: int = 5_000


class ClickStep(StrictModel):
    kind: Literal["click"] = "click"
    id: str
    target: TargetSpec
    risk: RiskLevel = RiskLevel.REVERSIBLE
    timeout_ms: int = 5_000
    retries: int = 0


class ExtractStep(StrictModel):
    kind: Literal["extract"] = "extract"
    id: str
    target: TargetSpec
    output: str
    transform: Literal["text", "usd"] = "text"
    risk: RiskLevel = RiskLevel.READ_ONLY
    timeout_ms: int = 5_000


Step = Annotated[
    NavigateStep | FillStep | ClickStep | ExtractStep,
    Field(discriminator="kind"),
]


class TextCondition(StrictModel):
    kind: Literal["text_visible"] = "text_visible"
    text: str
    frame: str | None = None


class UrlCondition(StrictModel):
    kind: Literal["url_matches"] = "url_matches"
    pattern: str


Condition = Annotated[TextCondition | UrlCondition, Field(discriminator="kind")]


class ParameterSpec(StrictModel):
    type: Literal["string", "integer", "boolean"]
    description: str
    pattern: str | None = None
    sensitive: bool = False


class OutputSpec(StrictModel):
    type: Literal["string", "number", "money"]
    description: str


class BusinessOutcomeSpec(StrictModel):
    code: str
    condition: Condition
    message: str


class CapabilityArtifact(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    capability_id: str
    capability_version: str
    title: str
    description: str
    risk: RiskLevel
    target_product: str
    inputs: dict[str, ParameterSpec]
    outputs: dict[str, OutputSpec]
    steps: list[Step]
    business_outcomes: list[BusinessOutcomeSpec] = []
    checkpoint: list[Condition]


class RunStatus(StrEnum):
    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    FAILURE = "failure"
    INTERVENTION_REQUIRED = "intervention_required"


class RunResult(StrictModel):
    status: RunStatus
    outputs: dict[str, Any] = Field(default_factory=dict)
    code: str | None = None
    message: str
    step_id: str | None = None
    category: str | None = None
    expected: Any | None = None
    observed: Any | None = None
    retryable: bool = False
    evidence_id: str


class PolicyConfig(StrictModel):
    allowed_origins: list[str]
    allowed_path_prefixes: list[str]
    allowed_actions: set[str]
    require_confirmation_for: set[RiskLevel] = {RiskLevel.IRREVERSIBLE}
    max_steps: int = 30
    max_retries_per_step: int = 2


class Observation(StrictModel):
    url: str
    title: str
    text: str
    interactive_elements: list[dict[str, Any]]
    screenshot_path: str | None = None


class DiscoveryDecision(StrictModel):
    kind: Literal["navigate", "fill", "click", "extract", "complete", "escalate"]
    reason: str
    target: TargetSpec | None = None
    value: str | None = None
    url: str | None = None
    output: str | None = None
