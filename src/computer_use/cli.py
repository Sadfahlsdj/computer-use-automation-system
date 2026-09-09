from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import typer
import uvicorn
from playwright.async_api import async_playwright

from computer_use.artifacts import load_artifact, save_artifact
from computer_use.browser import launch_browser
from computer_use.discovery import DiscoveryEngine, OpenRouterDecisionProvider
from computer_use.evidence import EvidenceRecorder
from computer_use.models import PolicyConfig
from computer_use.policy import PolicyEngine
from computer_use.replay import ReplayEngine
from computer_use.surface import PlaywrightSurface

app = typer.Typer(no_args_is_help=True)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DISCOVERY_MODEL = "nex-agi/nex-n2.5-pro:free"


def _parse_inputs(values: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise typer.BadParameter("Inputs must use NAME=VALUE")
        name, supplied = value.split("=", 1)
        parsed[name] = supplied
    return parsed


def _policy() -> PolicyEngine:
    import yaml

    data = yaml.safe_load((PROJECT_ROOT / "config" / "policy.yaml").read_text())
    return PolicyEngine(PolicyConfig.model_validate(data))


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Serve the target application, APIs, and operator UI."""
    uvicorn.run("computer_use.app:app", host=host, port=port, reload=reload)


async def _replay(artifact_path: Path, inputs: dict[str, str], headed: bool) -> None:
    artifact = load_artifact(artifact_path)
    evidence = EvidenceRecorder(PROJECT_ROOT / "evidence", list(inputs.values()))
    async with async_playwright() as playwright:
        browser = await launch_browser(playwright, headless=not headed)
        context = await browser.new_context()
        await context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = await context.new_page()
        engine = ReplayEngine(PlaywrightSurface(page), _policy(), evidence)
        result = await engine.run(artifact, inputs)
        await page.screenshot(path=str(evidence.run_dir / "final.png"), full_page=True)
        await context.tracing.stop(path=str(evidence.run_dir / "trace.zip"))
        await browser.close()
    typer.echo(json.dumps(result.model_dump(mode="json"), indent=2))


@app.command()
def replay(
    artifact: Path = typer.Argument(..., exists=True, dir_okay=False),
    input: list[str] = typer.Option([], "--input", "-i"),
    headed: bool = typer.Option(False, "--headed"),
) -> None:
    """Replay a capability without an LLM in the decision loop."""
    asyncio.run(_replay(artifact.resolve(), _parse_inputs(input), headed))


async def _discover(
    goal: str,
    inputs: dict[str, str],
    output: Path,
    provider: str,
    model: str,
    api_key: str | None,
    headed: bool,
) -> None:
    if provider.casefold() != "openrouter":
        raise typer.BadParameter(
            f"Unsupported discovery provider {provider!r}; the supported provider is 'openrouter'",
            param_hint="--provider",
        )
    resolved_api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not resolved_api_key:
        raise typer.BadParameter(
            "Set OPENROUTER_API_KEY or pass --api-key for a genuine discovery run",
            param_hint="--api-key",
        )
    evidence = EvidenceRecorder(PROJECT_ROOT / "evidence", list(inputs.values()))
    async with async_playwright() as playwright:
        browser = await launch_browser(playwright, headless=not headed)
        context = await browser.new_context()
        await context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = await context.new_page()
        engine = DiscoveryEngine(
            PlaywrightSurface(page),
            _policy(),
            evidence,
            OpenRouterDecisionProvider(model, resolved_api_key),
        )
        artifact = await engine.run(
            goal,
            inputs,
            "http://127.0.0.1:8000/demo",
            "Savings Account",
            "legacy-main",
        )
        save_artifact(artifact, output)
        await context.tracing.stop(path=str(evidence.run_dir / "trace.zip"))
        await browser.close()
    typer.echo(f"Saved discovered capability to {output}")


@app.command()
def discover(
    goal: str = typer.Option(..., "--goal"),
    input: list[str] = typer.Option([], "--input", "-i"),
    output: Path = typer.Option(Path("evidence/capabilities/discovered.yaml"), "--output"),
    provider: str = typer.Option("openrouter", "--provider"),
    model: str = typer.Option(DEFAULT_DISCOVERY_MODEL, "--model"),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        envvar="OPENROUTER_API_KEY",
        help="OpenRouter API key (prefer the OPENROUTER_API_KEY environment variable)",
    ),
    headed: bool = typer.Option(False, "--headed"),
) -> None:
    """Run genuine LLM-guided discovery and save the resulting artifact."""
    asyncio.run(
        _discover(
            goal,
            _parse_inputs(input),
            output.resolve(),
            provider,
            model,
            api_key,
            headed,
        )
    )


if __name__ == "__main__":
    app()
