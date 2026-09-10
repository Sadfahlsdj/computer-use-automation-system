from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path

import typer
import uvicorn
from dotenv import load_dotenv
from playwright.async_api import async_playwright

from computer_use.app import handoffs
from computer_use.artifacts import load_artifact, save_artifact
from computer_use.browser import launch_browser
from computer_use.discovery import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DiscoveryEngine,
    OpenRouterDecisionProvider,
)
from computer_use.evidence import EvidenceRecorder
from computer_use.intervention import CliInterventionHandler, OperatorBridge
from computer_use.models import BusinessOutcomeSpec, PolicyConfig, TextCondition
from computer_use.policy import PolicyEngine
from computer_use.replay import ReplayEngine
from computer_use.surface import PlaywrightSurface

app = typer.Typer(no_args_is_help=True)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")
DEFAULT_DISCOVERY_PROVIDER = "nex-agi"
DEFAULT_DISCOVERY_MODEL = "nex-n2.5-pro:free"
DEFAULT_HANDOFF_TIMEOUT_SECONDS = 600.0


def _configure_discovery_logging() -> None:
    project_logger = logging.getLogger("computer_use")
    if not any(getattr(handler, "_cua_discovery_handler", False) for handler in project_logger.handlers):
        handler = logging.StreamHandler()
        handler._cua_discovery_handler = True  # type: ignore[attr-defined]
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        project_logger.addHandler(handler)
    project_logger.setLevel(logging.INFO)
    project_logger.propagate = False


def _parse_inputs(values: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise typer.BadParameter("Inputs must use NAME=VALUE")
        name, supplied = value.split("=", 1)
        parsed[name] = supplied
    return parsed


def _openrouter_model_id(provider: str, model: str) -> str:
    model = model.strip()
    if "/" in model:
        return model
    provider = provider.strip().strip("/")
    if not provider or not model:
        raise typer.BadParameter(
            "OpenRouter provider and model cannot be empty",
            param_hint="--provider/--model",
        )
    return f"{provider}/{model}"


def _policy() -> PolicyEngine:
    import yaml

    data = yaml.safe_load((PROJECT_ROOT / "config" / "policy.yaml").read_text())
    return PolicyEngine(PolicyConfig.model_validate(data))


def _demo_business_outcomes() -> list[BusinessOutcomeSpec]:
    return [
        BusinessOutcomeSpec(
            code="MEMBER_NOT_FOUND",
            condition=TextCondition(
                kind="text_visible",
                text="No member found",
                frame="legacy-main",
            ),
            message="No member exists for the supplied identifier.",
        ),
        BusinessOutcomeSpec(
            code="PERMISSION_DENIED",
            condition=TextCondition(
                kind="text_visible",
                text="Permission denied",
                frame="legacy-main",
            ),
            message="The current operator cannot access this member.",
        ),
        BusinessOutcomeSpec(
            code="SESSION_EXPIRED",
            condition=TextCondition(
                kind="text_visible",
                text="session has expired",
                frame="legacy-main",
            ),
            message="The target application session expired.",
        ),
    ]


def _artifact_output_path(
    requested: Path,
    evidence_id: str,
    business_outcome_code: str | None,
) -> Path:
    outcome = "success"
    if business_outcome_code:
        normalized = re.sub(r"[^a-z0-9]+", "-", business_outcome_code.casefold()).strip("-")
        outcome = f"business-{normalized}"
    suffix = requested.suffix if requested.suffix in {".yaml", ".yml", ".json"} else ".yaml"
    parent = requested.parent if requested.suffix else requested
    stem = requested.stem if requested.suffix else "discovered"
    return parent / f"{stem}-{outcome}-{evidence_id}{suffix}"


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Serve the target application, APIs, and operator UI."""
    uvicorn.run("computer_use.app:app", host=host, port=port, reload=reload)


async def _replay(
    artifact_path: Path,
    inputs: dict[str, str],
    headed: bool,
    handoff_port: int,
    handoff_timeout_seconds: float,
) -> None:
    artifact = load_artifact(artifact_path)
    evidence = EvidenceRecorder(PROJECT_ROOT / "evidence", list(inputs.values()))
    bridge = OperatorBridge(port=handoff_port)
    async with async_playwright() as playwright:
        browser = await launch_browser(playwright, headless=not headed)
        context = await browser.new_context()
        await context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = await context.new_page()
        interventions = CliInterventionHandler(
            handoffs,
            bridge,
            context,
            page,
            evidence,
            handoff_timeout_seconds,
        )
        try:
            engine = ReplayEngine(PlaywrightSurface(page), _policy(), evidence, interventions)
            result = await engine.run(artifact, inputs)
            await page.screenshot(path=str(evidence.run_dir / "final.png"), full_page=True)
        finally:
            await bridge.stop()
            await context.tracing.stop(path=str(evidence.run_dir / "trace.zip"))
            await browser.close()
    typer.echo(json.dumps(result.model_dump(mode="json"), indent=2))


@app.command()
def replay(
    artifact: Path = typer.Argument(..., exists=True, dir_okay=False),
    input: list[str] = typer.Option([], "--input", "-i"),
    headed: bool = typer.Option(False, "--headed"),
    handoff_port: int = typer.Option(8001, "--handoff-port", min=1, max=65535),
    handoff_timeout: float = typer.Option(
        DEFAULT_HANDOFF_TIMEOUT_SECONDS,
        "--handoff-timeout",
        min=1,
        help="Seconds to wait for an operator to return control",
    ),
) -> None:
    """Replay a capability without an LLM in the decision loop."""
    _configure_discovery_logging()
    asyncio.run(
        _replay(
            artifact.resolve(),
            _parse_inputs(input),
            headed,
            handoff_port,
            handoff_timeout,
        )
    )


async def _discover(
    goal: str,
    inputs: dict[str, str],
    output: Path,
    provider: str,
    model: str,
    api_key: str | None,
    headed: bool,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    handoff_port: int = 8001,
    handoff_timeout_seconds: float = DEFAULT_HANDOFF_TIMEOUT_SECONDS,
) -> Path:
    resolved_api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not resolved_api_key:
        raise typer.BadParameter(
            "Set OPENROUTER_API_KEY or pass --api-key for a genuine discovery run",
            param_hint="--api-key",
        )
    evidence = EvidenceRecorder(PROJECT_ROOT / "evidence", list(inputs.values()))
    model_id = _openrouter_model_id(provider, model)
    logging.getLogger(__name__).info(
        "Starting discovery (model=%s, headed=%s, request_timeout=%.0fs, evidence=%s)",
        model_id,
        headed,
        request_timeout_seconds,
        evidence.run_dir,
    )
    saved_path: Path | None = None
    bridge = OperatorBridge(port=handoff_port)
    async with async_playwright() as playwright:
        logging.getLogger(__name__).info("Launching Playwright browser")
        browser = await launch_browser(playwright, headless=not headed)
        context = await browser.new_context()
        await context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = await context.new_page()
        try:
            engine = DiscoveryEngine(
                PlaywrightSurface(page),
                _policy(),
                evidence,
                OpenRouterDecisionProvider(
                    model_id,
                    resolved_api_key,
                    request_timeout_seconds=request_timeout_seconds,
                ),
                CliInterventionHandler(
                    handoffs,
                    bridge,
                    context,
                    page,
                    evidence,
                    handoff_timeout_seconds,
                ),
            )
            artifact = await engine.run(
                goal,
                inputs,
                "http://127.0.0.1:8000/demo",
                "Savings Account",
                "legacy-main",
                _demo_business_outcomes(),
                {"balance", "member_status"},
            )
            saved_path = _artifact_output_path(
                output,
                evidence.evidence_id,
                engine.business_outcome_code,
            )
            logging.getLogger(__name__).info("Saving discovered capability to %s", saved_path)
            save_artifact(artifact, saved_path)
            evidence.record(
                "discovery_artifact_saved",
                path=str(saved_path),
                outcome=engine.business_outcome_code or "SUCCESS",
            )
        except Exception as error:
            evidence.record(
                "discovery_failed",
                category=type(error).__name__,
                error=str(error),
            )
            logging.getLogger(__name__).error(
                "Discovery failed (%s); details recorded in %s",
                type(error).__name__,
                evidence.log_path,
            )
            raise
        finally:
            await bridge.stop()
            await context.tracing.stop(path=str(evidence.run_dir / "trace.zip"))
            await browser.close()
    logging.getLogger(__name__).info("Discovery finished successfully")
    if saved_path is None:
        raise RuntimeError("Discovery completed without saving an artifact")
    typer.echo(f"Saved discovered capability to {saved_path}")
    return saved_path


@app.command()
def discover(
    goal: str = typer.Option(..., "--goal"),
    input: list[str] = typer.Option([], "--input", "-i"),
    output: Path = typer.Option(
        Path("evidence/capabilities/discovered.yaml"),
        "--output",
        help="Artifact filename base; outcome and timestamp are appended",
    ),
    provider: str = typer.Option(
        DEFAULT_DISCOVERY_PROVIDER,
        "--provider",
        envvar="OPENROUTER_PROVIDER",
        help="Provider/author namespace in the OpenRouter model ID",
    ),
    model: str = typer.Option(
        DEFAULT_DISCOVERY_MODEL,
        "--model",
        envvar="OPENROUTER_MODEL",
        help="Model slug, or a fully qualified provider/model ID",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        envvar="OPENROUTER_API_KEY",
        help="OpenRouter API key (prefer the OPENROUTER_API_KEY environment variable)",
    ),
    headed: bool = typer.Option(False, "--headed"),
    request_timeout: float = typer.Option(
        DEFAULT_REQUEST_TIMEOUT_SECONDS,
        "--request-timeout",
        envvar="OPENROUTER_REQUEST_TIMEOUT_SECONDS",
        min=1,
        help="Maximum seconds to wait for each OpenRouter response",
    ),
    handoff_port: int = typer.Option(8001, "--handoff-port", min=1, max=65535),
    handoff_timeout: float = typer.Option(
        DEFAULT_HANDOFF_TIMEOUT_SECONDS,
        "--handoff-timeout",
        min=1,
        help="Seconds to wait for an operator to return control",
    ),
) -> None:
    """Run genuine LLM-guided discovery and save the resulting artifact."""
    _configure_discovery_logging()
    asyncio.run(
        _discover(
            goal,
            _parse_inputs(input),
            output.resolve(),
            provider,
            model,
            api_key,
            headed,
            request_timeout,
            handoff_port,
            handoff_timeout,
        )
    )


if __name__ == "__main__":
    app()
