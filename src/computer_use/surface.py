from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Protocol

from playwright.async_api import Frame, Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from computer_use.errors import AmbiguousTarget, TargetNotFound
from computer_use.models import Condition, Observation, TargetSpec


class Surface(Protocol):
    @property
    def url(self) -> str: ...

    async def navigate(self, url: str, timeout_ms: int) -> None: ...

    async def fill(self, target: TargetSpec, value: str, timeout_ms: int) -> None: ...

    async def click(self, target: TargetSpec, timeout_ms: int) -> None: ...

    async def extract(self, target: TargetSpec, timeout_ms: int) -> str: ...

    async def condition_met(self, condition: Condition) -> bool: ...

    async def observe(self, screenshot_path: Path | None = None) -> Observation: ...


class PlaywrightSurface:
    def __init__(self, page: Page) -> None:
        self.page = page

    @property
    def url(self) -> str:
        return self.page.url

    async def _scope(self, frame_name: str | None, timeout_ms: int) -> Page | Frame:
        if frame_name is None:
            return self.page
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_ms / 1000
        while loop.time() < deadline:
            frame = self.page.frame(name=frame_name)
            if frame is not None:
                return frame
            await asyncio.sleep(0.025)
        raise TargetNotFound(f"Frame not found after {timeout_ms} ms: {frame_name}")

    def _candidate(self, scope: Page | Frame, target: TargetSpec, index: int) -> Locator:
        candidate = target.candidates[index]
        if candidate.kind == "role":
            return scope.get_by_role(candidate.role, name=candidate.name, exact=candidate.exact)
        if candidate.kind == "label":
            return scope.get_by_label(candidate.value, exact=candidate.exact)
        if candidate.kind == "text":
            return scope.get_by_text(candidate.value, exact=candidate.exact)
        return scope.locator(candidate.selector)

    async def resolve(self, target: TargetSpec, timeout_ms: int) -> Locator:
        scope = await self._scope(target.frame, timeout_ms)
        errors: list[str] = []
        for index, candidate in enumerate(target.candidates):
            locator = self._candidate(scope, target, index)
            try:
                await locator.first.wait_for(state="visible" if target.visible else "attached", timeout=timeout_ms)
                count = await locator.count()
            except PlaywrightTimeout:
                errors.append(f"{candidate.kind}: timed out")
                continue
            if target.unique and count != 1:
                if count > 1:
                    errors.append(f"{candidate.kind}: matched {count} elements")
                continue
            return locator.first
        if any("matched" in error for error in errors):
            raise AmbiguousTarget("; ".join(errors))
        raise TargetNotFound("; ".join(errors) or "No target candidates")

    async def navigate(self, url: str, timeout_ms: int) -> None:
        await self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    async def fill(self, target: TargetSpec, value: str, timeout_ms: int) -> None:
        locator = await self.resolve(target, timeout_ms)
        await locator.fill(value, timeout=timeout_ms)

    async def click(self, target: TargetSpec, timeout_ms: int) -> None:
        locator = await self.resolve(target, timeout_ms)
        await locator.click(timeout=timeout_ms)
        # A click can start an iframe navigation after Playwright considers the
        # locator action complete. Give that navigation a chance to commit before
        # discovery captures the next observation.
        await self.page.wait_for_timeout(250)

    async def extract(self, target: TargetSpec, timeout_ms: int) -> str:
        locator = await self.resolve(target, timeout_ms)
        return (await locator.inner_text(timeout=timeout_ms)).strip()

    async def condition_met(self, condition: Condition) -> bool:
        if condition.kind == "url_matches":
            return re.search(condition.pattern, self.url) is not None
        try:
            scope = await self._scope(condition.frame, 1_000)
        except TargetNotFound:
            return False
        return await scope.get_by_text(condition.text, exact=False).count() > 0

    async def observe(self, screenshot_path: Path | None = None) -> Observation:
        if screenshot_path:
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            await self.page.screenshot(path=str(screenshot_path), full_page=True)
        elements = await self.page.locator("button, input, select, textarea, a").evaluate_all(
            """els => els.filter(e => e.offsetParent !== null).slice(0, 100).map((e, i) => ({
                ref: `top-${i}`,
                tag: e.tagName.toLowerCase(),
                role: e.getAttribute('role'),
                name: e.getAttribute('aria-label') || e.labels?.[0]?.innerText?.trim() ||
                      e.innerText || e.placeholder || '',
                id: e.id || null,
                html_name: e.getAttribute('name'),
                type: e.getAttribute('type')
            }))"""
        )
        frame_elements: list[dict[str, object]] = []
        frame_text: list[str] = []
        for frame in self.page.frames:
            if frame == self.page.main_frame:
                continue
            try:
                items = await frame.locator("button, input, select, textarea, a").evaluate_all(
                    """els => els.filter(e => e.offsetParent !== null).slice(0, 100).map((e, i) => ({
                        ref: `frame-${i}`,
                        tag: e.tagName.toLowerCase(),
                        role: e.getAttribute('role'),
                        name: e.getAttribute('aria-label') || e.labels?.[0]?.innerText?.trim() ||
                              e.innerText || e.placeholder || '',
                        id: e.id || null,
                        html_name: e.getAttribute('name'),
                        type: e.getAttribute('type')
                    }))"""
                )
                for item in items:
                    item["frame"] = frame.name
                frame_elements.extend(items)
                frame_text.append(f"[frame={frame.name}]\n{(await frame.locator('body').inner_text())[:6000]}")
            except Exception:
                continue
        return Observation(
            url=self.url,
            title=await self.page.title(),
            text=(await self.page.locator("body").inner_text())[:8_000] + "\n" + "\n".join(frame_text),
            interactive_elements=[*elements, *frame_elements],
            screenshot_path=str(screenshot_path) if screenshot_path else None,
        )
