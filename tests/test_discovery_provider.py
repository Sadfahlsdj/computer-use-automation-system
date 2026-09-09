from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import typer

from computer_use.cli import _discover, _openrouter_model_id
from computer_use.discovery import OpenRouterDecisionProvider


def test_openrouter_provider_uses_compatible_api_endpoint() -> None:
    provider = OpenRouterDecisionProvider("openrouter/free", "test-key")

    assert provider.model == "openrouter/free"
    assert str(provider.client.base_url) == "https://openrouter.ai/api/v1/"


def test_openrouter_model_id_combines_provider_and_model() -> None:
    assert _openrouter_model_id("nex-agi", "nex-n2.5-pro:free") == (
        "nex-agi/nex-n2.5-pro:free"
    )


def test_openrouter_model_id_accepts_fully_qualified_model() -> None:
    assert _openrouter_model_id("ignored", "openrouter/free") == "openrouter/free"


@pytest.mark.asyncio
async def test_openrouter_decision_uses_schema_and_corrects_invalid_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
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
async def test_discovery_requires_openrouter_key_before_browser_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
