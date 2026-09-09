from types import SimpleNamespace

import pytest

from computer_use.app import handoff_state
from computer_use.handoff import ControlOwner, HandoffManager, HandoffSession


class FakeKeyboard:
    def __init__(self) -> None:
        self.typed: list[str] = []
        self.pressed: list[str] = []

    async def type(self, text: str) -> None:
        self.typed.append(text)

    async def press(self, key: str) -> None:
        self.pressed.append(key)


async def test_human_keyboard_input_and_control_lease() -> None:
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
    result = await handoff_state("expired-session")

    assert result["id"] == ""
    assert result["owner"] == "closed"
    assert result["events"] == []
