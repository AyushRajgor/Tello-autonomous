"""
Dashboard Server

FastAPI + WebSocket server for:
- Live video feed from drone
- Chat interface (text commands)
- Telemetry display (battery, position, heading)
- Trace viewer

Chat routing has two modes:
- Live mode (default): text is sent to a persistent Gemini Live API session.
  Audio replies play locally on the host machine's speakers (Aoede voice).
  Transcriptions are pushed to the browser.
- Legacy mode (--legacy-agent): goes through core.agent.TelloAgent (slower).
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import DASHBOARD
from core.toolkit import TelloToolkit
from core.agent import TelloAgent

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Injected by main.py ──────────────────────────────────────────────────────
agent: Optional[TelloAgent] = None
toolkit: Optional[TelloToolkit] = None
tracer = None
live_agent = None   # voice.gemini_live.LiveAgent if use_live
use_live: bool = True

# Host-side speaker for Live API audio output
_speaker = None

clients: list[WebSocket] = []


async def broadcast(message: dict):
    """Send message to all connected dashboard clients."""
    data = json.dumps(message)
    disconnected = []
    for ws in clients:
        try:
            await ws.send_text(data)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        if ws in clients:
            clients.remove(ws)


async def _wire_live_callbacks():
    """Hook Live session events up to the dashboard broadcast channel."""
    if live_agent is None:
        return

    async def on_audio(pcm_chunk: bytes):
        if _speaker is not None:
            _speaker.play(pcm_chunk)

    async def on_text(role: str, text: str):
        await broadcast({"type": "chat", "role": role, "text": text})

    async def on_tool(name, args, result):
        status = "ok" if (isinstance(result, dict) and result.get("success", True)) else "error"
        await broadcast({
            "type": "tool",
            "name": name,
            "args": args,
            "result": result,
            "status": status,
        })
        if toolkit:
            await broadcast({"type": "state", "data": toolkit.state.to_dict()})

    def on_interrupt():
        if _speaker is not None:
            _speaker.flush()

    live_agent.on_audio(on_audio)
    live_agent.on_text(on_text)
    live_agent.on_tool(on_tool)
    live_agent.on_interrupt(on_interrupt)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    global _speaker

    if use_live and live_agent is not None:
        try:
            from voice.audio import SpeakerPlayer
            _speaker = SpeakerPlayer()
            _speaker.start()
        except Exception as e:
            logger.warning(f"Could not start host speaker (continuing without audio playback): {e}")
            _speaker = None

        await _wire_live_callbacks()
        try:
            await live_agent.start()
            logger.info("Live API session ready for dashboard")
        except Exception as e:
            logger.error(f"Failed to start Live API session: {e}")

    telemetry_task = asyncio.create_task(telemetry_loop())

    try:
        yield
    finally:
        telemetry_task.cancel()
        if live_agent is not None:
            try:
                await live_agent.stop()
            except Exception:
                pass
        if _speaker is not None:
            try:
                _speaker.stop()
            except Exception:
                pass


app = FastAPI(title="TelloAI Dashboard", lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.append(ws)
    logger.info(f"Dashboard client connected ({len(clients)} total)")

    try:
        if toolkit:
            await ws.send_text(json.dumps({
                "type": "state",
                "data": toolkit.state.to_dict(),
            }))
            await ws.send_text(json.dumps({
                "type": "mode",
                "mode": "live" if use_live else "legacy",
            }))

        while True:
            # Accept both text (JSON) and binary (PCM audio) frames
            raw = await ws.receive()
            if raw.get("type") == "websocket.disconnect":
                break

            # Binary frame -> push to Live session as audio
            if "bytes" in raw and raw["bytes"] is not None:
                if use_live and live_agent is not None:
                    asyncio.create_task(live_agent.send_audio(raw["bytes"]))
                continue

            text = raw.get("text")
            if not text:
                continue

            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type")

            if mtype == "command":
                user_text = (msg.get("text") or "").strip()
                if not user_text:
                    continue
                asyncio.create_task(_handle_command(user_text))

            elif mtype == "audio_start":
                # Clearly mark a new mic session in logs; no action needed —
                # Live API auto-detects turn boundaries via VAD.
                logger.info("Mic ON (browser push-to-talk)")

            elif mtype == "audio_end":
                logger.info("Mic OFF")

            elif mtype == "get_frame" and toolkit:
                frame = toolkit.get_frame()
                if frame.get("success"):
                    await ws.send_text(json.dumps({
                        "type": "frame",
                        "image_base64": frame["image_base64"],
                    }))

    except WebSocketDisconnect:
        if ws in clients:
            clients.remove(ws)
        logger.info(f"Dashboard client disconnected ({len(clients)} total)")


async def _handle_command(user_text: str):
    """Route chat command through either Live API or legacy agent."""
    import time
    start = time.time()

    if use_live and live_agent is not None:
        # Live path: user message is broadcast first, responses come back async
        # via transcription callback.
        await broadcast({"type": "chat", "role": "user", "text": user_text})
        await live_agent.send_text(user_text)
        logger.info(f"Live command sent: {user_text[:60]}")
        # State will be broadcast by on_tool callbacks as they fire
        return

    # Legacy path
    if agent is None:
        await broadcast({"type": "chat", "role": "system", "text": "No agent configured."})
        return

    await broadcast({"type": "chat", "role": "user", "text": user_text})
    response = await agent.process_message(user_text)
    elapsed = time.time() - start
    logger.info(f"Legacy command processed in {elapsed:.2f}s | {response[:60]}")
    await broadcast({"type": "chat", "role": "assistant", "text": response})

    if tracer and tracer.completed_traces:
        latest = tracer.get_recent_traces(1)
        if latest:
            await broadcast({"type": "trace", "data": latest[0]})

    if toolkit:
        await broadcast({"type": "state", "data": toolkit.state.to_dict()})


@app.get("/")
async def get_dashboard():
    """Serve the dashboard HTML."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/video-status")
async def video_status():
    """Check if drone stream or webcam is providing frames."""
    if toolkit is None:
        return {"active": False}
    if toolkit.use_webcam and toolkit._webcam is not None:
        return {"active": True, "source": "webcam"}
    if not toolkit.simulate and toolkit.state.is_streaming:
        return {"active": True, "source": "drone"}
    return {"active": False}


# Legacy alias
@app.get("/webcam-status")
async def webcam_status():
    return await video_status()


@app.get("/api/traces")
async def get_traces(limit: int = 50):
    if tracer:
        return {"traces": tracer.get_recent_traces(limit)}
    return {"traces": []}


@app.get("/api/traces/{trace_id}")
async def get_trace(trace_id: str):
    if tracer:
        trace = tracer.get_trace(trace_id)
        if trace:
            return trace
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Trace not found")


async def telemetry_loop():
    """Periodically broadcast drone telemetry to dashboard."""
    while True:
        if toolkit and clients:
            toolkit.get_battery()
            await broadcast({
                "type": "state",
                "data": toolkit.state.to_dict(),
            })
        await asyncio.sleep(2)
