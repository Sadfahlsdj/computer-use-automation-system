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
    def url(self) -> str:
        """Return the surface's current top-level location."""
        ...

    async def navigate(self, url: str, timeout_ms: int) -> None:
        """Navigate to ``url`` within ``timeout_ms`` milliseconds."""
        ...

    async def fill(self, target: TargetSpec, value: str, timeout_ms: int) -> None:
        """Replace the contents of ``target`` with ``value`` within the timeout."""
        ...

    async def click(self, target: TargetSpec, timeout_ms: int) -> None:
        """Activate ``target`` within ``timeout_ms`` milliseconds."""
        ...

    async def extract(self, target: TargetSpec, timeout_ms: int) -> str:
        """Return normalized visible text read from ``target`` within the timeout."""
        ...

    async def condition_met(self, condition: Condition) -> bool:
        """Return whether the typed ``condition`` currently holds on the surface."""
        ...

    async def observe(self, screenshot_path: Path | None = None) -> Observation:
        """Return structured surface state and optionally write a screenshot."""
        ...


class PlaywrightSurface:
    def __init__(self, page: Page) -> None:
        """Adapt a live Playwright ``page`` to the surface-neutral execution contract."""
        self.page = page

    @property
    def url(self) -> str:
        """Return the Playwright page's current top-level URL."""
        return self.page.url

    async def _scope(self, frame_name: str | None, timeout_ms: int) -> Page | Frame:
        """Return the page or named frame, waiting up to ``timeout_ms`` for attachment."""
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
        """Translate one ordered target candidate into a Playwright locator."""
        candidate = target.candidates[index]
        if candidate.kind == "role":
            return scope.get_by_role(candidate.role, name=candidate.name, exact=candidate.exact)
        if candidate.kind == "label":
            return scope.get_by_label(candidate.value, exact=candidate.exact)
        if candidate.kind == "text":
            return scope.get_by_text(candidate.value, exact=candidate.exact)
        return scope.locator(candidate.selector)

    async def resolve(self, target: TargetSpec, timeout_ms: int) -> Locator:
        """Return the first visible, unique locator that satisfies ``target``."""
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
        """Load ``url`` and wait for DOM content within ``timeout_ms``."""
        await self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    async def fill(self, target: TargetSpec, value: str, timeout_ms: int) -> None:
        """Resolve ``target`` and replace its value with ``value``."""
        locator = await self.resolve(target, timeout_ms)
        await locator.fill(value, timeout=timeout_ms)

    async def click(self, target: TargetSpec, timeout_ms: int) -> None:
        """Resolve and click ``target``, then briefly yield for legacy navigation."""
        locator = await self.resolve(target, timeout_ms)
        await locator.click(timeout=timeout_ms)
        # A click can start an iframe navigation after Playwright considers the
        # locator action complete. Give that navigation a chance to commit before
        # discovery captures the next observation.
        await self.page.wait_for_timeout(250)

    async def extract(self, target: TargetSpec, timeout_ms: int) -> str:
        """Resolve ``target`` and return its stripped inner text."""
        locator = await self.resolve(target, timeout_ms)
        return (await locator.inner_text(timeout=timeout_ms)).strip()

    async def condition_met(self, condition: Condition) -> bool:
        """Return whether a text or URL checkpoint is currently satisfied."""
        if condition.kind == "url_matches":
            return re.search(condition.pattern, self.url) is not None
        try:
            scope = await self._scope(condition.frame, 1_000)
        except TargetNotFound:
            return False
        return await scope.get_by_text(condition.text, exact=False).count() > 0

    async def observe(self, screenshot_path: Path | None = None) -> Observation:
        """Collect URL, title, text, controls, extractable nodes, and optional screenshot."""
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
        extractable = await self.page.locator("[id], h1, h2, h3, th, td").evaluate_all(
            """els => els.filter(e => e.offsetParent !== null && e.innerText?.trim())
                .slice(0, 150).map((e, i) => ({
                    ref: `top-value-${i}`,
                    tag: e.tagName.toLowerCase(),
                    id: e.id || null,
                    text: e.innerText.trim().slice(0, 500)
                }))"""
        )
        frame_elements: list[dict[str, object]] = []
        frame_extractable: list[dict[str, object]] = []
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
                values = await frame.locator("[id], h1, h2, h3, th, td").evaluate_all(
                    """els => els.filter(e => e.offsetParent !== null && e.innerText?.trim())
                        .slice(0, 150).map((e, i) => ({
                            ref: `frame-value-${i}`,
                            tag: e.tagName.toLowerCase(),
                            id: e.id || null,
                            text: e.innerText.trim().slice(0, 500)
                        }))"""
                )
                for value in values:
                    value["frame"] = frame.name
                frame_extractable.extend(values)
                frame_text.append(f"[frame={frame.name}]\n{(await frame.locator('body').inner_text())[:6000]}")
            except Exception:
                continue
        return Observation(
            url=self.url,
            title=await self.page.title(),
            text=(await self.page.locator("body").inner_text())[:8_000] + "\n" + "\n".join(frame_text),
            interactive_elements=[*elements, *frame_elements],
            extractable_elements=[*extractable, *frame_extractable],
            screenshot_path=str(screenshot_path) if screenshot_path else None,
        )
