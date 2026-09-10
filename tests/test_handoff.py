import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from computer_use.app import handoff_state
from computer_use.evidence import EvidenceRecorder
from computer_use.handoff import ControlOwner, HandoffManager, HandoffSession


class FakeKeyboard:
    def __init__(self) -> None:
        """Initialize captured typed text and special-key presses."""
        self.typed: list[str] = []
        self.pressed: list[str] = []

    async def type(self, text: str) -> None:
        """Capture ``text`` instead of sending it to a browser."""
        self.typed.append(text)

    async def press(self, key: str) -> None:
        """Capture a synthetic special-key press."""
        self.pressed.append(key)


async def test_human_keyboard_input_and_control_lease() -> None:
    """Verify keyboard commands require and preserve the exclusive human lease."""
    keyboard = FakeKeyboard()
    page = SimpleNamespace(keyboard=keyboard)
    session = HandoffSession(
        id="test-session",
        context=SimpleNamespace(),  # type: ignore[arg-type]
        page=page,  # type: ignore[arg-type]
        owner=ControlOwner.HUMAN,
        reason="test",
    )
    manager = HandoffManager()
    manager.sessions[session.id] = session

    await manager.type_text(session.id, "A")
    await manager.press_key(session.id, "Backspace")

    assert keyboard.typed == ["A"]
    assert keyboard.pressed == ["Backspace"]
    assert [event["action"] for event in session.events] == ["type", "key"]

    await manager.resume(session.id)
    with pytest.raises(PermissionError, match="does not hold"):
        await manager.type_text(session.id, "blocked")


async def test_rejects_unsupported_operator_key() -> None:
    """Verify the operator cannot forward keys outside the explicit allowlist."""
    session = HandoffSession(
        id="test-session",
        context=SimpleNamespace(),  # type: ignore[arg-type]
        page=SimpleNamespace(keyboard=FakeKeyboard()),  # type: ignore[arg-type]
        owner=ControlOwner.HUMAN,
        reason="test",
    )
    manager = HandoffManager()
    manager.sessions[session.id] = session

    with pytest.raises(ValueError, match="Unsupported operator key"):
        await manager.press_key(session.id, "Meta+R")


async def test_unknown_session_returns_polling_tombstone() -> None:
    """Verify stale polling receives a closed session that stops future requests."""
    result = await handoff_state("expired-session")

    assert result["id"] == ""
    assert result["owner"] == "closed"
    assert result["events"] == []


async def test_executor_session_pauses_and_records_shared_evidence(tmp_path: Path) -> None:
    """Verify cede/resume events share the executor's redacted evidence stream."""
    manager = HandoffManager()
    evidence = EvidenceRecorder(tmp_path, ["10001"])
    session = manager.register_intervention(
        context=SimpleNamespace(),  # type: ignore[arg-type]
        page=SimpleNamespace(),  # type: ignore[arg-type]
        reason="Step requires confirmation",
        capability_id="member.open-account",
        goal="Open a new account for member 10001",
        current_step="create-account",
        kind="approval",
        evidence=evidence,
    )

    waiter = asyncio.create_task(manager.wait_for_resume(session.id, 1))
    assert not waiter.done()
    await manager.resume(session.id)
    await waiter

    assert session.owner == ControlOwner.AUTOMATION
    assert session.capability_id == "member.open-account"
    assert session.current_step == "create-account"
    log = evidence.log_path.read_text()
    assert "ceded_control" in log
    assert "returned_control" in log
    assert "10001" not in log
