from __future__ import annotations

import asyncio
import logging
from typing import Literal, Protocol

import uvicorn
from playwright.async_api import BrowserContext, Page

from computer_use.evidence import EvidenceRecorder
from computer_use.handoff import HandoffManager

logger = logging.getLogger(__name__)


class InterventionHandler(Protocol):
    async def request(
        self,
        *,
        reason: str,
        capability_id: str | None,
        goal: str | None,
        current_step: str | None,
        kind: Literal["approval", "manual_recovery"],
    ) -> None:
        """Pause for an operator using the supplied run context, then return on resume."""
        ...


class OperatorBridge:
    """Lazily serves the operator UI in the same process as a CLI-owned page."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8001) -> None:
        """Configure the local operator bridge address without starting the server."""
        self.host = host
        self.port = port
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the operator UI/API if needed and return once its socket is ready."""
        if self._task is not None:
            return
        from computer_use.app import app

        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self._task = asyncio.create_task(self._server.serve())
        for _ in range(100):
            if self._server.started:
                return
            if self._task.done():
                await self._task
                raise RuntimeError("Operator bridge stopped before it became ready")
            await asyncio.sleep(0.05)
        raise RuntimeError(f"Operator bridge did not start on http://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop a running operator bridge and release its local listening socket."""
        if self._server is None or self._task is None:
            return
        self._server.should_exit = True
        await self._task
        self._server = None
        self._task = None


class CliInterventionHandler:
    def __init__(
        self,
        manager: HandoffManager,
        bridge: OperatorBridge,
        context: BrowserContext,
        page: Page,
        evidence: EvidenceRecorder,
        timeout_seconds: float = 600.0,
    ) -> None:
        """Bind the live page, evidence recorder, bridge, manager, and wait timeout."""
        self.manager = manager
        self.bridge = bridge
        self.context = context
        self.page = page
        self.evidence = evidence
        self.timeout_seconds = timeout_seconds

    async def request(
        self,
        *,
        reason: str,
        capability_id: str | None,
        goal: str | None,
        current_step: str | None,
        kind: Literal["approval", "manual_recovery"],
    ) -> None:
        """Register a live intervention, publish its URL, and wait for operator resume."""
        session = self.manager.register_intervention(
            context=self.context,
            page=self.page,
            reason=reason,
            capability_id=capability_id,
            goal=goal,
            current_step=current_step,
            kind=kind,
            evidence=self.evidence,
        )
        await self.bridge.start()
        url = f"http://{self.bridge.host}:{self.bridge.port}/?session={session.id}"
        logger.warning("Human intervention required: %s", reason)
        logger.warning("Open the operator console: %s", url)
        logger.warning("Waiting up to %.0f seconds for control to be returned", self.timeout_seconds)
        await self.manager.wait_for_resume(session.id, self.timeout_seconds)
        logger.info("Operator returned control; resuming automation")
