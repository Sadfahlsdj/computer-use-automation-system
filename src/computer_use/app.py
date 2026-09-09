from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from computer_use.handoff import HandoffManager
from computer_use.target_app import router as target_router

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UI_DIST = PROJECT_ROOT / "operator-ui" / "dist"

handoffs = HandoffManager()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await handoffs.shutdown()


app = FastAPI(title="Computer-Use Automation", version="0.1.0", lifespan=lifespan)
app.include_router(target_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def root() -> Response:
    index = UI_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return HTMLResponse(
        "<h1>Computer-Use Automation</h1>"
        "<p>Build the operator UI with <code>npm run build</code> in operator-ui.</p>"
        '<p><a href="/demo">Open the synthetic legacy application</a></p>'
    )


if UI_DIST.exists():
    app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")


class PointerCommand(BaseModel):
    x: float
    y: float


class TypeCommand(BaseModel):
    text: str


class KeyCommand(BaseModel):
    key: str


@app.post("/api/handoffs")
async def start_handoff() -> dict[str, str]:
    session = await handoffs.create_demo_handoff()
    return {"id": session.id, "owner": session.owner}


@app.get("/api/handoffs/{session_id}")
async def handoff_state(session_id: str) -> dict[str, object]:
    try:
        return await handoffs.state(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Unknown session") from error


@app.post("/api/handoffs/{session_id}/click")
async def handoff_click(session_id: str, command: PointerCommand) -> dict[str, str]:
    try:
        await handoffs.click(session_id, command.x, command.y)
        return {"status": "ok"}
    except (KeyError, PermissionError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/handoffs/{session_id}/type")
async def handoff_type(session_id: str, command: TypeCommand) -> dict[str, str]:
    try:
        await handoffs.type_text(session_id, command.text)
        return {"status": "ok"}
    except (KeyError, PermissionError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/handoffs/{session_id}/key")
async def handoff_key(session_id: str, command: KeyCommand) -> dict[str, str]:
    try:
        await handoffs.press_key(session_id, command.key)
        return {"status": "ok"}
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Unknown session") from error
    except PermissionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/handoffs/{session_id}/resume")
async def handoff_resume(session_id: str) -> dict[str, str]:
    try:
        await handoffs.resume(session_id)
        return {"status": "ok", "owner": "automation"}
    except (KeyError, PermissionError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
