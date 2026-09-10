from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import typer

from computer_use.cli import _artifact_output_path, _discover, _openrouter_model_id
from computer_use.discovery import OpenRouterDecisionProvider


def test_openrouter_provider_uses_compatible_api_endpoint() -> None:
    """Verify the adapter targets OpenRouter and disables hidden SDK retries."""
    provider = OpenRouterDecisionProvider("openrouter/free", "test-key")

    assert provider.model == "openrouter/free"
    assert str(provider.client.base_url) == "https://openrouter.ai/api/v1/"
    assert provider.client.max_retries == 0


def test_openrouter_model_id_combines_provider_and_model() -> None:
    """Verify separate provider and model inputs form one OpenRouter identifier."""
    assert _openrouter_model_id("nex-agi", "nex-n2.5-pro:free") == (
        "nex-agi/nex-n2.5-pro:free"
    )


def test_openrouter_model_id_accepts_fully_qualified_model() -> None:
    """Verify an already qualified model ID is returned unchanged."""
    assert _openrouter_model_id("ignored", "openrouter/free") == "openrouter/free"


def test_artifact_output_path_separates_success_and_business_runs() -> None:
    """Verify output names preserve distinct success and business-outcome runs."""
    requested = Path("evidence/capabilities/discovered.yaml")

    assert _artifact_output_path(requested, "timestamp-a", None) == Path(
        "evidence/capabilities/discovered-success-timestamp-a.yaml"
    )
    assert _artifact_output_path(requested, "timestamp-b", "MEMBER_NOT_FOUND") == Path(
        "evidence/capabilities/discovered-business-member-not-found-timestamp-b.yaml"
    )


@pytest.mark.asyncio
async def test_openrouter_decision_uses_schema_and_corrects_invalid_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify invalid model JSON gets one schema-informed correction attempt."""
    caplog.set_level(logging.INFO, logger="computer_use.discovery")
    invalid = json.dumps(
        {
            "kind": "fill",
            "target_spec": {"role": "textbox", "name": "Member ID"},
            "text": "10002",
        }
    )
    valid = json.dumps({"kind": "complete", "reason": "The requested values are visible"})
    create = AsyncMock(
        side_effect=[
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=invalid))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=valid))]),
        ]
    )
    provider = OpenRouterDecisionProvider("nex-agi/nex-n2.5-pro:free", "test-key")
    provider.client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    decision = await provider.decide("Look up a member", {})

    assert decision.kind == "complete"
    assert create.await_count == 2
    first_request = create.await_args_list[0].kwargs
    assert first_request["response_format"]["type"] == "json_schema"
    assert first_request["response_format"]["json_schema"]["strict"] is True
    assert first_request["response_format"]["json_schema"]["schema"][
        "additionalProperties"
    ] is False
    assert first_request["extra_body"] == {"provider": {"require_parameters": True}}
    retry_messages = create.await_args_list[1].kwargs["messages"]
    assert "target_spec" in retry_messages[-1]["content"]
    assert "Waiting for OpenRouter response" in caplog.text
    assert "OpenRouter responded in" in caplog.text
    assert "requesting one correction" in caplog.text


@pytest.mark.asyncio
async def test_openrouter_decision_reports_invalid_response_after_retry() -> None:
    """Verify repeated invalid model responses produce a clear terminal error."""
    invalid = json.dumps({"kind": "fill", "text": "10002"})
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=invalid))]
    )
    create = AsyncMock(return_value=response)
    provider = OpenRouterDecisionProvider("nex-agi/nex-n2.5-pro:free", "test-key")
    provider.client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    with pytest.raises(RuntimeError, match="invalid discovery decision after 2 attempts"):
        await provider.decide("Look up a member", {})

    assert create.await_count == 2


@pytest.mark.asyncio
async def test_openrouter_infers_missing_extract_output_from_target(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify extraction output can be inferred from a matching target name."""
    raw = json.dumps(
        {
            "kind": "extract",
            "reason": "Read the current savings balance",
            "target": {
                "frame": "legacy-main",
                "candidates": [{"kind": "css", "selector": "#savings-balance"}],
            },
        }
    )
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=raw))]
        )
    )
    caplog.set_level(logging.INFO, logger="computer_use.discovery")
    provider = OpenRouterDecisionProvider("nex-agi/nex-n2.5-pro:free", "test-key")
    provider.client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    decision = await provider.decide(
        "Read member balance and status",
        {
            "discovery_progress": {
                "captured_outputs": [],
                "required_outputs": ["balance", "member_status"],
            }
        },
    )

    assert decision.output == "balance"
    assert create.await_count == 1
    assert "Inferred missing extract output" in caplog.text


@pytest.mark.asyncio
async def test_openrouter_infers_only_remaining_extract_output() -> None:
    """Verify the sole uncaptured required output is repaired automatically."""
    raw = json.dumps(
        {
            "kind": "extract",
            "reason": "Read the other requested value",
            "target": {
                "frame": "legacy-main",
                "candidates": [{"kind": "css", "selector": "#member-status"}],
            },
        }
    )
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=raw))]
        )
    )
    provider = OpenRouterDecisionProvider("nex-agi/nex-n2.5-pro:free", "test-key")
    provider.client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    decision = await provider.decide(
        "Read member balance and status",
        {
            "discovery_progress": {
                "captured_outputs": ["balance"],
                "required_outputs": ["balance", "member_status"],
            }
        },
    )

    assert decision.output == "member_status"
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_openrouter_infers_missing_role_locator_kind(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify an omitted role-locator discriminator is inferred from its fields."""
    raw = json.dumps(
        {
            "kind": "click",
            "reason": "Submit the member search",
            "target": {
                "frame": "legacy-main",
                "candidates": [{"role": "button", "name": "Search Records"}],
            },
        }
    )
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=raw))]
        )
    )
    caplog.set_level(logging.INFO, logger="computer_use.discovery")
    provider = OpenRouterDecisionProvider("nex-agi/nex-n2.5-pro:free", "test-key")
    provider.client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    decision = await provider.decide("Look up a member", {})

    assert decision.target is not None
    assert decision.target.candidates[0].kind == "role"
    assert create.await_count == 1
    assert "Inferred missing locator kind" in caplog.text


@pytest.mark.asyncio
async def test_openrouter_infers_missing_css_locator_kind() -> None:
    """Verify an omitted CSS-locator discriminator is inferred from its selector."""
    raw = json.dumps(
        {
            "kind": "click",
            "reason": "Open the member",
            "target": {
                "frame": "legacy-main",
                "candidates": [{"selector": "a[href*='member']"}],
            },
        }
    )
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=raw))]
        )
    )
    provider = OpenRouterDecisionProvider("nex-agi/nex-n2.5-pro:free", "test-key")
    provider.client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    decision = await provider.decide("Look up a member", {})

    assert decision.target is not None
    assert decision.target.candidates[0].kind == "css"
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_openrouter_request_has_timeout_and_heartbeat(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify slow provider calls emit heartbeats and stop at the request timeout."""
    async def slow_response(**kwargs: object) -> object:
        """Delay longer than the configured timeout and otherwise return a placeholder."""
        await asyncio.sleep(1)
        return object()

    caplog.set_level(logging.INFO, logger="computer_use.discovery")
    create = AsyncMock(side_effect=slow_response)
    provider = OpenRouterDecisionProvider(
        "nex-agi/nex-n2.5-pro:free",
        "test-key",
        request_timeout_seconds=0.03,
        heartbeat_seconds=0.01,
    )
    provider.client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    with pytest.raises(RuntimeError, match="OpenRouter did not respond within"):
        await provider.decide("Look up a member", {})

    assert "Still waiting for OpenRouter" in caplog.text


@pytest.mark.asyncio
async def test_discovery_requires_openrouter_key_before_browser_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify discovery rejects a missing API key before launching a browser."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(typer.BadParameter, match="OPENROUTER_API_KEY"):
        await _discover(
            goal="test",
            inputs={},
            output=None,  # type: ignore[arg-type]
            provider="nex-agi",
            model="nex-n2.5-pro:free",
            api_key=None,
            headed=False,
        )
