"""
Gemini Live voice agent (MODELS["live"], gemini-3.8-live).

One logical Live API session hears the user, sees the drone camera and flies
the drone through TelloToolkit:

- Audio in (16 kHz PCM) and audio out (24 kHz PCM), plus input/output
  transcription for the chat log, tracing and the stop-word fast path.
- Two turn-taking modes:
    push_to_talk=True  (dashboard) Automatic activity detection is off. The
                       host calls begin_user_turn()/end_user_turn(), which send
                       activity_start/activity_end. Mic audio is never gated,
                       so holding the button really barges in.
    push_to_talk=False (CLI)       Server VAD on an open mic, with a
                       half-duplex echo gate so laptop speakers don't make the
                       model interrupt itself (headphones recommended).
- Video: drone camera frames as JPEG, never more than 1 FPS. gemini-3.8-live
  puts video into user turns by default, so push-to-talk mode only sends
  frames while the user is talking, while tools run, and right after a move.
- Tools are declared BLOCKING (3.8 defaults to async; a drone action must
  finish before the model continues). Each call runs as its own task keyed by
  call id, so the receive loop keeps reading go_away and tool_call_cancellation
  while the drone moves. A cancelled in-flight motion is aborted with
  toolkit.hover().
- Spoken stop words ("stop", "land now", ...) act immediately through
  toolkit.operator_command() without waiting for the model.
- Context-window compression lifts the 2-minute audio+video session cap.
  Session resumption keeps one session alive across ~10-minute connections:
  go_away triggers a proactive reconnect with the newest resumable handle;
  dropped connections reconnect with exponential backoff.
- A tool response only ever goes to the connection that issued the call. When
  a connection is lost (or a go_away deadline passes) with calls pending, its
  queued calls are cancelled, executing ones finish, and the model gets one
  system notice on the new connection listing what ran and what didn't.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from google import genai
from google.genai import types
from PIL import Image

from config import GEMINI_API_KEY, LIVE, MODELS, TOOL_SCHEMAS
from core.toolkit import TelloToolkit

logger = logging.getLogger(__name__)


VOICE_SYSTEM_PROMPT = (
    "You are TelloAI, the voice of a small indoor DJI Tello drone. You see the "
    "drone's camera as video frames (sent while the user talks and after every "
    "move). Use them to answer questions about the surroundings and to judge "
    "whether a move is safe.\n\n"
    "Rules:\n"
    "- Keep spoken replies short and natural: one or two sentences. No markdown, "
    "no lists.\n"
    "- Confirm actions briefly: 'Taking off', not long explanations.\n"
    "- Each move is at most 100 cm per call; the drone software enforces this "
    "and clamps longer requests. For longer distances, move in steps and look at "
    "the camera between steps. You have no depth sensor, so judge distance from "
    "object size.\n"
    "- Stay within 8 meters of the takeoff point and below 200 cm altitude "
    "(both enforced in software).\n"
    "- Relay tool errors honestly: say plainly what failed and why. Never claim "
    "an action happened unless its tool result says success.\n"
    "- A tool error starting with 'Operator hold' means a human operator stopped "
    "the drone. Do not retry or start any motion until the user speaks to you "
    "again with a new instruction.\n"
    "- If the user just says stop, hover, freeze or land, the drone's software "
    "has already acted on it. Acknowledge briefly; do not call motion tools.\n"
    "- You have no emergency-stop or flip tools; those are for the human operator "
    "only. If asked, tell the user to use the operator controls.\n"
    "- Messages starting with [SAFETY NOTICE] come from the drone's safety "
    "system, not the user. Briefly tell the user what happened, and do not "
    "start new motion because of them.\n"
    "- If battery drops below 20%, warn the user and land.\n"
    "- For risky moves (through doorways, near people) confirm with the user first.\n"
    "- On 'what do you see?' just describe the latest camera frame; don't call a tool.\n"
)


# ── Callback types ──────────────────────────────────────────────────────────
AudioCallback = Callable[[bytes], Awaitable[None]] | Callable[[bytes], None]
TextCallback = Callable[[str, str], Awaitable[None]] | Callable[[str, str], None]  # (role, text)
ToolCallback = Callable[[str, dict, dict], Awaitable[None]] | Callable[[str, dict, dict], None]
                          # (name, args, result)


# ── Tunables (module-level so tests can patch them) ─────────────────────────
_MAX_VIDEO_FPS = 1.0          # Live API accepts <= 1 FPS
_VIDEO_IDLE_POLL_S = 0.25     # PTT idle: how often the pump re-checks whether video is wanted
_SEND_WAIT_S = 5.0            # send_text/send_system_notice wait this long for a reconnecting link
_GO_AWAY_DEFAULT_S = 5.0      # assumed time_left when go_away doesn't carry a parseable one
_GO_AWAY_MARGIN_S = 1.0       # reconnect this long before the server's deadline
_HEALTHY_AFTER_S = 30.0       # a connection that lived this long counts as healthy
_HANDLE_MAX_FAILURES = 2      # failed resumes with one handle before starting a fresh session
_CLOSE_TIMEOUT_S = 5.0        # max wait for a websocket close

# Tools whose execution is aborted with toolkit.hover() when the server cancels them.
_ABORTABLE_TOOLS = frozenset({"takeoff", "move", "rotate"})
# Tools after which the model gets a fresh camera frame before the tool response.
_FRAME_AFTER_TOOLS = frozenset({"takeoff", "land", "move", "rotate"})


# ── Stop-word fast path ─────────────────────────────────────────────────────
_STOP_PHRASES = {
    "stop": "hover", "stop now": "hover", "stop it": "hover", "stop the drone": "hover",
    "halt": "hover", "halt now": "hover",
    "hover": "hover", "hover now": "hover",
    "freeze": "hover", "freeze now": "hover",
    "hold": "hover", "hold position": "hover",
    "land": "land", "land now": "land", "land it": "land", "land the drone": "land",
    "land immediately": "land",
}
_STOP_FILLERS = frozenset({"please", "ok", "okay", "hey"})
_WORD_RE = re.compile(r"[a-z']+")


def match_stop_word(utterance: str) -> Optional[str]:
    """
    Map a whole spoken utterance that is just a stop word to an operator action.

    Case- and punctuation-insensitive; repeated words ("stop stop stop") and a
    leading/trailing "please"/"okay" are tolerated. Returns "hover", "land" or None.
    """
    words = _WORD_RE.findall((utterance or "").lower())
    words = [w for i, w in enumerate(words) if i == 0 or w != words[i - 1]]
    while words and words[0] in _STOP_FILLERS:
        words.pop(0)
    while words and words[-1] in _STOP_FILLERS:
        words.pop()
    return _STOP_PHRASES.get(" ".join(words))


# ── Config builders ─────────────────────────────────────────────────────────

def _enum_member(enum_cls, value, prefix: str = ""):
    """Strict enum lookup by name ('MEDIA_RESOLUTION_LOW' or just 'low')."""
    if isinstance(value, enum_cls):
        return value
    name = str(value).strip().upper()
    for candidate in (name, f"{prefix}{name}"):
        if candidate in enum_cls.__members__:
            return enum_cls[candidate]
    raise ValueError(f"Invalid {enum_cls.__name__} value in config: {value!r}")


def _schema_from_json(prop: dict) -> types.Schema:
    kwargs: dict[str, Any] = {"type": _enum_member(types.Type, prop.get("type", "string"))}
    if "description" in prop:
        kwargs["description"] = prop["description"]
    if "enum" in prop:
        kwargs["enum"] = [str(v) for v in prop["enum"]]
    if "items" in prop:
        kwargs["items"] = _schema_from_json(prop["items"])
    return types.Schema(**kwargs)


def _build_live_tools() -> list[types.Tool]:
    """TOOL_SCHEMAS -> Live tool declarations (minus LIVE['disabled_tools']), all BLOCKING."""
    disabled = set(LIVE.get("disabled_tools", []))
    declarations = []
    for schema in TOOL_SCHEMAS:
        if schema["name"] in disabled:
            continue
        params = schema.get("parameters") or {}
        props = params.get("properties") or {}
        decl_kwargs: dict[str, Any] = {
            "name": schema["name"],
            "description": schema["description"],
            # gemini-3.8-live defaults to NON_BLOCKING (async) tools. Drone
            # actions must finish before the model continues talking/acting.
            "behavior": types.Behavior.BLOCKING,
        }
        if props:
            decl_kwargs["parameters"] = types.Schema(
                type=types.Type.OBJECT,
                properties={name: _schema_from_json(p) for name, p in props.items()},
                required=list(params.get("required", [])),
            )
        declarations.append(types.FunctionDeclaration(**decl_kwargs))
    return [types.Tool(function_declarations=declarations)]


def build_live_config(push_to_talk: bool = False, resume_handle: Optional[str] = None) -> types.LiveConnectConfig:
    """
    Typed connect config for gemini-3.8-live.

    No thinking_config (unsupported on 3.8), no temperature (ignored), no
    affective dialog (removed) and no proactivity (always on; setting it false errors).
    """
    if push_to_talk:
        # Client-side turn taking: the host sends activity_start/activity_end.
        activity_detection = types.AutomaticActivityDetection(disabled=True)
    else:
        activity_detection = types.AutomaticActivityDetection(
            disabled=False,
            start_of_speech_sensitivity=_enum_member(
                types.StartSensitivity, LIVE.get("vad_start_sensitivity", "START_SENSITIVITY_LOW"),
                "START_SENSITIVITY_",
            ),
            silence_duration_ms=int(LIVE.get("vad_silence_duration_ms", 500)),
            prefix_padding_ms=int(LIVE.get("vad_prefix_padding_ms", 200)),
        )
        if LIVE.get("vad_end_sensitivity"):
            activity_detection.end_of_speech_sensitivity = _enum_member(
                types.EndSensitivity, LIVE["vad_end_sensitivity"], "END_SENSITIVITY_",
            )

    if push_to_talk:
        coverage = LIVE.get("turn_coverage_ptt", "TURN_INCLUDES_AUDIO_ACTIVITY_AND_ALL_VIDEO")
    else:
        coverage = LIVE.get("turn_coverage", "TURN_INCLUDES_ONLY_ACTIVITY")
    realtime_input_config = types.RealtimeInputConfig(
        automatic_activity_detection=activity_detection,
        activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
        turn_coverage=_enum_member(types.TurnCoverage, coverage, "TURN_"),
    )

    return types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        system_instruction=VOICE_SYSTEM_PROMPT,
        tools=_build_live_tools(),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=LIVE["voice_name"]),
            ),
        ),
        input_audio_transcription=types.AudioTranscriptionConfig() if LIVE.get("input_transcription", True) else None,
        output_audio_transcription=types.AudioTranscriptionConfig() if LIVE.get("output_transcription", True) else None,
        media_resolution=_enum_member(
            types.MediaResolution, LIVE.get("media_resolution", "MEDIA_RESOLUTION_LOW"), "MEDIA_RESOLUTION_",
        ),
        realtime_input_config=realtime_input_config,
        context_window_compression=types.ContextWindowCompressionConfig(
            trigger_tokens=int(LIVE.get("compression_trigger_tokens", 25600)),
            sliding_window=types.SlidingWindow(target_tokens=int(LIVE.get("compression_target_tokens", 12800))),
        ),
        session_resumption=types.SessionResumptionConfig(handle=resume_handle),
    )


# ── Small helpers ───────────────────────────────────────────────────────────

def _encode_frame_for_live(image_base64: str) -> Optional[bytes]:
    """Decode base64 JPEG, downscale, re-encode for Live API video input."""
    try:
        raw = base64.b64decode(image_base64)
        img = Image.open(io.BytesIO(raw))
        max_side = LIVE.get("video_max_side", 640)
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=LIVE.get("video_jpeg_quality", 60))
        return buf.getvalue()
    except Exception as e:
        logger.debug(f"Frame encode for Live failed: {e}")
        return None


_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*s?\s*$")


def _parse_duration_s(value) -> Optional[float]:
    """Parse a protobuf Duration string like '50s' or '1.5s'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _DURATION_RE.match(str(value))
    return float(match.group(1)) if match else None


@dataclass(eq=False)
class _ToolCall:
    key: str
    call_id: Optional[str]
    name: str
    args: dict
    session: Any                        # the connection that issued the call; only it gets the response
    task: Optional[asyncio.Task] = None
    started: bool = False
    cancelled: bool = False             # cancelled by the server (tool_call_cancellation)
    orphaned: bool = False              # its connection dropped before it responded
    outcome: Optional[dict] = None      # the tool response payload, once there is one


@dataclass(eq=False)
class _LinkDrop:
    """Tool calls of a connection that dropped before they could respond."""
    ran: list                           # executing (or finished, response undelivered): reported with outcome
    skipped: list                       # queued: cancelled, never executed


def _describe_call(rec: _ToolCall) -> str:
    return f"{rec.name}({' '.join(str(v) for v in rec.args.values())})"


def _describe_outcome(outcome: Optional[dict]) -> str:
    if not isinstance(outcome, dict):
        return "outcome unknown"
    if outcome.get("success", True):
        message = str(outcome.get("message") or "").strip()
        return f"success ({message[:80]})" if message else "success"
    return f"failed: {str(outcome.get('error') or 'unknown error')[:80]}"


_HEADPHONE_HINT_SHOWN = False


def _log_headphone_hint():
    global _HEADPHONE_HINT_SHOWN
    if _HEADPHONE_HINT_SHOWN:
        return
    _HEADPHONE_HINT_SHOWN = True
    logger.info(
        "Open-mic voice mode: the mic is muted while TelloAI speaks (echo gate), so you "
        "can't talk over it. Use headphones for the best experience, or the dashboard's "
        "push-to-talk for real barge-in."
    )


class LiveAgent:
    """
    Persistent Gemini Live API session that drives the drone.

    Lifecycle:
        agent = LiveAgent(toolkit, tracer, push_to_talk=True)
        agent.on_audio(play_speaker)          # cb(pcm_bytes)
        agent.on_text(dashboard_broadcast)    # cb(role, text); role: user|assistant|system
        agent.on_tool(...)                    # cb(name, args, result)
        agent.on_interrupt(speaker.flush)     # cb()
        agent.on_metric(...)                  # cb(name, value), e.g. "voice_latency_ms"
        await agent.start()
        await agent.begin_user_turn(); await agent.send_audio(pcm); await agent.end_user_turn()
        await agent.send_text("take off")
        await agent.send_system_notice("Battery low (19%)")
        ...
        await agent.stop()
    """

    def __init__(self, toolkit: TelloToolkit, tracer=None, push_to_talk: bool = False):
        self.toolkit = toolkit
        self.tracer = tracer
        self.push_to_talk = bool(push_to_talk)
        self.client = None  # genai.Client, created in start() (slow to build; tests inject a fake)
        self.model = MODELS["live"]

        # Connection (one logical session across many websocket connections)
        self._session = None
        self._ctx = None  # async context manager from client.aio.live.connect
        self._running = False
        self._link_failed = False
        self._connected = asyncio.Event()
        self._supervisor_task: Optional[asyncio.Task] = None
        self._video_task: Optional[asyncio.Task] = None
        self._bg_tasks: set[asyncio.Task] = set()
        self._reconnect_evt: Optional[asyncio.Event] = None

        # Session resumption
        self._resume_handle: Optional[str] = None
        self._resumable = asyncio.Event()   # set while the latest update said resumable
        self._handle_failures = 0
        self._attempts = 0                  # reconnect attempts since the last healthy connection
        self._session_healthy = False
        self._session_opened_at = 0.0
        self._go_away_session = None

        # Tool calls in flight, keyed by call id
        self._tool_calls: dict[str, _ToolCall] = {}
        self._undelivered: list[_ToolCall] = []  # finished, but sending the response failed
        self._tool_lock = asyncio.Lock()    # drone commands run one at a time, in order
        self._executing = 0
        self._stop_commands: set[asyncio.Future] = set()  # stop-word operator commands in flight

        # Video (rate-limited to <= 1 FPS across the pump and explicit pushes)
        self._frame_lock = asyncio.Lock()
        self._last_frame_ts = 0.0
        fps = min(max(float(LIVE.get("video_fps", 1.0)), 0.1), _MAX_VIDEO_FPS)
        self._frame_interval = 1.0 / fps

        # Push-to-talk turn state
        self._ptt_active = False
        self._ptt_turn_seq = 0
        self._ptt_end_task: Optional[asyncio.Task] = None
        self._activity_session = None       # connection the open activity_start was sent on

        # Trace for this session (one trace covers the whole conversation)
        self._session_trace_id: Optional[str] = None

        # Transcription buffers
        self._input_transcript_buf: str = ""   # whole user turn, for the chat log
        self._output_transcript_buf: str = ""
        self._utterance_buf: str = ""          # current utterance, for the stop-word check

        # Half-duplex echo gate (open-mic mode only). While the model is
        # speaking, mic audio is dropped so speaker output bleeding back into
        # the mic doesn't register as user speech.
        self._model_speaking = False
        self._last_model_audio_ts: float = 0.0
        # Extra grace period after the last model audio chunk — speaker
        # playback lags the API by the prebuffer amount plus OS latency.
        self._echo_guard_ms: int = 500

        # Latency tracking: time from user turn end (text send, push-to-talk
        # release, or finalized VAD transcription) to the model's first output.
        self._turn_user_ts: float = 0.0
        self._last_user_audio_ts: float = 0.0
        self._latency_emitted: bool = False
        self._latency_start_locked: bool = False

        # Callbacks
        self._on_audio: Optional[AudioCallback] = None
        self._on_text: Optional[TextCallback] = None
        self._on_tool: Optional[ToolCallback] = None
        self._on_interrupt: Optional[Callable] = None
        self._on_metric: Optional[Callable] = None

    # ── Callback registration ──
    def on_audio(self, cb: AudioCallback):
        self._on_audio = cb

    def on_text(self, cb: TextCallback):
        """cb(role, text) where role is 'user' | 'assistant' | 'system'."""
        self._on_text = cb

    def on_tool(self, cb: ToolCallback):
        self._on_tool = cb

    def on_interrupt(self, cb: Callable):
        """cb() — flush local playback (barge-in / server interruption)."""
        self._on_interrupt = cb

    def on_metric(self, cb: Callable):
        """cb(name, value) for lightweight telemetry (e.g. voice latency)."""
        self._on_metric = cb

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    @property
    def link_failed(self) -> bool:
        """True after reconnecting gave up; call stop() + start() to recover."""
        return self._link_failed

    async def _emit(self, cb, *args):
        if cb is None:
            return
        try:
            await _maybe_await(cb(*args))
        except Exception as e:
            logger.warning(f"Live callback {getattr(cb, '__name__', cb)!r} failed: {e}")

    async def _notify(self, text: str):
        logger.info(f"[Live] {text}")
        await self._emit(self._on_text, "system", text)

    def _spawn_bg(self, coro, name: Optional[str] = None) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_task_done)
        return task

    def _bg_task_done(self, task: asyncio.Task):
        self._bg_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning(f"Live background task {task.get_name()} failed: {task.exception()!r}")

    # ── Latency bookkeeping ──
    def _start_latency_timer(self, lock: bool):
        self._turn_user_ts = time.perf_counter()
        self._latency_emitted = False
        self._latency_start_locked = lock

    def _reset_latency(self):
        self._turn_user_ts = 0.0
        self._last_user_audio_ts = 0.0
        self._latency_emitted = False
        self._latency_start_locked = False

    def mark_user_turn_end(self):
        """Start response latency timing when the user has finished input (sync, latency only)."""
        if self._model_speaking:
            return
        self._start_latency_timer(lock=True)

    def _mark_vad_turn_end(self):
        if not self._model_speaking and not self._latency_emitted:
            self._start_latency_timer(lock=True)

    def _enum_value(self, value) -> str:
        return str(getattr(value, "value", value))

    async def _emit_response_latency_once(self):
        if self._latency_emitted:
            return
        start_ts = self._turn_user_ts or self._last_user_audio_ts
        if not start_ts:
            return
        latency_ms = round((time.perf_counter() - start_ts) * 1000)
        self._latency_emitted = True
        await self._emit(self._on_metric, "voice_latency_ms", latency_ms)

    # ── Session lifecycle ──
    async def start(self):
        """Open the first connection (raises if that fails), then keep it alive."""
        if self._running:
            return
        if self.client is None:
            if not GEMINI_API_KEY:
                raise RuntimeError("GEMINI_API_KEY not configured")
            self.client = genai.Client(api_key=GEMINI_API_KEY)

        # Fresh primitives: asyncio locks/events bind to the first loop that
        # waits on them, and a restarted agent may run on a different loop.
        self._connected = asyncio.Event()
        self._resumable = asyncio.Event()
        self._tool_lock = asyncio.Lock()
        self._frame_lock = asyncio.Lock()

        self._running = True
        self._link_failed = False
        self._resume_handle = None
        self._handle_failures = 0
        self._attempts = 0
        try:
            await self._open_session()
        except Exception as e:
            self._running = False
            logger.error(f"Failed to open Live session: {e}")
            raise
        mode = "push-to-talk" if self.push_to_talk else "open mic"
        logger.info(f"Live session opened with {self.model} (voice={LIVE['voice_name']}, {mode})")
        if not self.push_to_talk:
            _log_headphone_hint()

        if self.tracer:
            self._session_trace_id = self.tracer.start_trace(
                user_message="[Live session]",
                drone_state=self.toolkit.state.to_dict(),
            )

        self._supervisor_task = asyncio.create_task(self._supervise(), name="live_supervisor")
        self._video_task = asyncio.create_task(self._video_loop(), name="live_video")

    async def stop(self):
        self._running = False
        current = asyncio.current_task()
        tasks = [t for t in (self._supervisor_task, self._video_task, self._ptt_end_task) if t is not None]
        tasks += list(self._bg_tasks)
        tasks += [rec.task for rec in list(self._tool_calls.values()) if rec.task is not None]
        tasks = [t for t in tasks if t is not current and not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._supervisor_task = None
        self._video_task = None
        self._ptt_end_task = None
        self._bg_tasks.clear()
        self._tool_calls.clear()
        self._undelivered.clear()
        self._stop_commands.clear()
        self._executing = 0
        self._ptt_active = False

        await self._close_session()
        self._resume_handle = None

        if self.tracer and self._session_trace_id:
            self.tracer.end_trace(self._session_trace_id, final_response="[Live session closed]")
            self._session_trace_id = None

        logger.info("Live session closed")

    async def _open_session(self):
        config = build_live_config(self.push_to_talk, self._resume_handle)
        ctx = self.client.aio.live.connect(model=self.model, config=config)
        session = await ctx.__aenter__()
        self._ctx = ctx
        self._session = session
        self._session_healthy = False
        self._session_opened_at = time.monotonic()
        self._go_away_session = None
        self._resumable.clear()
        self._link_failed = False
        self._connected.set()
        # The user is mid push-to-talk turn: reopen the activity on this connection.
        if self.push_to_talk and self._ptt_active:
            if await self._send_realtime_on(session, activity_start=types.ActivityStart()):
                self._activity_session = session

    async def _close_session(self):
        ctx, self._ctx = self._ctx, None
        self._session = None
        self._connected.clear()
        self._activity_session = None
        self._model_speaking = False  # a dropped turn never sends turn_complete
        await self._flush_transcripts()
        if ctx is not None:
            try:
                await asyncio.wait_for(ctx.__aexit__(None, None, None), timeout=_CLOSE_TIMEOUT_S)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"Live connection close: {e}")

    async def _wait_for_session(self):
        """Current session, waiting briefly if the link is reconnecting. None if unavailable."""
        if self._session is not None:
            return self._session
        if not self._running or self._link_failed:
            return None
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=_SEND_WAIT_S)
        except asyncio.TimeoutError:
            return None
        return self._session

    def _link_down_message(self, what: str) -> str:
        if self._link_failed:
            return f"Voice link failed — restart required ({what})."
        if self._running:
            return f"Voice link is reconnecting — {what}. Please try again in a moment."
        return f"Voice session is not running — {what}."

    # ── Connection supervisor: receive loop + go_away / error reconnects ──
    async def _supervise(self):
        while self._running:
            session = self._session
            if session is None:
                if not await self._reconnect(immediate=False):
                    break
                continue

            self._reconnect_evt = asyncio.Event()
            recv = asyncio.create_task(self._receive_loop(session), name="live_recv")
            wake = asyncio.create_task(self._reconnect_evt.wait(), name="live_reconnect_wait")
            try:
                await asyncio.wait({recv, wake}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for t in (recv, wake):
                    if not t.done():
                        t.cancel()
                await asyncio.gather(recv, wake, return_exceptions=True)
            if not self._running:
                break

            graceful = self._reconnect_evt.is_set() or self._go_away_session is session
            error = recv.exception() if recv.done() and not recv.cancelled() else None
            if self._session_healthy or time.monotonic() - self._session_opened_at >= _HEALTHY_AFTER_S:
                self._attempts = 0
            if graceful:
                logger.info("Live connection is being recycled (go_away); resuming the session")
            else:
                logger.warning(f"Live connection lost: {error!r}")

            # Before _close_session(): from here on, no call of this connection may
            # start, and none of their results may go to the next connection.
            dropped = self._orphan_tool_calls(session)
            await self._close_session()
            if not await self._reconnect(immediate=graceful):
                if self._running:
                    self._link_failed = True
                    await self._notify("Voice link failed — restart required")
                    if dropped is not None:
                        self._spawn_bg(self._report_link_drop(dropped, to_model=False), name="live_drop_report")
                break
            if dropped is not None:
                self._spawn_bg(self._report_link_drop(dropped, to_model=True), name="live_drop_report")

    async def _reconnect(self, immediate: bool) -> bool:
        """Reopen the connection with the newest resumption handle. False = gave up."""
        max_attempts = max(1, int(LIVE.get("reconnect_max_attempts", 5)))
        base_delay = max(0.0, float(LIVE.get("reconnect_backoff_s", 1.0)))
        announced = False
        dropped_handle = False
        while self._running:
            if self._attempts >= max_attempts:
                return False
            if not immediate or self._attempts > 0:
                if not announced:
                    announced = True
                    await self._notify("Voice link reconnecting…")
                delay = base_delay * (2 ** self._attempts)
                if delay > 0:
                    await asyncio.sleep(delay)
                if not self._running:
                    return False
            immediate = False
            self._attempts += 1
            used_handle = self._resume_handle
            try:
                await self._open_session()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Live reconnect attempt {self._attempts}/{max_attempts} failed: {e}")
                if used_handle:
                    self._handle_failures += 1
                    if self._handle_failures >= _HANDLE_MAX_FAILURES:
                        # Expired (>2 h) or invalid handle: start a fresh session.
                        logger.warning("Dropping the session resumption handle; starting a fresh session")
                        self._resume_handle = None
                        self._handle_failures = 0
                        dropped_handle = True
                continue
            self._handle_failures = 0
            logger.info(f"Live connection reopened ({'resumed session' if used_handle else 'new session'})")
            if announced:
                if dropped_handle or not used_handle:
                    await self._notify("Voice link restored (new session — earlier conversation context was lost)")
                else:
                    await self._notify("Voice link restored")
            return True
        return False

    def _request_reconnect(self, session):
        if session is not None and session is self._session and self._reconnect_evt is not None:
            self._reconnect_evt.set()

    async def _receive_loop(self, session):
        # session.receive() yields one model turn and then returns; keep listening.
        while self._running and session is self._session:
            got_any = False
            async for message in session.receive():
                got_any = True
                if not self._running or session is not self._session:
                    return
                if not self._session_healthy:
                    self._session_healthy = True
                    self._attempts = 0
                try:
                    await self._handle_response(message)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Error handling a Live server message")
            if not got_any:
                raise ConnectionError("Live receive stream ended")

    # ── Input side ──
    async def send_text(self, text: str, *, release_hold: bool = True):
        """
        Typed user input; always produces a model turn. Clears the operator hold
        (new user intent) unless release_hold=False, for requests that must not
        lift a human STOP (e.g. a camera-only "describe what you see").
        """
        text = (text or "").strip()
        if not text:
            return
        session = await self._wait_for_session()
        if session is None:
            # Not delivered: keep any operator hold, or the model's next turn
            # (e.g. its reply to a link-drop notice) could move the drone.
            await self._notify(self._link_down_message("message not sent"))
            return
        hold_was_set = self.toolkit.operator_hold
        if release_hold:
            self.toolkit.clear_operator_hold()  # new user intent
        # Best effort: give the model a current view (rate-limited, so a no-op
        # if the pump just sent one).
        await self._push_frame(wait=False)
        self._start_latency_timer(lock=True)
        try:
            # On gemini-3.8-live client content works for the whole session and
            # turn_complete=True always yields a turn — in both VAD modes (with
            # automatic activity detection off, realtime text would not).
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=text)]),
                turn_complete=True,
            )
        except Exception as e:
            logger.error(f"send_text error: {e}")
            if hold_was_set and release_hold:
                self.toolkit.state.operator_hold = True  # undelivered: restore the hold
            await self._notify("Couldn't deliver your message to the voice model.")
            return
        await self._emit(self._on_text, "user", text)

    async def send_system_notice(self, text: str):
        """Tell the model about a safety/system event so it informs the user. Keeps any operator hold."""
        text = (text or "").strip()
        if not text:
            return
        session = await self._wait_for_session()
        if session is None:
            logger.warning(f"System notice not delivered to the voice model (link down): {text}")
            return
        notice = f"[SAFETY NOTICE from the drone's watchdog — not from the user] {text} Briefly tell the user."
        try:
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=notice)]),
                turn_complete=True,
            )
        except Exception as e:
            logger.warning(f"send_system_notice error: {e}")

    async def send_audio(self, pcm_chunk: bytes):
        session = self._session
        if session is None or not pcm_chunk:
            return  # reconnecting: drop

        if self.push_to_talk:
            # The user is deliberately holding the button: no echo gate (real
            # barge-in). Outside a turn the server would ignore the audio anyway.
            if not self._ptt_active:
                return
        else:
            # Half-duplex: drop mic audio while the model is speaking so that
            # speaker output bleeding into the mic doesn't feed back as user
            # speech (choppy playback + ghost transcriptions).
            if self._model_speaking:
                return
            if self._last_model_audio_ts:
                since_last_ms = (time.time() - self._last_model_audio_ts) * 1000
                if since_last_ms < self._echo_guard_ms:
                    return

        try:
            await session.send_realtime_input(
                audio=types.Blob(
                    data=pcm_chunk,
                    mime_type=f"audio/pcm;rate={LIVE['input_sample_rate']}",
                )
            )
            if not self._model_speaking and not self._latency_emitted:
                self._last_user_audio_ts = time.perf_counter()
        except Exception as e:
            logger.debug(f"send_audio error: {e}")

    async def send_video_frame(self, jpeg_bytes: bytes) -> bool:
        """Send one JPEG frame (dropped if it would exceed 1 FPS). Returns True if sent."""
        if self._session is None or not jpeg_bytes:
            return False
        async with self._frame_lock:
            if time.monotonic() - self._last_frame_ts < self._frame_interval:
                return False
            return await self._send_frame_bytes(jpeg_bytes)

    async def _send_realtime_on(self, session, **kwargs) -> bool:
        if session is None:
            return False
        try:
            await session.send_realtime_input(**kwargs)
            return True
        except Exception as e:
            logger.warning(f"send_realtime_input({', '.join(kwargs)}) failed: {e}")
            return False

    # ── Push-to-talk ──
    async def begin_user_turn(self):
        """
        Push-to-talk pressed: flush playback, lift the operator hold, open a
        user activity (interrupting the model) and show the model a fresh frame.
        Open-mic mode: latency bookkeeping only.
        """
        self._reset_latency()
        if not self.push_to_talk:
            return

        self._ptt_turn_seq += 1
        pending_end = self._ptt_end_task
        self._ptt_end_task = None
        if pending_end is not None and not pending_end.done():
            pending_end.cancel()  # pressed again during the release tail: keep the activity open
        self._ptt_active = True
        self._model_speaking = False
        self._utterance_buf = ""

        await self._emit(self._on_interrupt)   # flush host speaker
        self.toolkit.clear_operator_hold()      # new user intent

        session = self._session
        if session is not None and self._activity_session is not session:
            if await self._send_realtime_on(session, activity_start=types.ActivityStart()):
                self._activity_session = session
        self._spawn_bg(self._push_frame(wait=True), name="live_turn_frame")

    async def end_user_turn(self, wait: bool = False):
        """
        Push-to-talk released. Audio keeps flowing for LIVE['ptt_tail_ms'] so the
        last word isn't clipped, then activity_end is sent and the response
        latency timer starts. Returns immediately (so a caller that awaits this
        inside its socket loop doesn't block the trailing audio) unless wait=True.
        Open-mic mode: same as mark_user_turn_end().
        """
        if not self.push_to_talk:
            self.mark_user_turn_end()
            return
        task = self._ptt_end_task
        if task is None or task.done():
            if not self._ptt_active:
                return
            task = asyncio.create_task(self._finish_user_turn(self._ptt_turn_seq), name="live_ptt_end")
            self._ptt_end_task = task
        if wait:
            await asyncio.wait({task})

    async def _finish_user_turn(self, seq: int):
        tail_s = max(0.0, float(LIVE.get("ptt_tail_ms", 300))) / 1000.0
        if tail_s:
            await asyncio.sleep(tail_s)
        if seq != self._ptt_turn_seq:
            return  # a new turn started during the tail
        self._ptt_active = False
        session = self._activity_session
        self._activity_session = None
        self._start_latency_timer(lock=True)
        if session is not None and session is self._session:
            await self._send_realtime_on(session, activity_end=types.ActivityEnd())

    # ── Video ──
    def _video_wanted(self) -> bool:
        if not self.push_to_talk:
            return True
        # 3.8 includes video in user turns: only spend tokens when it matters.
        return self._ptt_active or self._executing > 0

    def _capture_frame_jpeg(self) -> Optional[bytes]:
        result = self.toolkit.get_frame()
        if not isinstance(result, dict) or not result.get("success"):
            return None
        b64 = result.get("image_base64")
        return _encode_frame_for_live(b64) if b64 else None

    async def _send_frame_bytes(self, jpeg: bytes) -> bool:
        session = self._session
        if session is None:
            return False
        try:
            await session.send_realtime_input(video=types.Blob(data=jpeg, mime_type="image/jpeg"))
        except Exception as e:
            logger.debug(f"send_video error: {e}")
            return False
        self._last_frame_ts = time.monotonic()
        return True

    async def _push_frame(self, wait: bool) -> bool:
        """Capture and send a camera frame now, honoring the 1 FPS cap (wait for the slot if wait=True)."""
        if self._session is None:
            return False
        async with self._frame_lock:
            delay = self._frame_interval - (time.monotonic() - self._last_frame_ts)
            if delay > 0:
                if not wait:
                    return False
                await asyncio.sleep(delay)
            try:
                jpeg = await asyncio.to_thread(self._capture_frame_jpeg)
            except Exception as e:
                logger.debug(f"Frame capture failed: {e}")
                return False
            if not jpeg:
                return False
            return await self._send_frame_bytes(jpeg)

    async def _video_loop(self):
        while self._running:
            try:
                if self._session is None or not self._video_wanted():
                    await asyncio.sleep(_VIDEO_IDLE_POLL_S)
                    continue
                delay = self._frame_interval - (time.monotonic() - self._last_frame_ts)
                if delay > 0:
                    await asyncio.sleep(delay)
                    continue
                if not await self._push_frame(wait=False):
                    await asyncio.sleep(self._frame_interval)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"Video loop error: {e}")
                await asyncio.sleep(self._frame_interval)

    # ── Server messages ──
    async def _handle_response(self, response):
        session = self._session

        update = getattr(response, "session_resumption_update", None)
        if update is not None:
            if getattr(update, "resumable", False) and getattr(update, "new_handle", None):
                self._resume_handle = update.new_handle
                self._handle_failures = 0
                self._resumable.set()
            else:
                self._resumable.clear()

        go_away = getattr(response, "go_away", None)
        if go_away is not None:
            self._on_go_away(go_away, session)

        voice_activity = getattr(response, "voice_activity", None)
        if voice_activity:
            activity_type = self._enum_value(getattr(voice_activity, "voice_activity_type", ""))
            if activity_type == "ACTIVITY_END":
                self._mark_vad_turn_end()

        cancellation = getattr(response, "tool_call_cancellation", None)
        if cancellation is not None:
            self._cancel_tool_calls(getattr(cancellation, "ids", None) or [], session)

        tool_call = getattr(response, "tool_call", None)
        if tool_call:
            self._finish_utterance(fallback=True)  # a stop word must beat any motion
            self._schedule_tool_calls(getattr(tool_call, "function_calls", None) or [], session)

        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            logger.debug(
                f"[Live] usage: prompt={getattr(usage, 'prompt_token_count', None)} "
                f"response={getattr(usage, 'response_token_count', None)} "
                f"total={getattr(usage, 'total_token_count', None)}"
            )

        sc = getattr(response, "server_content", None)
        if sc is None:
            return

        # Interruption (barge-in, or a client message cut the model off):
        # always drop whatever is still queued for playback.
        if getattr(sc, "interrupted", False):
            self._model_speaking = False
            await self._emit(self._on_interrupt)

        # Audio output
        model_turn = getattr(sc, "model_turn", None)
        parts = (getattr(model_turn, "parts", None) or []) if model_turn is not None else []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None) if inline is not None else None
            if data:
                # First audio chunk of the reply -> report response latency.
                await self._emit_response_latency_once()
                self._model_speaking = True  # echo gate (open mic)
                self._last_model_audio_ts = time.time()
                await self._emit(self._on_audio, data)

        # User transcription (what the user said)
        in_trans = getattr(sc, "input_transcription", None)
        if in_trans is not None:
            chunk = getattr(in_trans, "text", None)
            if chunk:
                self._input_transcript_buf += chunk
                self._utterance_buf += chunk
            if getattr(in_trans, "finished", False):
                if (
                    not self._model_speaking
                    and not self._latency_emitted
                    and not self._latency_start_locked
                ):
                    self._start_latency_timer(lock=True)
                self._finish_utterance()

        # Model transcription (what the model said, as text)
        out_trans = getattr(sc, "output_transcription", None)
        out_text = getattr(out_trans, "text", None) if out_trans is not None else None
        if out_text:
            await self._emit_response_latency_once()
            self._output_transcript_buf += out_text

        # Turn boundary — flush transcript buffers to callbacks
        if getattr(sc, "turn_complete", False):
            self._finish_utterance(fallback=True)
            self._model_speaking = False
            self._reset_latency()
            await self._flush_transcripts()

    async def _flush_transcripts(self):
        user_text = self._input_transcript_buf.strip()
        model_text = self._output_transcript_buf.strip()
        self._input_transcript_buf = ""
        self._output_transcript_buf = ""
        if user_text:
            await self._emit(self._on_text, "user", user_text)
        if model_text:
            await self._emit(self._on_text, "assistant", model_text)

    def _on_go_away(self, go_away, session):
        if session is None or self._go_away_session is session:
            return
        self._go_away_session = session
        time_left = _parse_duration_s(getattr(go_away, "time_left", None))
        logger.info(f"[Live] go_away received (time_left={getattr(go_away, 'time_left', None)})")
        self._spawn_bg(self._drain_then_reconnect(session, time_left), name="live_go_away")

    async def _drain_then_reconnect(self, session, time_left: Optional[float]):
        """Finish in-flight tools on this connection and wait for a resumable handle, within time_left."""
        loop = asyncio.get_running_loop()
        budget = (time_left if time_left is not None else _GO_AWAY_DEFAULT_S) - _GO_AWAY_MARGIN_S
        deadline = loop.time() + max(0.0, budget)

        pending = [
            rec.task for rec in list(self._tool_calls.values())
            if rec.session is session and rec.task is not None and not rec.task.done()
        ]
        if pending:
            await asyncio.wait(pending, timeout=max(0.0, deadline - loop.time()))
        if not self._resumable.is_set():
            # resumable is False while the model generates or runs tools.
            try:
                await asyncio.wait_for(self._resumable.wait(), timeout=max(0.0, deadline - loop.time()))
            except asyncio.TimeoutError:
                logger.warning("[Live] No resumable handle before the go_away deadline; reconnecting anyway")
        self._request_reconnect(session)

    # ── Stop-word fast path ──
    def _finish_utterance(self, fallback: bool = False):
        """
        End of a user utterance: if it was just a stop word, act now. `fallback`
        is used at turn/tool boundaries in case the transcript never sent
        finished=True; it must not cut a push-to-talk utterance that is still open.
        """
        if fallback and self._ptt_active:
            return
        utterance = self._utterance_buf.strip()
        self._utterance_buf = ""
        if not utterance:
            return
        action = match_stop_word(utterance)
        if not action:
            # Open mic has no push-to-talk press to signal new intent: a new
            # spoken instruction releases an earlier operator hold instead.
            if not self.push_to_talk and self.toolkit.operator_hold:
                logger.info(f"[Live] New spoken instruction {utterance!r} releases the operator hold")
                self.toolkit.clear_operator_hold()
            return
        logger.warning(f"[Live] Stop word {utterance!r} -> operator {action} (no LLM round-trip)")
        # Run on a dedicated thread right now (hover is an immediate UDP "stop"
        # that aborts an in-flight move) — not the shared default executor,
        # which busy tool calls could saturate. Tool calls that run after this
        # wait for it, so the operator hold is always set before they execute.
        loop = asyncio.get_running_loop()
        command = loop.create_future()

        def _resolve(result):
            loop.call_soon_threadsafe(lambda: command.done() or command.set_result(result))

        self.toolkit.operator_command_background(action, on_done=_resolve, source="voice_stop_word")
        self._stop_commands.add(command)
        command.add_done_callback(self._stop_commands.discard)
        self._spawn_bg(self._report_stop_word(action, utterance, command), name="live_stop_word")

    async def _report_stop_word(self, action: str, heard: str, command: asyncio.Future):
        verb = "landing" if action == "land" else "stopping and hovering"
        resume_hint = (
            "until you talk to it again" if self.push_to_talk
            else "until your next instruction (or type 'resume')"
        )
        await self._notify(f'Heard "{heard}" — {verb} now. AI motion is on hold {resume_hint}.')
        result = await command
        if not isinstance(result, dict) or not result.get("success"):
            error = result.get("error") if isinstance(result, dict) else result
            await self._notify(f"Operator {action} failed: {error}")

    # ── Tool calls ──
    def _schedule_tool_calls(self, function_calls, session):
        for fc in function_calls:
            name = getattr(fc, "name", None) or ""
            raw_args = getattr(fc, "args", None)
            args = dict(raw_args) if raw_args else {}
            call_id = getattr(fc, "id", None)
            key = call_id or f"_noid_{uuid.uuid4().hex[:8]}"
            if key in self._tool_calls:  # same id still running from an earlier connection
                key = f"{key}#{uuid.uuid4().hex[:8]}"
            rec = _ToolCall(key=key, call_id=call_id, name=name, args=args, session=session)
            self._tool_calls[key] = rec
            rec.task = asyncio.create_task(self._run_tool_call(rec), name=f"live_tool_{name}")

    def _cancel_tool_calls(self, ids, session):
        """Server-side cancellation. A connection can only cancel the calls it issued."""
        ids = set(ids)
        for rec in list(self._tool_calls.values()):
            if rec.call_id not in ids or rec.session is not session or rec.cancelled or rec.orphaned:
                continue  # unknown/finished, from another connection, or already handled
            rec.cancelled = True
            if not rec.started:
                if rec.task is not None:
                    rec.task.cancel()
                self._tool_calls.pop(rec.key, None)
                logger.info(f"[Live] Cancelled queued tool call {rec.name} ({rec.call_id})")
            elif rec.name in _ABORTABLE_TOOLS:
                self._spawn_bg(self._abort_motion(rec), name="live_abort_motion")
            else:
                logger.info(f"[Live] Tool call {rec.name} ({rec.call_id}) cancelled while running; result discarded")

    async def _abort_motion(self, rec: _ToolCall):
        logger.warning(f"[Live] {rec.name} ({rec.call_id}) cancelled mid-flight: sending hover")
        hover = asyncio.ensure_future(asyncio.to_thread(self.toolkit.hover, "voice_interrupt"))
        await self._notify(f"Interrupted — aborting {rec.name} and hovering.")
        result = await hover
        if not isinstance(result, dict) or not result.get("success"):
            error = result.get("error") if isinstance(result, dict) else result
            await self._notify(f"Hover after interruption failed: {error}")

    def _orphan_tool_calls(self, session) -> Optional[_LinkDrop]:
        """
        `session` is no longer the live connection. Its queued calls are cancelled
        (never executed); calls already executing finish — a move can't be undone —
        and keep their result for the notice sent on the next connection. Returns
        None when no call was affected. Synchronous, so nothing can start in between.
        """
        ran = [rec for rec in self._undelivered if rec.session is session]
        self._undelivered = [rec for rec in self._undelivered if rec.session is not session]
        skipped = []
        for rec in list(self._tool_calls.values()):
            if rec.session is not session or rec.cancelled or rec.orphaned:
                continue
            rec.orphaned = True
            if rec.started:
                ran.append(rec)
                continue
            skipped.append(rec)
            if rec.task is not None:
                rec.task.cancel()
            # A task cancelled before its first step never runs its finally.
            self._tool_calls.pop(rec.key, None)
        if not ran and not skipped:
            return None
        logger.warning(
            f"[Live] Connection dropped with tool calls pending: running={[_describe_call(r) for r in ran]} "
            f"cancelled={[_describe_call(r) for r in skipped]}"
        )
        return _LinkDrop(ran=ran, skipped=skipped)

    async def _report_link_drop(self, drop: _LinkDrop, to_model: bool):
        """Once the dropped connection's executing calls finish, tell the model (or the host) once."""
        running = [rec.task for rec in drop.ran if rec.task is not None and not rec.task.done()]
        if running:
            await asyncio.wait(running)
        parts = ["Voice link dropped mid-action."]
        if drop.ran:
            parts.append("Completed: " + "; ".join(
                f"{_describe_call(rec)} → {_describe_outcome(rec.outcome)}" for rec in drop.ran
            ) + ".")
        if drop.skipped:
            parts.append("Not executed (cancelled): " + "; ".join(_describe_call(rec) for rec in drop.skipped) + ".")
        try:
            status = await asyncio.to_thread(self.toolkit.safety.get_status_summary)
            parts.append(f"Current drone status: {status}.")
        except Exception as e:
            logger.debug(f"Status summary for the link-drop notice failed: {e}")
        parts.append("Do not repeat actions without checking.")
        text = " ".join(parts)
        if to_model:
            await self.send_system_notice(text)
        else:
            await self._notify(text)

    async def _run_tool_call(self, rec: _ToolCall):
        try:
            async with self._tool_lock:
                if self._stop_commands:
                    # A spoken stop word is being applied: let it set the operator
                    # hold first so this call can't slip a motion in before it.
                    await asyncio.wait(set(self._stop_commands))
                if rec.cancelled or rec.orphaned:
                    return
                rec.started = True
                self._executing += 1
                logger.info(f"[Live] Tool: {rec.name}({rec.args})")
                t0 = time.time()
                try:
                    result = await asyncio.to_thread(self.toolkit.execute, rec.name, rec.args)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"[Live] {rec.name} raised: {e}")
                    result = {"success": False, "error": str(e)}
                finally:
                    self._executing -= 1
                elapsed = time.time() - t0
                if not isinstance(result, dict):
                    result = {"success": True, "result": result}

                log_result = dict(result)
                if log_result.get("image_base64"):
                    # Base64 text would cost tens of thousands of tokens and the
                    # model can't see it as an image; the frame goes to video instead.
                    log_result.pop("image_base64")
                    log_result["note"] = "The latest camera frame was sent to your video input."
                logger.info(f"[Live] -> {log_result} ({elapsed * 1000:.0f}ms)")
                self._trace_tool(rec, log_result, t0, elapsed)
                await self._emit(self._on_tool, rec.name, rec.args, log_result)

                # Let the model see the result of a motion before it continues.
                if result.get("success") and rec.name in _FRAME_AFTER_TOOLS:
                    await self._push_frame(wait=True)

            await self._respond(rec, {**log_result, "drone": self._drone_summary()})
        finally:
            if self._tool_calls.get(rec.key) is rec:
                del self._tool_calls[rec.key]

    def _drone_summary(self) -> dict:
        """Compact current state, so the model never relies on facts evicted by compression."""
        try:
            state = self.toolkit.state.to_dict()
        except Exception:
            return {}
        keys = ("is_flying", "battery_pct", "height_cm", "heading_deg", "distance_from_home_cm", "operator_hold")
        return {k: state[k] for k in keys if k in state}

    async def _respond(self, rec: _ToolCall, payload: dict):
        """Send the result to the connection that issued the call — never to another one."""
        rec.outcome = payload
        if rec.cancelled:
            logger.info(f"[Live] Not responding to cancelled call {rec.name} ({rec.call_id})")
            return
        session = rec.session
        if rec.orphaned:
            logger.warning(f"[Live] {rec.name} finished after its connection dropped; reporting it on the new one")
            return
        if session is None or session is not self._session:
            logger.warning(f"[Live] Dropping {rec.name} response: the voice link was reconnected meanwhile")
            return
        if not rec.call_id:
            logger.error(f"[Live] {rec.name} call had no id; cannot send a tool response")
            return
        try:
            await session.send_tool_response(function_responses=[
                types.FunctionResponse(id=rec.call_id, name=rec.name, response={"result": payload}),
            ])
        except Exception as e:
            logger.error(f"send_tool_response error: {e}")
            if not rec.orphaned:
                # Most likely the connection is dying: report it once it's replaced.
                self._undelivered = [*self._undelivered[-9:], rec]

    def _trace_tool(self, rec: _ToolCall, log_result: dict, t0: float, elapsed: float):
        if not (self.tracer and self._session_trace_id):
            return
        try:
            from core.tracing import Span
            self.tracer.add_span(
                self._session_trace_id,
                Span(
                    span_id=uuid.uuid4().hex[:8],
                    trace_id=self._session_trace_id,
                    span_type="tool_call",
                    name=rec.name,
                    start_time=t0,
                    end_time=t0 + elapsed,
                    duration_ms=elapsed * 1000,
                    tool_name=rec.name,
                    tool_args=rec.args,
                    tool_result=log_result,
                    status="ok" if log_result.get("success", True) else "error",
                ),
            )
        except Exception as e:
            logger.debug(f"Trace add failed: {e}")


async def _maybe_await(result):
    """Await result if it's a coroutine, otherwise return as-is."""
    if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
        return await result
    return result
