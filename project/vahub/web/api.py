"""REST + WebSocket + a minimal live status page.

Everything here sits behind the proxy's auth in prod. In the dev sandbox there
is no proxy; the compose file publishes the port on 127.0.0.1 only. Both the
WebSocket and the state-changing REST routes check an Origin allowlist, because
same-origin does not stop a cross-origin browser from *sending* a POST or
opening a socket. There is still no authentication here: that is the proxy's job
(M2), and the dev tool-call endpoint stays off unless explicitly enabled.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from ..core import metrics

if TYPE_CHECKING:
    from ..core.runtime import Runtime

_STATIC = Path(__file__).parent / "static"


class DevCall(BaseModel):
    module: str = Field(max_length=128)
    tool: str = Field(max_length=128)
    args: dict = {}
    timeout_s: float = Field(10.0, gt=0, le=60)


class ChatTurn(BaseModel):
    message: str = Field(max_length=8000)
    session_id: str | None = Field(default=None, max_length=64)


def _reject_bad_origin(request: Request, allow: list[str]) -> None:
    """Same-origin does not protect state-changing POSTs from a cross-origin
    browser. Reject a present-but-disallowed Origin (a non-browser client sends
    none and is allowed, matching the WebSocket rule)."""
    origin = request.headers.get("origin")
    if origin is not None and origin not in allow:
        raise HTTPException(status_code=403, detail="bad origin")


def _module_view(mod) -> dict:
    m = mod.manifest
    return {
        "name": m.name,
        "version": m.version,
        "state": mod.state.value,
        "last_error": mod.last_error,
        "health": mod.health,
        "tools": [t.get("name") for t in mod.tools],
        "restarts": mod.restarts,
    }


def create_app(rt: "Runtime") -> FastAPI:
    app = FastAPI(title="vahub", version="0.1.0")

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/api/client-config")
    async def client_config() -> JSONResponse:
        # Lets the browser decide the voice path: mock STT/TTS -> use the browser
        # Web Speech API; openai_compat -> record and POST to /api/voice.
        return JSONResponse({"stt_provider": rt.config.stt.provider, "tts_provider": rt.config.tts.provider})

    @app.get("/api/modules")
    async def modules() -> JSONResponse:
        return JSONResponse([_module_view(m) for m in rt.supervisor.modules.values()])

    @app.get("/api/modules/{name}/logs")
    async def module_logs(name: str) -> JSONResponse:
        mod = rt.supervisor.modules.get(name)
        if mod is None:
            raise HTTPException(status_code=404, detail="unknown module")
        return JSONResponse({"module": name, "lines": list(mod.stderr_ring)})

    @app.get("/api/tools")
    async def tools() -> JSONResponse:
        return JSONResponse(rt.registry.list_tools())

    @app.post("/api/dev/call")
    async def dev_call(call: DevCall, request: Request) -> JSONResponse:
        # Deliberately gated: this bypasses the (future) policy gate and the
        # agent. It exists to validate the module contract in M1. Remove once
        # the gate (M3) and agent (M4) are the only callers.
        if not rt.config.web.dev_tools_endpoint:
            raise HTTPException(status_code=403, detail="dev tools endpoint disabled")
        _reject_bad_origin(request, rt.config.web.origin_allowlist)
        result = await rt.moduleapi.call(
            module=call.module, tool=call.tool, args=call.args, timeout_s=call.timeout_s, principal="dev"
        )
        return JSONResponse(result)

    @app.post("/api/chat")
    async def chat(turn: ChatTurn, request: Request) -> JSONResponse:
        _reject_bad_origin(request, rt.config.web.origin_allowlist)
        session = rt.sessions.get_or_create(turn.session_id)
        result = await rt.agent.run_turn(session, turn.message)
        rt.sessions.trim(session)
        return JSONResponse(result)

    @app.post("/api/voice")
    async def voice(
        request: Request,
        audio: UploadFile = File(...),
        session_id: str | None = Form(default=None),
    ) -> JSONResponse:
        # Server-side voice path (used when stt/tts provider is openai_compat).
        # With the default mock STT there is no server model, so we tell the
        # client to fall back to the browser's Web Speech API.
        _reject_bad_origin(request, rt.config.web.origin_allowlist)
        data = await audio.read()
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="audio too large")
        transcript = await rt.stt.transcribe(data, audio.content_type or "audio/webm")
        if not transcript:
            return JSONResponse({"ok": False, "error": "no_server_stt",
                                 "hint": "server STT is mock; the browser uses Web Speech instead"})
        session = rt.sessions.get_or_create(session_id)
        result = await rt.agent.run_turn(session, transcript)
        rt.sessions.trim(session)
        payload: dict = {"transcript": transcript, **result}
        audio_out = await rt.tts.synthesize(result.get("reply") or "")
        if audio_out is not None:
            blob, mime = audio_out
            payload["audio"] = base64.b64encode(blob).decode("ascii")
            payload["audio_mime"] = mime
        return JSONResponse(payload)

    @app.get("/api/pending")
    async def pending() -> JSONResponse:
        return JSONResponse(await rt.store.list_pending())

    @app.post("/api/confirm/{pending_id}")
    async def confirm(pending_id: str, request: Request) -> JSONResponse:
        # Out-of-band confirmation of a destructive call. The frozen arguments
        # are executed, not whatever the model may have changed since.
        _reject_bad_origin(request, rt.config.web.origin_allowlist)
        subject = request.headers.get("x-auth-subject")  # set by the proxy in prod
        result = await rt.moduleapi.confirm(pending_id, subject=subject)
        return JSONResponse(result)

    @app.get("/api/audit")
    async def audit() -> JSONResponse:
        return JSONResponse(await rt.store.recent_tool_calls(limit=200))

    @app.get("/api/schedules")
    async def schedules() -> JSONResponse:
        return JSONResponse(rt.scheduler.list_schedules())

    @app.post("/api/schedules/{schedule_id}/run")
    async def run_schedule(schedule_id: str, request: Request) -> JSONResponse:
        # Manual trigger for testing a routine without waiting for its cron time.
        if not rt.config.web.dev_tools_endpoint:
            raise HTTPException(status_code=403, detail="dev tools endpoint disabled")
        _reject_bad_origin(request, rt.config.web.origin_allowlist)
        return JSONResponse(await rt.scheduler.run_schedule(schedule_id))

    @app.get("/metrics")
    async def prometheus() -> Response:
        body, content_type = metrics.render()
        return Response(content=body, media_type=content_type)

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        origin = ws.headers.get("origin")
        allow = rt.config.web.origin_allowlist
        # Browsers always send Origin. A missing Origin here means a non-browser
        # client (curl, a script); we allow it since the sandbox is loopback-only.
        if origin is not None and origin not in allow:
            await ws.close(code=1008)
            return
        await ws.accept()
        subs: list = []
        try:
            # Subscribe inside the try so a failed snapshot send still unsubscribes.
            topics = [
                ("module.state_changed", "state"),
                ("module.log", "log"),
                ("policy.confirmation_required", "confirm"),
                ("schedule.fired", "schedule"),
            ]
            subs = [(rt.bus.subscribe(topic), kind) for topic, kind in topics]
            await ws.send_json(
                {"type": "snapshot", "modules": [_module_view(m) for m in rt.supervisor.modules.values()]}
            )
            # Merge every subscription into one queue so a single consumer does
            # every ws.send_json (Starlette sends are not safe to call concurrently).
            merged: asyncio.Queue = asyncio.Queue(maxsize=1024)

            async def feed(sub, kind):
                async for ev in sub.events():
                    await merged.put((kind, ev))

            feeders = [asyncio.create_task(feed(sub, kind)) for sub, kind in subs]
            try:
                while True:
                    kind, ev = await merged.get()
                    await ws.send_json({"type": kind, "data": ev})
            finally:
                for f in feeders:
                    f.cancel()
                for f in feeders:
                    try:
                        await f
                    except (asyncio.CancelledError, Exception):
                        pass
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            for sub, _ in subs:
                rt.bus.unsubscribe(sub)

    @app.get("/")
    async def index() -> HTMLResponse:
        return HTMLResponse((_STATIC / "index.html").read_text())

    return app


__all__ = ["create_app"]
