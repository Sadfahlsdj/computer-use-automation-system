import asyncio

import pytest

from computer_use.errors import TargetNotFound
from computer_use.surface import PlaywrightSurface


class DelayedFramePage:
    url = "http://127.0.0.1:8000/demo"

    def __init__(self, available_after: int) -> None:
        self.available_after = available_after
        self.calls = 0
        self.expected_frame = object()

    def frame(self, *, name: str):
        self.calls += 1
        if name == "legacy-main" and self.calls >= self.available_after:
            return self.expected_frame
        return None


async def test_scope_waits_for_delayed_frame() -> None:
    page = DelayedFramePage(available_after=3)
    surface = PlaywrightSurface(page)  # type: ignore[arg-type]

    frame = await surface._scope("legacy-main", timeout_ms=250)

    assert frame is page.expected_frame
    assert page.calls >= 3


async def test_scope_times_out_when_frame_never_attaches() -> None:
    surface = PlaywrightSurface(DelayedFramePage(available_after=10_000))  # type: ignore[arg-type]

    with pytest.raises(TargetNotFound, match="after 20 ms"):
        await surface._scope("missing", timeout_ms=20)

    await asyncio.sleep(0)
