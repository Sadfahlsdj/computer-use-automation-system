from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from computer_use.browser import launch_browser
from computer_use.models import (
    ClickStep,
    FillStep,
    LabelLocator,
    RoleLocator,
    TargetSpec,
)
from computer_use.policy import PolicyEngine, default_policy
from computer_use.surface import PlaywrightSurface


class ControlOwner(StrEnum):
    AUTOMATION = "automation"
    HUMAN = "human"
    CLOSED = "closed"


@dataclass
class HandoffSession:
    id: str
    context: BrowserContext
    page: Page
    owner: ControlOwner
    reason: str
    events: list[dict[str, Any]] = field(default_factory=list)

    def record(self, actor: str, action: str, **details: Any) -> None:
        self.events.append({"actor": actor, "action": action, **details})


class HandoffManager:
    ALLOWED_KEYS = {
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "ArrowUp",
        "Backspace",
        "Delete",
        "End",
        "Enter",
        "Escape",
        "Home",
        "Tab",
    }

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self.sessions: dict[str, HandoffSession] = {}

    async def _ensure_browser(self) -> Browser:
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await launch_browser(self._playwright, headless=True)
        return self._browser

    async def create_demo_handoff(self) -> HandoffSession:
        browser = await self._ensure_browser()
        context = await browser.new_context(viewport={"width": 1100, "height": 760})
        page = await context.new_page()
        surface = PlaywrightSurface(page)
        policy: PolicyEngine = default_policy()
        await surface.navigate("http://127.0.0.1:8000/demo", 10_000)
        fill = FillStep(
            id="operator-demo-member",
            target=TargetSpec(
                frame="legacy-main",
                candidates=[LabelLocator(kind="label", value="Member Number")],
            ),
            value="10001",
            sensitive=True,
        )
        click_search = ClickStep(
            id="operator-demo-search",
            target=TargetSpec(
                frame="legacy-main",
                candidates=[RoleLocator(kind="role", role="button", name="Search Records")],
            ),
        )
        click_member = ClickStep(
            id="operator-demo-open",
            target=TargetSpec(
                frame="legacy-main",
                candidates=[RoleLocator(kind="role", role="link", name="Open Member")],
            ),
        )
        for step in (fill, click_search, click_member):
            policy.check_step(step, surface.url)
            if step.kind == "fill":
                await surface.fill(step.target, step.value, step.timeout_ms)
            else:
                await surface.click(step.target, step.timeout_ms)
        await surface.click(
            TargetSpec(
                frame="legacy-main",
                candidates=[RoleLocator(kind="role", role="link", name="Open New Sub-Account")],
            ),
            5_000,
        )
        await surface.click(
            TargetSpec(
                frame="legacy-main",
                candidates=[RoleLocator(kind="role", role="button", name="Review")],
            ),
            5_000,
        )
        session = HandoffSession(
            id=uuid.uuid4().hex[:10],
            context=context,
            page=page,
            owner=ControlOwner.HUMAN,
            reason="Irreversible Create Account action requires human review",
        )
        session.record("automation", "ceded_control", reason=session.reason)
        self.sessions[session.id] = session
        return session

    def get(self, session_id: str) -> HandoffSession:
        if session_id not in self.sessions:
            raise KeyError(session_id)
        return self.sessions[session_id]

    async def state(self, session_id: str) -> dict[str, Any]:
        session = self.get(session_id)
        image = await session.page.screenshot()
        return {
            "id": session.id,
            "owner": session.owner,
            "reason": session.reason,
            "url": session.page.url,
            "screenshot": base64.b64encode(image).decode(),
            "events": session.events[-20:],
        }

    async def click(self, session_id: str, x: float, y: float) -> None:
        session = self.get(session_id)
        self._require_human(session)
        await session.page.mouse.click(x, y)
        session.record("human", "click", x=x, y=y)

    async def type_text(self, session_id: str, text: str) -> None:
        session = self.get(session_id)
        self._require_human(session)
        await session.page.keyboard.type(text)
        session.record("human", "type", text="[REDACTED]")

    async def press_key(self, session_id: str, key: str) -> None:
        session = self.get(session_id)
        self._require_human(session)
        if key not in self.ALLOWED_KEYS:
            raise ValueError(f"Unsupported operator key: {key}")
        await session.page.keyboard.press(key)
        session.record("human", "key", key=key)

    async def resume(self, session_id: str) -> None:
        session = self.get(session_id)
        self._require_human(session)
        session.owner = ControlOwner.AUTOMATION
        session.record("human", "returned_control")

    async def close(self, session_id: str) -> None:
        session = self.get(session_id)
        await session.context.close()
        session.owner = ControlOwner.CLOSED
        session.record("system", "closed")

    @staticmethod
    def _require_human(session: HandoffSession) -> None:
        if session.owner != ControlOwner.HUMAN:
            raise PermissionError(f"Human does not hold the control lease; owner={session.owner}")

    async def shutdown(self) -> None:
        for session in self.sessions.values():
            if session.owner != ControlOwner.CLOSED:
                await session.context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
