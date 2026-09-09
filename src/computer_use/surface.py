from __future__ import annotations

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

    def _scope(self, frame_name: str | None) -> Page | Frame:
        if frame_name is None:
            return self.page
        frame = self.page.frame(name=frame_name)
        if frame is None:
            raise TargetNotFound(f"Frame not found: {frame_name}")
        return frame

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
        scope = self._scope(target.frame)
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

    async def extract(self, target: TargetSpec, timeout_ms: int) -> str:
        locator = await self.resolve(target, timeout_ms)
        return (await locator.inner_text(timeout=timeout_ms)).strip()

    async def condition_met(self, condition: Condition) -> bool:
        if condition.kind == "url_matches":
            return re.search(condition.pattern, self.url) is not None
        scope = self._scope(condition.frame)
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
                name: e.getAttribute('aria-label') || e.innerText || e.name || e.placeholder || '',
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
                        name: e.getAttribute('aria-label') || e.innerText || e.name || e.placeholder || '',
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
