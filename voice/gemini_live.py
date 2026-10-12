"""
Gemini Live API Client — gemini-3.1-flash-live-preview

Real-time bidirectional session with:
- Text input            (dashboard chat, CLI typing)
- Audio input           (mic -> 16 kHz PCM blobs)
- Video input           (drone camera -> 1 FPS JPEG blobs)
- Audio output          (Aoede voice, 24 kHz PCM)
- Input/Output transcription (for dashboard display + tracing)
- Synchronous tool calls against TelloToolkit

This replaces the slow multi-step text agent loop with a single persistent
WebSocket session. The model sees the drone's camera continuously, so there's
no separate vision API call — scene analysis happens natively.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import time
import uuid
from typing import Awaitable, Callable, Optional

from google import genai
from google.genai import types
from PIL import Image

from config import GEMINI_API_KEY, LIVE, MODELS, TOOL_SCHEMAS
from core.toolkit import TelloToolkit

logger = logging.getLogger(__name__)


VOICE_SYSTEM_PROMPT = (
    "You are TelloAI, a voice-controlled drone assistant flying a DJI Tello. "
    "You see the drone's camera as a continuous video feed — use it to answer "
    "questions about the environment and make flight decisions.\n\n"
    "Rules:\n"
    "- Keep spoken replies short and natural. No markdown, no bullet lists.\n"
    "- Confirm actions briefly: 'Taking off' not long explanations.\n"
    "- If battery drops below 20%, warn the user and land.\n"
    "- Never move more than 100 cm at a time without looking at the feed first. "
    "You have no depth sensor — judge distance from object size.\n"
    "- Stay within 8 meters of the takeoff point (enforced in software).\n"
    "- Altitude cap is 200 cm indoor.\n"
    "- For risky moves (flying through doorways, near people) confirm first.\n"
    "- On 'what do you see?' just describe the current feed — don't call any tool.\n"
)


# ── Callback types ──────────────────────────────────────────────────────────
AudioCallback = Callable[[bytes], Awaitable[None]] | Callable[[bytes], None]
TextCallback = Callable[[str, str], Awaitable[None]] | Callable[[str, str], None]  # (role, text)
ToolCallback = Callable[[str, dict, dict], Awaitable[None]] | Callable[[str, dict, dict], None]
                          # (name, args, result)


def _build_live_tools() -> list[dict]:
    """Convert TOOL_SCHEMAS to Live API tool declarations, filtering disabled tools."""
    disabled = set(LIVE.get("disabled_tools", []))
    declarations = []
    for schema in TOOL_SCHEMAS:
        if schema["name"] in disabled:
            continue
        props = schema["parameters"].get("properties", {})
        required = schema["parameters"].get("required", [])
        gemini_props = {}
        for prop_name, prop_def in props.items():
            gemini_prop = {"type": prop_def.get("type", "string").upper()}
            if "description" in prop_def:
                gemini_prop["description"] = prop_def["description"]
            if "enum" in prop_def:
                gemini_prop["enum"] = prop_def["enum"]
            gemini_props[prop_name] = gemini_prop

        decl = {
            "name": schema["name"],
            "description": schema["description"],
        }
        if gemini_props:
            decl["parameters"] = {
                "type": "OBJECT",
                "properties": gemini_props,
                "required": required,
            }
        declarations.append(decl)
    return [{"function_declarations": declarations}]


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


class LiveAgent:
    """
    Persistent Gemini Live API session that drives the drone.

    Lifecycle:
        agent = LiveAgent(toolkit)
        agent.on_audio(play_speaker)
        agent.on_text(dashboard_broadcast)
        await agent.start()
        await agent.send_text("take off")
        await agent.send_audio(pcm_chunk)
        ...
        await agent.stop()
    """

    def __init__(self, toolkit: TelloToolkit, tracer=None):
        self.toolkit = toolkit
        self.tracer = tracer
        self.client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
        self.model = MODELS["live"]

        self._session = None
        self._ctx = None  # async context manager from client.aio.live.connect
        self._running = False
        self._tasks: list[asyncio.Task] = []

        # Trace for this session (one trace covers the whole conversation)
        self._session_trace_id: Optional[str] = None

        # Transcription buffers (aggregate partial transcripts across a turn)
        self._input_transcript_buf: str = ""
        self._output_transcript_buf: str = ""

        # Half-duplex gate. While True, incoming mic audio is dropped so that
        # the model's own voice (bleeding from speakers back into the mic)
        # doesn't trigger false interruptions and echo-transcribe as user
        # speech. Set True when the model starts emitting audio; cleared on
        # turn_complete or flipped on genuine interruption.
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
        self._on_interrupt = cb

    def on_metric(self, cb: Callable):
        """cb(name, value) for lightweight telemetry (e.g. voice latency)."""
        self._on_metric = cb

    def _start_latency_timer(self, lock: bool):
        self._turn_user_ts = time.perf_counter()
        self._latency_emitted = False
        self._latency_start_locked = lock

    def mark_user_turn_end(self):
        """Start response latency timing when the user has finished input."""
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
        if self._on_metric:
            await _maybe_await(self._on_metric("voice_latency_ms", latency_ms))

    # ── Session lifecycle ──
    async def start(self):
        if self.client is None:
            raise RuntimeError("GEMINI_API_KEY not configured")
        if self._running:
            return

        # Build config
        config_dict: dict = {
            "response_modalities": ["AUDIO"],
            "system_instruction": VOICE_SYSTEM_PROMPT,
            "tools": _build_live_tools(),
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {"voice_name": LIVE["voice_name"]}
                }
            },
            "realtime_input_config": {
                "turn_coverage": LIVE["turn_coverage"],
            },
        }
        if LIVE.get("input_transcription"):
            config_dict["input_audio_transcription"] = {}
        if LIVE.get("output_transcription"):
            config_dict["output_audio_transcription"] = {}

        try:
            self._ctx = self.client.aio.live.connect(model=self.model, config=config_dict)
            self._session = await self._ctx.__aenter__()
            self._running = True
            logger.info(f"Live session opened with {self.model} (voice={LIVE['voice_name']})")
        except Exception as e:
            logger.error(f"Failed to open Live session: {e}")
            raise

        # Start trace
        if self.tracer:
            self._session_trace_id = self.tracer.start_trace(
                user_message="[Live session]",
                drone_state=self.toolkit.state.to_dict(),
            )

        # Kick off background loops
        self._tasks.append(asyncio.create_task(self._receive_loop(), name="live_recv"))
        self._tasks.append(asyncio.create_task(self._video_loop(), name="live_video"))

    async def stop(self):
        self._running = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

        if self._ctx is not None:
            try:
                await self._ctx.__aexit__(None, None, None)
            except Exception:
                pass
        self._session = None
        self._ctx = None

        if self.tracer and self._session_trace_id:
            self.tracer.end_trace(self._session_trace_id, final_response="[Live session closed]")
            self._session_trace_id = None

        logger.info("Live session closed")

    # ── Input side ──
    async def send_text(self, text: str):
        if self._session is None:
            return
        try:
            self._start_latency_timer(lock=True)
            await self._session.send_realtime_input(text=text)
            if self._on_text:
                await _maybe_await(self._on_text("user", text))
        except Exception as e:
            logger.error(f"send_text error: {e}")

    async def send_audio(self, pcm_chunk: bytes):
        if self._session is None or not pcm_chunk:
            return

        # Half-duplex: drop mic audio while the model is speaking so that
        # speaker output bleeding into the mic doesn't feed back as user
        # speech (causes choppy playback + ghost transcriptions).
        if self._model_speaking:
            return
        if self._last_model_audio_ts:
            since_last_ms = (time.time() - self._last_model_audio_ts) * 1000
            if since_last_ms < self._echo_guard_ms:
                return

        try:
            await self._session.send_realtime_input(
                audio=types.Blob(
                    data=pcm_chunk,
                    mime_type=f"audio/pcm;rate={LIVE['input_sample_rate']}",
                )
            )
            if not self._model_speaking and not self._latency_emitted:
                self._last_user_audio_ts = time.perf_counter()
        except Exception as e:
            logger.debug(f"send_audio error: {e}")

    async def send_video_frame(self, jpeg_bytes: bytes):
        if self._session is None or not jpeg_bytes:
            return
        try:
            await self._session.send_realtime_input(
                video=types.Blob(data=jpeg_bytes, mime_type="image/jpeg")
            )
        except Exception as e:
            logger.debug(f"send_video error: {e}")

    # ── Background: pump drone video at LIVE["video_fps"] ──
    async def _video_loop(self):
        interval = 1.0 / max(LIVE.get("video_fps", 1.0), 0.1)
        while self._running:
            try:
                frame_result = await asyncio.get_event_loop().run_in_executor(
                    None, self.toolkit.get_frame
                )
                if frame_result.get("success"):
                    b64 = frame_result.get("image_base64", "")
                    if b64:
                        jpeg = _encode_frame_for_live(b64)
                        if jpeg:
                            await self.send_video_frame(jpeg)
            except Exception as e:
                logger.debug(f"Video loop error: {e}")
            await asyncio.sleep(interval)

    # ── Background: receive from session ──
    async def _receive_loop(self):
        try:
            while self._running:
                async for response in self._session.receive():
                    if not self._running:
                        break
                    await self._handle_response(response)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self._running:
                logger.error(f"Live receive loop error: {e}")

    async def _handle_response(self, response):
        voice_activity = getattr(response, "voice_activity", None)
        if voice_activity:
            activity_type = self._enum_value(getattr(voice_activity, "voice_activity_type", ""))
            if activity_type == "ACTIVITY_END":
                self._mark_vad_turn_end()

        vad_signal = getattr(response, "voice_activity_detection_signal", None)
        if vad_signal:
            signal_type = self._enum_value(getattr(vad_signal, "vad_signal_type", ""))
            if signal_type == "VAD_SIGNAL_TYPE_EOS":
                self._mark_vad_turn_end()

        # Tool calls
        tc = getattr(response, "tool_call", None)
        if tc:
            await self._run_tool_calls(tc.function_calls)
            return

        # Server content: audio, transcripts, turn_complete, interruption
        sc = getattr(response, "server_content", None)
        if sc is None:
            return

        # Audio output
        model_turn = getattr(sc, "model_turn", None)
        if model_turn and getattr(model_turn, "parts", None):
            for part in model_turn.parts:
                inline = getattr(part, "inline_data", None)
                if inline and inline.data:
                    # First audio chunk of the reply -> report response latency.
                    await self._emit_response_latency_once()
                    # Half-duplex gate: model is actively speaking, ignore mic
                    self._model_speaking = True
                    self._last_model_audio_ts = time.time()
                    if self._on_audio:
                        await _maybe_await(self._on_audio(inline.data))

        # User transcription (what the user said)
        in_trans = getattr(sc, "input_transcription", None)
        if in_trans and getattr(in_trans, "text", None):
            self._input_transcript_buf += in_trans.text
            if (
                getattr(in_trans, "finished", False)
                and not self._model_speaking
                and not self._latency_emitted
                and not self._latency_start_locked
            ):
                self._start_latency_timer(lock=True)

        # Model transcription (what the model said, as text)
        out_trans = getattr(sc, "output_transcription", None)
        if out_trans and getattr(out_trans, "text", None):
            await self._emit_response_latency_once()
            self._output_transcript_buf += out_trans.text

        # Interruption (user spoke over the model). With the half-duplex gate
        # on send_audio, genuine user barge-in is rare; most interruption
        # events during model playback are acoustic echo and would only cause
        # choppy output if we flushed the speaker. Only honor interruptions
        # that happen when the model isn't currently emitting audio.
        if getattr(sc, "interrupted", False):
            if not self._model_speaking and self._on_interrupt:
                try:
                    self._on_interrupt()
                except Exception:
                    pass

        # Turn boundary — flush transcript buffers to callbacks
        if getattr(sc, "turn_complete", False):
            self._model_speaking = False
            self._latency_emitted = False
            self._latency_start_locked = False
            self._turn_user_ts = 0.0
            self._last_user_audio_ts = 0.0
            if self._input_transcript_buf.strip() and self._on_text:
                await _maybe_await(self._on_text("user", self._input_transcript_buf.strip()))
            if self._output_transcript_buf.strip() and self._on_text:
                await _maybe_await(self._on_text("assistant", self._output_transcript_buf.strip()))
            self._input_transcript_buf = ""
            self._output_transcript_buf = ""

    async def _run_tool_calls(self, function_calls):
        """Execute toolkit functions synchronously and return results to the session."""
        responses = []
        for fc in function_calls:
            fn_name = fc.name
            fn_args = dict(fc.args) if fc.args else {}
            logger.info(f"[Live] Tool: {fn_name}({fn_args})")

            t0 = time.time()
            # Toolkit.execute is synchronous — run in executor to avoid blocking the loop
            result = await asyncio.get_event_loop().run_in_executor(
                None, self.toolkit.execute, fn_name, fn_args
            )
            elapsed = time.time() - t0

            # Strip base64 for logging
            log_result = result.copy() if isinstance(result, dict) else result
            if isinstance(log_result, dict) and "image_base64" in log_result and log_result["image_base64"]:
                log_result["image_base64"] = f"<base64_omitted_len_{len(log_result['image_base64'])}>"
            logger.info(f"[Live] -> {log_result} ({elapsed*1000:.0f}ms)")

            # Trace it
            if self.tracer and self._session_trace_id:
                try:
                    from core.tracing import Span
                    self.tracer.add_span(
                        self._session_trace_id,
                        Span(
                            span_id=uuid.uuid4().hex[:8],
                            trace_id=self._session_trace_id,
                            span_type="tool_call",
                            name=fn_name,
                            start_time=t0,
                            end_time=t0 + elapsed,
                            duration_ms=elapsed * 1000,
                            tool_name=fn_name,
                            tool_args=fn_args,
                            tool_result=log_result,
                            status="error" if isinstance(result, dict) and not result.get("success", True) else "ok",
                        ),
                    )
                except Exception as e:
                    logger.debug(f"Trace add failed: {e}")

            # External tool callback (for dashboard)
            if self._on_tool:
                try:
                    await _maybe_await(self._on_tool(fn_name, fn_args, log_result))
                except Exception as e:
                    logger.debug(f"on_tool callback error: {e}")

            responses.append(
                types.FunctionResponse(
                    id=getattr(fc, "id", None),
                    name=fn_name,
                    response={"result": result},
                )
            )

        try:
            await self._session.send_tool_response(function_responses=responses)
        except Exception as e:
            logger.error(f"send_tool_response error: {e}")


async def _maybe_await(result):
    """Await result if it's a coroutine, otherwise return as-is."""
    if asyncio.iscoroutine(result):
        return await result
    return result
