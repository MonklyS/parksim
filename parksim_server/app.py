"""FastAPI application.

REST
  GET  /api/health
  GET  /api/catalog                  buildable templates for the editor palette
  GET  /api/examples/sample          a ready-made park to start from
  POST /api/validate                 check a layout without saving it
  POST /api/parks                    save a new park  -> {id, edit_token}
  GET  /api/parks/{id}               load a park (anyone with the link)
  PUT  /api/parks/{id}               update a park (needs X-Edit-Token)

WebSocket
  /api/live                          send {"type": "start", "layout": ..., "settings": ...},
                                     then receive frames; send commands any time
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from parksim import Park, ParkError, __version__
from parksim.catalog import META_KEYS, TEMPLATES

from .live import TICK_SECONDS, LiveSession
from .models import CreatedPark, ParkLayout, SimSettings, StoredPark, Validation
from .storage import ParkStore

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_PARK = ROOT / "examples" / "sample_park.json"
DEFAULT_STATIC = ROOT / "web" / "dist"


def _catalog() -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "label": t["label"],
            "category": t["category"],
            "description": t.get("description", ""),
            "size": t["size"],
            "build_cost": t.get("build_cost", 0),
            "stats": {k: v for k, v in t.items() if k not in META_KEYS},
        }
        for key, t in TEMPLATES.items()
    ]


def _check(layout: ParkLayout) -> Park:
    """Build the engine park, turning layout-rule violations into a 422."""
    try:
        return Park.from_dict(layout.to_engine())
    except ParkError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def create_app(
    db_path: str | Path | None = None, static_dir: Path | None = DEFAULT_STATIC
) -> FastAPI:
    store = ParkStore(db_path or os.environ.get("PARKSIM_DB", "parksim.db"))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        store.close()

    app = FastAPI(title="ParkSim API", version=__version__, lifespan=lifespan)
    app.state.store = store

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/catalog")
    def catalog() -> list[dict[str, Any]]:
        return _catalog()

    @app.get("/api/examples/sample")
    def sample() -> dict[str, Any]:
        return json.loads(SAMPLE_PARK.read_text(encoding="utf-8"))

    @app.post("/api/validate")
    def validate(layout: ParkLayout) -> Validation:
        try:
            park = Park.from_dict(layout.to_engine())
        except ParkError as exc:
            return Validation(ok=False, error=str(exc))
        return Validation(ok=True, warnings=park.warnings())

    @app.post("/api/parks", status_code=201)
    def create_park(layout: ParkLayout) -> CreatedPark:
        _check(layout)
        park_id, token = store.create(layout.to_engine())
        return CreatedPark(id=park_id, edit_token=token)

    @app.get("/api/parks/{park_id}")
    def get_park(park_id: str) -> StoredPark:
        record = store.get(park_id)
        if record is None:
            raise HTTPException(status_code=404, detail="park not found")
        warnings = Park.from_dict(record["layout"]).warnings()
        return StoredPark(warnings=warnings, **record)

    @app.put("/api/parks/{park_id}")
    def update_park(
        park_id: str,
        layout: ParkLayout,
        x_edit_token: Annotated[str | None, Header()] = None,
    ) -> StoredPark:
        park = _check(layout)
        if not x_edit_token:
            raise HTTPException(status_code=401, detail="missing X-Edit-Token header")
        try:
            stamp = store.update(park_id, x_edit_token, layout.to_engine())
        except KeyError:
            raise HTTPException(status_code=404, detail="park not found") from None
        except PermissionError:
            raise HTTPException(status_code=403, detail="edit token does not match") from None
        return StoredPark(
            id=park_id, layout=layout.to_engine(), warnings=park.warnings(), updated_at=stamp
        )

    @app.websocket("/api/live")
    async def live(ws: WebSocket) -> None:
        await ws.accept()
        try:
            start = await ws.receive_json()
            if not isinstance(start, dict) or start.get("type") != "start":
                raise ValueError("first message must be {'type': 'start', 'layout': ...}")
            layout = ParkLayout.model_validate(start.get("layout"))
            settings = SimSettings.model_validate(start.get("settings") or {})
            park = Park.from_dict(layout.to_engine())
        except WebSocketDisconnect:
            return
        except (ValidationError, ValueError) as exc:  # ParkError is a ValueError
            await ws.send_json({"type": "error", "message": str(exc)[:1000]})
            await ws.close(code=1008)
            return

        session = LiveSession(park, settings)
        await ws.send_json(session.init_message())
        receiver = asyncio.create_task(_receive_commands(ws, session))
        loop = asyncio.get_running_loop()
        last = loop.time()
        try:
            while not session.done and not receiver.done():
                now = loop.time()
                session.tick(now - last)
                last = now
                await ws.send_json(session.frame_message())
                await asyncio.sleep(TICK_SECONDS)
            if session.done:
                await ws.send_json(session.frame_message())
                await ws.send_json(session.report_message())
                await receiver  # stay open until the viewer leaves
        except WebSocketDisconnect:
            pass
        finally:
            receiver.cancel()

    if static_dir is not None and static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    return app


async def _receive_commands(ws: WebSocket, session: LiveSession) -> None:
    while True:
        try:
            message = await ws.receive_json()
        except WebSocketDisconnect:
            return
        except (ValueError, KeyError):  # malformed JSON / non-text frame
            session.errors.append("could not parse command")
            continue
        session.handle(message)
