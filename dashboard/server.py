"""
Dashboard Server

FastAPI + WebSocket server for:
- Live video feed from drone
- Chat interface (text commands)
- Direct operator controls (STOP / land / E-STOP / manual moves) that bypass the LLM
- Telemetry display (battery, position, heading, link + operator-hold status)
- Safety watchdog alerts
- Trace viewer

Chat routing has two modes:
- Live mode (default): text is sent to a persistent Gemini Live API session.
  Audio replies play locally on the host machine's speakers (Aoede voice).
  Transcriptions are pushed to the browser.
- Legacy mode (--legacy-agent): goes through core.agent.TelloAgent (slower).

Security:
- The WebSocket checks the browser Origin before accepting (Cross-Site
  WebSocket Hijacking: any web page you visit could otherwise open
  ws://localhost:8080/ws and fly the drone).
- If DASHBOARD["token"] is set, /ws and /api/* require a matching ?token=.

WebSocket protocol (browser -> server):
  {"type": "command", "text": str}                          chat to the AI
  {"type": "operator", "action": str, "args": {...}}        direct control, no AI
  {"type": "audio_start"} / {"type": "audio_end"}           push-to-talk
  {"type": "get_frame"}                                     request one camera frame
  <binary frame>                                            PCM16 mic audio (live mode)

Server -> browser:
  {"type": "state", "data": {...}}
  {"type": "mode", "mode": "live" | "legacy"}
  {"type": "chat", "role": str, "text": str}
  {"type": "tool", "name", "args", "result", "status"}
  {"type": "operator_result", "action": str, "args": {...}, "result": {...}}
  {"type": "safety", "level": "info"|"warning"|"critical", "code": str, "message": str}
  {"type": "trace", "data": {...}} / {"type": "metric", "name", "value"}
  {"type": "frame", "image_base64": str}
"""

import asyncio
import inspect
import ipaddress
import json
import logging
import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from config import DASHBOARD
from core.toolkit import TelloToolkit, parse_operator_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.agent import TelloAgent

logger = logging.getLogger(__name__)

STATIC_DIST_DIR = Path(__file__).parent / "static_dist"

BUILD_MISSING_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>TelloAI dashboard not built</title>
<style>body{font-family:system-ui,sans-serif;background:#111;color:#eee;max-width:640px;margin:4rem auto;padding:0 1rem}
code,pre{background:#222;padding:.2rem .4rem;border-radius:4px}pre{padding:1rem}</style></head>
<body><h1>Dashboard build not found</h1>
<p>The React dashboard has not been built yet (expected <code>dashboard/static_dist/index.html</code>).</p>
<p>Build it once, then reload this page:</p>
<pre>cd dashboard/frontend; npm install; npm run build</pre>
</body></html>
"""

# Actions the browser may request through {"type": "operator"}.
OPERATOR_ACTIONS = frozenset({
    "hover", "land", "emergency_stop", "resume", "takeoff", "move", "rotate", "flip",
})
# STOP / E-STOP / Resume never block (a raw UDP send at most), so they get a
# pool nothing else uses and can never be starved — not even by repeated Land
# presses, which wait on the drone (and are single-flight in the toolkit).
_INSTANT_ACTIONS = frozenset({"hover", "emergency_stop", "resume"})
_INSTANT_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="operator-instant")
_LAND_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="operator-land")

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# Per-connection backlog of mic chunks before we start dropping (~10s of audio).
_MAX_QUEUED_LIVE_INPUTS = 500
_VITE_DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")

# Injected by main.py ──────────────────────────────────────────────────────
agent: Optional["TelloAgent"] = None
toolkit: Optional[TelloToolkit] = None
tracer = None
watchdog = None     # core.watchdog.SafetyWatchdog
live_agent = None   # voice.gemini_live.LiveAgent if use_live
use_live: bool = True

# Host-side speaker for Live API audio output
_speaker = None

clients: list[WebSocket] = []

# Strong references to fire-and-forget tasks (asyncio only keeps weak ones).
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


# ── Access control ─────────────────────────────────────────────────────────

def _host_origin(host: str, port: int) -> str:
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}".lower()


def allowed_origins() -> set[str]:
    """Browser origins allowed to open the dashboard WebSocket."""
    port = DASHBOARD["port"]
    host = str(DASHBOARD["host"])
    origins = {
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
        f"http://[::1]:{port}",
        *_VITE_DEV_ORIGINS,
    }
    origins.update(o.strip().rstrip("/").lower() for o in DASHBOARD.get("extra_origins") or [] if o.strip())
    if host not in _LOOPBACK_HOSTS:
        origins.add(_host_origin(host, port))
    return origins


def _is_ip_literal_same_origin(origin: str, host_header: Optional[str]) -> bool:
    """
    True when the page was served by us at an IP-literal address (e.g. a phone
    on the LAN opening http://192.168.1.20:8080). IP literals can't be DNS
    rebound, so Origin == Host is a safe same-origin proof for them.
    """
    if not host_header:
        return False
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        ipaddress.ip_address(parts.hostname)
    except ValueError:
        return False
    return parts.netloc.lower() == host_header.strip().lower()


def origin_allowed(origin: Optional[str], host_header: Optional[str] = None) -> bool:
    """Cross-Site WebSocket Hijacking guard. A missing Origin = non-browser client."""
    if origin is None:
        return True
    normalized = origin.strip().rstrip("/").lower()
    if normalized in allowed_origins():
        return True
    if str(DASHBOARD["host"]) not in _LOOPBACK_HOSTS:
        return _is_ip_literal_same_origin(normalized, host_header)
    return False


def token_ok(supplied: Optional[str]) -> bool:
    """Shared-secret check (only enforced when DASHBOARD["token"] is set)."""
    expected = DASHBOARD.get("token") or ""
    if not expected:
        return True
    if not supplied:
        return False
    return secrets.compare_digest(supplied.encode("utf-8"), str(expected).encode("utf-8"))


async def require_api_token(request: Request):
    if not token_ok(request.query_params.get("token")):
        raise HTTPException(status_code=401, detail="Missing or invalid dashboard token")


# ── Broadcast helpers ──────────────────────────────────────────────────────

async def broadcast(message: dict):
    """Send message to all connected dashboard clients."""
    data = json.dumps(message, default=str)
    disconnected = []
    for ws in list(clients):
        try:
            await ws.send_text(data)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        if ws in clients:
            clients.remove(ws)


async def broadcast_state():
    if toolkit is not None:
        await broadcast({"type": "state", "data": toolkit.state.to_dict()})


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
        await broadcast_state()

    def on_interrupt():
        if _speaker is not None:
            _speaker.flush()

    async def on_metric(name, value):
        await broadcast({"type": "metric", "name": name, "value": value})

    live_agent.on_audio(on_audio)
    live_agent.on_text(on_text)
    live_agent.on_tool(on_tool)
    live_agent.on_interrupt(on_interrupt)
    live_agent.on_metric(on_metric)


# ── Safety watchdog → browser ──────────────────────────────────────────────

async def _handle_safety_event(event: dict):
    level = str(event.get("level", "info"))
    code = str(event.get("code", "safety"))
    message = str(event.get("message", ""))
    await broadcast({"type": "safety", "level": level, "code": code, "message": message})
    await broadcast_state()

    # Let the voice model know, so it can tell the user and stop planning moves.
    if level in ("warning", "critical") and live_agent is not None and hasattr(live_agent, "send_system_notice"):
        try:
            # send_system_notice adds its own [SAFETY NOTICE] framing.
            await live_agent.send_system_notice(message)
        except Exception as e:
            logger.warning(f"Could not forward safety notice to Live session: {e}")


class _WatchdogBridge:
    """Thread-safe adapter: watchdog thread → dashboard event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.active = True

    def __call__(self, event: dict):
        # Runs on the watchdog thread.
        if not self.active or self.loop.is_closed():
            return
        try:
            self.loop.call_soon_threadsafe(self._schedule, dict(event))
        except RuntimeError:
            pass  # loop shutting down

    def _schedule(self, event: dict):
        # Runs on the event loop thread.
        if self.active:
            _spawn(_handle_safety_event(event))


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

    bridge: Optional[_WatchdogBridge] = None
    if watchdog is not None:
        bridge = _WatchdogBridge(asyncio.get_running_loop())
        watchdog.add_listener(bridge)

    telemetry_task = asyncio.create_task(telemetry_loop())

    try:
        yield
    finally:
        telemetry_task.cancel()
        if bridge is not None:
            bridge.active = False
            remove = getattr(watchdog, "remove_listener", None)
            if callable(remove):
                try:
                    remove(bridge)
                except Exception:
                    pass
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


class _OptionalStaticFiles(StaticFiles):
    """StaticFiles that 404s (instead of crashing) while the build is missing."""

    async def __call__(self, scope, receive, send):
        if not os.path.isdir(str(self.directory)):
            response = PlainTextResponse(
                "Dashboard build not found. Run: cd dashboard/frontend; npm install; npm run build",
                status_code=404,
            )
            await response(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


app = FastAPI(title="TelloAI Dashboard", lifespan=_lifespan)
app.mount("/static", _OptionalStaticFiles(directory=str(STATIC_DIST_DIR), check_dir=False), name="static")


# ── WebSocket ──────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    origin = ws.headers.get("origin")
    if not origin_allowed(origin, ws.headers.get("host")):
        logger.warning(f"Rejected dashboard WebSocket from disallowed Origin: {origin!r}")
        await ws.close(code=1008)
        return
    if not token_ok(ws.query_params.get("token")):
        logger.warning("Rejected dashboard WebSocket: missing or invalid token")
        await ws.close(code=1008)
        return

    await ws.accept()
    clients.append(ws)
    logger.info(f"Dashboard client connected ({len(clients)} total)")

    # Live input (turn start → audio chunks → turn end) must reach Gemini in
    # order, but must never block this receive loop (operator STOP has to be
    # handled immediately). A per-connection serial worker gives both.
    live_queue: Optional[asyncio.Queue] = None
    live_worker: Optional[asyncio.Task] = None
    turn_open = False
    dropped_audio = 0
    if use_live and live_agent is not None:
        live_queue = asyncio.Queue()
        live_worker = asyncio.create_task(_live_input_worker(live_queue))

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
            if raw.get("bytes") is not None:
                if live_queue is not None:
                    if live_queue.qsize() < _MAX_QUEUED_LIVE_INPUTS:
                        live_queue.put_nowait(("audio", raw["bytes"]))
                    else:
                        dropped_audio += 1
                        if dropped_audio == 1 or dropped_audio % 100 == 0:
                            logger.warning(f"Live session is behind; dropped {dropped_audio} mic chunk(s)")
                continue

            text = raw.get("text")
            if not text:
                continue

            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue

            mtype = msg.get("type")

            if mtype == "operator":
                # Direct human control — never waits behind the AI.
                _spawn(_handle_operator(msg.get("action"), msg.get("args")))

            elif mtype == "command":
                user_text = str(msg.get("text") or "").strip()
                if not user_text:
                    continue
                # Exact operator words typed in chat ("stop", "land", ...) act
                # instantly without the AI, same as the text/voice CLIs.
                action = parse_operator_text(user_text)
                if action is not None:
                    _spawn(_handle_operator(action, {}, echo=user_text))
                    continue
                # The agent lifts an operator hold when it STARTS this turn
                # (not here: an AI turn still running from before a STOP must
                # keep being refused). keep_hold: camera-only requests such as
                # the Observe button must not lift a human STOP at all.
                _spawn(_handle_command(user_text, release_hold=not bool(msg.get("keep_hold"))))

            elif mtype == "audio_start":
                # Push-to-talk press = new user intent: begin_user_turn() lifts
                # any operator hold and opens an explicit user turn (barge-in +
                # fresh frame), in order with the audio that follows.
                if live_queue is not None:
                    live_queue.put_nowait(("begin", None))
                    turn_open = True
                logger.info("Mic ON (browser push-to-talk)")

            elif mtype == "audio_end":
                if live_queue is not None:
                    live_queue.put_nowait(("end", None))
                    turn_open = False
                logger.info("Mic OFF")

            elif mtype == "get_frame" and toolkit:
                frame = await asyncio.to_thread(toolkit.get_frame)
                if frame.get("success"):
                    await ws.send_text(json.dumps({
                        "type": "frame",
                        "image_base64": frame["image_base64"],
                    }))

    except WebSocketDisconnect:
        pass
    except RuntimeError as e:
        # Starlette raises RuntimeError if receive() is called after disconnect.
        logger.debug(f"Dashboard socket closed: {e}")
    finally:
        if ws in clients:
            clients.remove(ws)
        if live_queue is not None and live_worker is not None:
            if turn_open:
                # Browser vanished mid push-to-talk: close the turn so the
                # model doesn't wait forever for activity_end.
                live_queue.put_nowait(("end", None))
            # Let the worker drain what's queued (bounded), then stop it.
            live_queue.put_nowait(("stop", None))
            _spawn(_finish_live_worker(live_worker))
        logger.info(f"Dashboard client disconnected ({len(clients)} total)")


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def _live_input_worker(queue: asyncio.Queue):
    """Forward one connection's push-to-talk input to the Live session, in order."""
    while True:
        kind, payload = await queue.get()
        if kind == "stop":
            return
        agent_ = live_agent
        if agent_ is None:
            continue
        try:
            if kind == "audio":
                await _maybe_await(agent_.send_audio(payload))
            elif kind == "begin":
                begin = getattr(agent_, "begin_user_turn", None)
                if callable(begin):
                    await _maybe_await(begin())
            elif kind == "end":
                end = getattr(agent_, "end_user_turn", None)
                if callable(end):
                    await _maybe_await(end())
                elif hasattr(agent_, "mark_user_turn_end"):
                    agent_.mark_user_turn_end()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Live input ({kind}) failed: {e}")


async def _finish_live_worker(worker: asyncio.Task, timeout_s: float = 5.0):
    try:
        await asyncio.wait_for(worker, timeout=timeout_s)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        worker.cancel()
    except Exception as e:
        logger.debug(f"live input worker ended with error: {e}")


async def _handle_operator(action: Any, args: Any, echo: Optional[str] = None):
    """Run a direct operator action (bypasses the LLM) and report back."""
    if echo:
        await broadcast({"type": "chat", "role": "user", "text": echo})

    if not isinstance(args, dict):
        args = {}

    if toolkit is None:
        result = {"success": False, "error": "No drone toolkit configured"}
    elif not isinstance(action, str) or action not in OPERATOR_ACTIONS:
        result = {"success": False, "error": f"Unknown operator action: {action}"}
    else:
        try:
            # Stop-type actions never share the default executor with other work.
            pool = _INSTANT_POOL if action in _INSTANT_ACTIONS else _LAND_POOL if action == "land" else None
            result = await asyncio.get_running_loop().run_in_executor(
                pool, toolkit.operator_command, action, args,
            )
        except Exception as e:
            logger.error(f"Operator action {action} failed: {e}")
            result = {"success": False, "error": str(e)}

    log = logger.info if result.get("success") else logger.warning
    log(f"Operator {action}{args or ''}: {result.get('message') or result.get('error')}")

    await broadcast({
        "type": "operator_result",
        "action": action if isinstance(action, str) else str(action),
        "args": args,
        "result": result,
    })
    await broadcast_state()


async def _handle_command(user_text: str, release_hold: bool = True):
    """Route chat command through either Live API or legacy agent."""
    start = time.time()

    if use_live and live_agent is not None:
        # Live path: send_text() already echoes the user message through the
        # on_text callback, so broadcasting it here too produced a duplicate
        # user bubble in the dashboard. Let send_text() be the single source.
        await live_agent.send_text(user_text, release_hold=release_hold)
        logger.info(f"Live command sent: {user_text[:60]}")
        # State will be broadcast by on_tool callbacks as they fire
        return

    # Legacy path
    if agent is None:
        await broadcast({"type": "chat", "role": "system", "text": "No agent configured."})
        return

    await broadcast({"type": "chat", "role": "user", "text": user_text})
    try:
        response = await agent.process_message(user_text, release_hold=release_hold)
    except Exception as e:
        logger.error(f"Legacy agent failed: {e}")
        await broadcast({"type": "chat", "role": "system", "text": f"Agent error: {e}"})
        await broadcast_state()
        return
    elapsed = time.time() - start
    logger.info(f"Legacy command processed in {elapsed:.2f}s | {response[:60]}")
    await broadcast({"type": "chat", "role": "assistant", "text": response})

    if tracer and tracer.completed_traces:
        latest = tracer.get_recent_traces(1)
        if latest:
            await broadcast({"type": "trace", "data": latest[0]})

    await broadcast_state()


# ── HTTP routes ────────────────────────────────────────────────────────────

@app.get("/")
async def get_dashboard():
    """Serve the dashboard HTML (or build instructions if it isn't built)."""
    index = STATIC_DIST_DIR / "index.html"
    if not index.exists():
        return HTMLResponse(BUILD_MISSING_HTML, status_code=503)
    return FileResponse(index)


@app.get("/video-status", dependencies=[Depends(require_api_token)])
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
@app.get("/webcam-status", dependencies=[Depends(require_api_token)])
async def webcam_status():
    return await video_status()


api = APIRouter(prefix="/api", dependencies=[Depends(require_api_token)])


@api.get("/traces")
async def get_traces(limit: int = 50):
    if tracer:
        return {"traces": tracer.get_recent_traces(limit)}
    return {"traces": []}


@api.get("/traces/{trace_id}")
async def get_trace(trace_id: str):
    if tracer:
        trace = tracer.get_trace(trace_id)
        if trace:
            return trace
    raise HTTPException(status_code=404, detail="Trace not found")


app.include_router(api)


async def telemetry_loop():
    """
    Periodically broadcast drone state to the dashboard.

    No drone I/O here: the safety watchdog refreshes telemetry every second.
    """
    while True:
        try:
            if toolkit and clients:
                await broadcast_state()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"telemetry broadcast failed: {e}")
        await asyncio.sleep(1.0)
