"""
Agent Core

The main orchestration loop:
1. User sends message (text or dashboard)
2. LLM reasons about what to do
3. LLM calls toolkit functions (executed in a worker thread — drone I/O blocks)
4. Results fed back to LLM. get_frame results carry the JPEG itself as a
   multimodal function response, so the planner looks at the camera directly.
5. LLM responds to user

Uses MODELS["vision_reasoning"] (Gemini 3.8 Flash) with function calling.

History rules (Gemini 3 thought signatures):
- Model turns are appended verbatim (they carry the thought signatures).
- Every function response echoes the id and name of its call.
- After a turn completes, camera images in that turn's function responses are
  replaced by a short text note so old frames aren't re-sent on every request.
  Only function_response parts are rewritten; model content is never touched.
- History keeps the last MAX_HISTORY_TURNS user turns, cut only at user-text
  boundaries so call/response pairs and signatures stay intact.
- If a turn fails, history is rolled back to before that user message.
"""

import asyncio
import base64
import binascii
import json
import logging
import threading
import time
import uuid
from typing import Callable, Optional

from google.genai import types

from config import MODELS, THINKING, TOOL_SCHEMAS, VISION
from core.cost import calculate_cost
from core.genai_client import make_client
from core.toolkit import TelloToolkit, parse_operator_text
from core.tracing import Span
from vision.llm_vision import make_thumbnail_b64, resolve_media_resolution

logger = logging.getLogger(__name__)

# Max model calls per user message before giving up.
MAX_ITERATIONS = 15
# User turns kept in conversation history (older turns are dropped whole).
MAX_HISTORY_TURNS = 10
# Caps thinking + visible output combined; medium thinking needs headroom.
MAX_OUTPUT_TOKENS = 4096
# Replaces a camera image in a finished turn's function response.
FRAME_DROPPED_NOTE = "Camera image from an earlier turn was removed to save tokens. Call get_frame for a current view."
TIMEOUT_MESSAGE = "I've been thinking too long — stopping here for safety. Please give me a simpler instruction."

SYSTEM_PROMPT = """You are TelloAI, an AI agent controlling a DJI Tello drone indoors. You can fly, see through the drone's camera, and talk to the user.

## Context provided automatically
Every user message starts with a status line:
[Drone Status: Battery: X% | Distance from home: Y/800cm | Altitude: Z/200cm | Heading: H° | Flying: True/False]
It may also end with "OPERATOR HOLD" (a human stopped the drone) or "LINK LOST" (no telemetry from the drone: do not fly, tell the user).
Use these values directly — do NOT call get_battery(), get_height() or get_speed() just to read status you already have.
Only call sensor tools if you need a live refresh mid-flight after extended operations.

## Seeing
- get_frame() returns the actual camera image attached to the tool result. Look at it yourself: judge obstacles and distances, find objects, describe the scene.
- Images from earlier turns are removed from the conversation to save cost. Call get_frame() again whenever you need a current view.
- You have NO depth sensor. Estimate distance from object size in the frame. When in doubt, move 20–30cm at a time.

## Flight rules (enforced in code)
- Each move is capped at 100cm per call; longer requests are clamped to 100cm. Call get_frame() and look before every horizontal move — it is your only obstacle avoidance.
- Never exceed 8m (800cm) from the takeoff point or 200cm altitude. Moves past these limits are refused.
- If battery is below 20%, tell the user and land. At 10% the drone force-lands automatically.
- Do NOT take off if already flying. Do NOT land if already on the ground.
- An "Operator hold" error means a human operator stopped the drone. Do NOT retry or work around it — stop moving, tell the user what happened, and wait for their next instruction.
- You have no emergency-stop or flip tools; those are operator-only controls. If the user asks for one, tell them to use the operator controls (dashboard buttons or typed 'stop' / 'land' / '!estop').

## Safe action protocol
- Confirm risky or irreversible actions first: "I'm about to fly through the doorway — should I proceed?"
- If unsure about the environment or next step, stay where you are (the drone hovers on its own) and ask the user.
- If a tool returns an error, don't blindly retry. Explain it and adapt.

## Exploration strategy (for "find X" tasks)
1. Scan the current room: rotate 360° in six 60° steps and call get_frame() at each step.
2. Note objects, doors and openings you see in each frame.
3. If the target isn't found, pick the most promising opening, turn to face it, and approach in short moves with a get_frame() check before each one.
4. Report what you see to the user as you go.

## Response style
- During flight, keep responses short and actionable.
- After executing actions, briefly confirm what happened.
- Describe interesting things you see naturally (no need to list every detail).
- When a full task completes, give a concise summary of what was done.
"""


def _build_gemini_tools() -> list[types.Tool]:
    """Convert our tool schemas to Gemini function declarations."""
    declarations = []
    for schema in TOOL_SCHEMAS:
        props = schema["parameters"].get("properties", {})
        required = schema["parameters"].get("required", [])

        # Convert to Gemini schema format
        gemini_props = {}
        for prop_name, prop_def in props.items():
            prop_type = prop_def.get("type", "string").upper()
            gemini_prop = {"type": prop_type}
            if "description" in prop_def:
                gemini_prop["description"] = prop_def["description"]
            if "enum" in prop_def:
                gemini_prop["enum"] = prop_def["enum"]
            gemini_props[prop_name] = gemini_prop

        decl = types.FunctionDeclaration(
            name=schema["name"],
            description=schema["description"],
            parameters=(
                {
                    "type": "OBJECT",
                    "properties": gemini_props,
                    "required": required,
                }
                if gemini_props
                else None
            ),
        )
        declarations.append(decl)

    return [types.Tool(function_declarations=declarations)]


# ── History helpers ──

def _is_user_text_turn(content: types.Content) -> bool:
    """True for a real user message (text), False for function-response turns."""
    if content.role != "user" or not content.parts:
        return False
    has_text = any(part.text for part in content.parts)
    has_fr = any(part.function_response is not None for part in content.parts)
    return has_text and not has_fr


def _strip_frames(content: types.Content) -> types.Content:
    """
    Return content with images removed from its function responses.

    Only function_response parts of user-role content are rewritten (id and
    name are kept). Model content — which carries thought signatures — is
    returned untouched. Builds new objects instead of mutating in place.
    """
    if content.role != "user" or not content.parts:
        return content
    changed = False
    new_parts = []
    for part in content.parts:
        fr = part.function_response
        if fr is not None and fr.parts:
            response = dict(fr.response or {})
            response["image"] = FRAME_DROPPED_NOTE
            part = part.model_copy(update={
                "function_response": fr.model_copy(update={"parts": None, "response": response}),
            })
            changed = True
        new_parts.append(part)
    return content.model_copy(update={"parts": new_parts}) if changed else content


def _decode_jpeg(image_b64) -> Optional[bytes]:
    """Decode the toolkit's base64 JPEG; None if missing or malformed."""
    if not image_b64:
        return None
    try:
        data = base64.b64decode(image_b64)
    except (binascii.Error, ValueError, TypeError):
        return None
    return data or None


def _function_response_part(fc: types.FunctionCall, response: dict, image: Optional[bytes] = None) -> types.Part:
    """Build a function response that echoes the call's id/name, with an optional JPEG."""
    parts = None
    if image:
        parts = [
            types.FunctionResponsePart(
                inline_data=types.FunctionResponseBlob(mime_type="image/jpeg", data=image)
            )
        ]
    return types.Part(
        function_response=types.FunctionResponse(id=fc.id, name=fc.name, response=response, parts=parts)
    )


def _summarize_contents(contents: list[types.Content]) -> list[dict]:
    """Compact, JSON-serializable view of the request for trace spans."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for content in contents:
        parts_text = []
        for part in content.parts or []:
            if part.text:
                if not part.thought:
                    parts_text.append(part.text)
            elif part.function_call:
                parts_text.append(f"[Call: {part.function_call.name}]")
            elif part.function_response:
                fr = part.function_response
                suffix = f" +{len(fr.parts)} image" if fr.parts else ""
                parts_text.append(f"[Return: {fr.name}{suffix}]")
        messages.append({"role": content.role or "unknown", "content": " | ".join(parts_text)})
    return messages


def _read_lines(input_fn: Callable[[], str], loop: asyncio.AbstractEventLoop, queue: asyncio.Queue):
    """Input thread for run_interactive: forward typed lines; None means EOF."""
    while True:
        try:
            line = input_fn()
        except (EOFError, KeyboardInterrupt):
            line = None
        except Exception as e:  # pragma: no cover - defensive
            logger.error(f"Input error: {e}")
            line = None
        try:
            loop.call_soon_threadsafe(queue.put_nowait, line)
        except RuntimeError:  # event loop already closed
            return
        if line is None:
            return


class TelloAgent:
    """Main agent that orchestrates LLM ↔ drone interaction."""

    def __init__(self, toolkit: TelloToolkit, tracer=None):
        self.toolkit = toolkit
        self.tracer = tracer
        # Retries transient 429/5xx (e.g. 503 "high demand") with backoff.
        self.client = make_client()
        self.conversation_history: list[types.Content] = []
        self.tools = _build_gemini_tools()
        self.media_resolution = resolve_media_resolution(VISION.get("media_resolution"))
        # One message at a time: concurrent dashboard commands would otherwise
        # interleave calls and responses on the same history.
        self._lock = asyncio.Lock()
        self._running = False

    # ── Message processing ──

    async def process_message(self, user_message: str, release_hold: bool = True) -> str:
        """
        Process a user message through the agent loop.
        Handles multi-turn function calling until LLM gives a final text response.
        Calls are serialized: a second message waits for the first to finish.

        release_hold=False keeps an operator hold in place (camera-only requests
        such as the dashboard's Observe button must not lift a human STOP).
        """
        # Remember the stop counter when the message ARRIVES: if the operator
        # presses STOP while this message waits behind a running turn, the
        # message predates that STOP and must not release its hold.
        arrival_epoch = self.toolkit.stop_epoch
        async with self._lock:
            return await self._process_message_locked(user_message, release_hold, arrival_epoch)

    async def _process_message_locked(
        self, user_message: str, release_hold: bool = True, arrival_epoch: Optional[int] = None,
    ) -> str:
        if self.client is None:
            return "Error: GEMINI_API_KEY not configured. Set it in .env file."

        # A new instruction from the user releases an earlier operator hold —
        # only now, when this turn starts (a turn still running from before
        # the STOP keeps being refused), and only if no STOP came after it.
        stopped_since_sent = arrival_epoch is not None and self.toolkit.stop_epoch != arrival_epoch
        hold_released = release_hold and not stopped_since_sent and self.toolkit.operator_hold
        if release_hold and not stopped_since_sent:
            self.toolkit.clear_operator_hold()

        state_ctx = self.toolkit.safety.get_status_summary()
        if hold_released:
            note = "\n[Note: the earlier operator hold is released by this new instruction.]"
        elif stopped_since_sent and self.toolkit.operator_hold:
            note = "\n[Note: the operator stopped the drone AFTER this message was sent. Do not move; tell the user.]"
        else:
            note = ""
        augmented_message = f"[Drone Status: {state_ctx}]{note}\n\nUser: {user_message}"

        trace_id = None
        if self.tracer:
            trace_id = self.tracer.start_trace(user_message, self.toolkit.state.to_dict())

        turn_start = len(self.conversation_history)
        self.conversation_history.append(
            types.Content(role="user", parts=[types.Part.from_text(text=augmented_message)])
        )

        try:
            final_text = await self._run_turn(trace_id)
        except asyncio.CancelledError:
            self._rollback(turn_start)
            self._end_trace(trace_id, "Cancelled")
            raise
        except Exception as e:
            logger.error(f"Agent turn failed: {e}")
            self._rollback(turn_start)
            error_msg = f"Sorry, I encountered an error: {e}"
            self._end_trace(trace_id, error_msg)
            return error_msg

        self._compact_history(turn_start)
        self._end_trace(trace_id, final_text)
        return final_text

    async def _run_turn(self, trace_id: Optional[str]) -> str:
        """Model ↔ tool loop for the current turn. Raises on API errors."""
        for _ in range(MAX_ITERATIONS):
            response = await self._call_model(trace_id)

            candidate = response.candidates[0] if response.candidates else None
            content = candidate.content if candidate is not None else None
            if content is None or not content.parts:
                reason = getattr(candidate, "finish_reason", None) if candidate is not None else None
                raise RuntimeError(f"No response from model{f' (finish reason: {reason})' if reason else ''}.")

            # Keep the model turn verbatim: it carries the thought signatures.
            self.conversation_history.append(content)

            function_calls = [part.function_call for part in content.parts if part.function_call]
            if not function_calls:
                text_parts = [part.text for part in content.parts if part.text and not part.thought]
                return "\n".join(text_parts) if text_parts else "Done."

            # All responses for this step go in one user turn, in call order.
            responses = []
            for fc in function_calls:
                responses.append(await self._execute_tool(fc, trace_id))
            self.conversation_history.append(types.Content(role="user", parts=responses))

        # Close the turn with a model message so the next user message doesn't
        # follow a dangling tool-response turn. Plain text needs no signature.
        self.conversation_history.append(
            types.Content(role="model", parts=[types.Part.from_text(text=TIMEOUT_MESSAGE)])
        )
        return TIMEOUT_MESSAGE

    async def _call_model(self, trace_id: Optional[str]):
        contents = list(self.conversation_history)
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=self.tools,
            thinking_config=types.ThinkingConfig(
                thinking_level=THINKING["reasoning"],
                include_thoughts=True,
            ),
            media_resolution=self.media_resolution,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )

        start_time = time.time()
        response = await self.client.aio.models.generate_content(
            model=MODELS["vision_reasoning"],
            contents=contents,
            config=config,
        )
        elapsed = time.time() - start_time
        logger.info(f"Gemini API (aio.models.generate_content) took {elapsed:.2f}s")

        if self.tracer and trace_id:
            self._trace_llm_call(trace_id, contents, response, start_time, elapsed)
        return response

    async def _execute_tool(self, fc: types.FunctionCall, trace_id: Optional[str]) -> types.Part:
        """Run one tool call off the event loop and build its function response."""
        fn_name = fc.name
        fn_args = dict(fc.args) if fc.args else {}

        logger.info(f"Executing: {fn_name}({fn_args})")
        fn_start_time = time.time()
        # Drone I/O blocks (UDP round-trips, multi-second moves): never on the loop.
        result = await asyncio.to_thread(self.toolkit.execute, fn_name, fn_args)
        fn_elapsed = time.time() - fn_start_time

        if not isinstance(result, dict):
            result = {"success": True, "result": result}

        # Never put base64 in the response dict: the image travels as bytes.
        response = {k: v for k, v in result.items() if k != "image_base64"}
        image = None
        if fn_name == "get_frame" and result.get("success"):
            image = _decode_jpeg(result.get("image_base64"))
            if image is None:
                response = {"success": False, "error": "Camera returned no usable image."}
            else:
                response["message"] = "Frame captured. The camera image is attached to this result."

        log_result = dict(response)
        if image is not None:
            log_result["image"] = f"<jpeg {len(image)} bytes>"
        logger.info(f"Result (took {fn_elapsed:.2f}s): {log_result}")

        if self.tracer and trace_id:
            self.tracer.add_span(trace_id, Span(
                span_id=uuid.uuid4().hex[:8],
                trace_id=trace_id,
                span_type="tool_call",
                name=fn_name,
                start_time=fn_start_time,
                end_time=fn_start_time + fn_elapsed,
                duration_ms=fn_elapsed * 1000,
                tool_name=fn_name,
                tool_args=fn_args,
                tool_result=log_result,
                frame_thumbnail_b64=make_thumbnail_b64(image) if image else None,
                status="ok" if response.get("success", True) else "error",
            ))

        return _function_response_part(fc, response, image)

    # ── History management ──

    def _rollback(self, turn_start: int):
        """Drop everything from the failed turn (no dangling call/response pairs)."""
        del self.conversation_history[turn_start:]

    def _compact_history(self, turn_start: int):
        """After a turn: drop its images, then keep only recent user turns."""
        history = self.conversation_history
        for i in range(turn_start, len(history)):
            history[i] = _strip_frames(history[i])

        user_turns = [i for i, content in enumerate(history) if _is_user_text_turn(content)]
        keep = max(1, MAX_HISTORY_TURNS)
        if len(user_turns) > keep:
            del history[:user_turns[-keep]]

    def reset_conversation(self):
        """Clear conversation history."""
        self.conversation_history = []

    # ── Tracing ──

    def _trace_llm_call(self, trace_id: str, contents, response, start_time: float, elapsed: float):
        serializable_output = []
        thought_parts = []
        candidates = response.candidates or []
        content = candidates[0].content if candidates else None
        for part in (content.parts if content and content.parts else []):
            if part.text:
                if part.thought:
                    thought_parts.append(part.text)
                else:
                    serializable_output.append({"text": part.text})
            elif part.function_call:
                args = dict(part.function_call.args) if part.function_call.args else {}
                serializable_output.append({"function_call": part.function_call.name, "args": args})

        usage = response.usage_metadata
        tokens_in = getattr(usage, "prompt_token_count", 0) or 0
        tokens_out = getattr(usage, "candidates_token_count", 0) or 0
        thinking_tokens = getattr(usage, "thoughts_token_count", 0) or 0
        model = MODELS["vision_reasoning"]

        self.tracer.add_span(trace_id, Span(
            span_id=uuid.uuid4().hex[:8],
            trace_id=trace_id,
            span_type="llm_call",
            name=model,
            start_time=start_time,
            end_time=start_time + elapsed,
            duration_ms=elapsed * 1000,
            model=model,
            thinking_level=THINKING["reasoning"],
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            thinking_tokens=thinking_tokens,
            cost_usd=calculate_cost(model, tokens_in, tokens_out, thinking_tokens),
            input_messages=_summarize_contents(contents),
            output_message=serializable_output,
            model_parameters={
                "thinking_level": THINKING["reasoning"],
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "media_resolution": self.media_resolution.value if self.media_resolution else None,
            },
            thought_summary="\n\n".join(thought_parts) if thought_parts else None,
        ))

    def _end_trace(self, trace_id: Optional[str], final_text: str):
        if self.tracer and trace_id:
            self.tracer.end_trace(trace_id, final_text)

    # ── Interactive CLI ──

    async def run_interactive(self, input_fn: Callable[[], str] = input):
        """
        Interactive text-based agent loop (Phase 1 testing).

        Input is read on a background thread, so typed operator commands
        ('stop', 'land', '!estop', 'resume') run immediately — even while the
        agent is still working on a request — without going through the LLM.
        """
        print("\n" + "=" * 60)
        print("  TelloAI Agent — Interactive Mode")
        print("  Type commands. 'quit' to land and exit, 'reset' to clear history, 'status' for state.")
        print("  Typed 'stop' / 'land' / 'resume' act instantly (no AI); '!estop' cuts the motors.")
        print("=" * 60)

        loop = asyncio.get_running_loop()
        lines: asyncio.Queue = asyncio.Queue()
        threading.Thread(
            target=_read_lines, args=(input_fn, loop, lines), name="cli-input", daemon=True
        ).start()

        self._running = True
        pending: Optional[asyncio.Task] = None
        operator_threads: list[threading.Thread] = []
        _prompt()
        try:
            while self._running:
                line = await lines.get()
                if line is None:  # EOF: let the current request finish, then exit
                    if pending is not None:
                        await pending
                    print("\nExiting...")
                    break

                text = line.strip()
                if not text:
                    continue

                action = parse_operator_text(text)
                if action:
                    # Fire on its own thread and keep reading input: a typed
                    # '!estop' must never wait behind a 'land' that is still
                    # waiting for the drone.
                    operator_threads.append(self.toolkit.operator_command_background(
                        action,
                        on_done=lambda r: print(f"\n  [operator] {r.get('message') or r.get('error')}", flush=True),
                    ))
                    continue

                lowered = text.lower()
                if lowered in ("quit", "exit"):
                    break
                if lowered == "status":
                    print(f"State: {json.dumps(self.toolkit.state.to_dict(), indent=2)}")
                    if pending is None or pending.done():
                        _prompt()
                    continue
                if pending is not None and not pending.done():
                    print("  Still working on the last request. Type 'stop' or 'land' to stop the drone.")
                    continue
                if lowered == "reset":
                    self.reset_conversation()
                    print("Conversation history cleared.")
                    _prompt()
                    continue

                pending = asyncio.create_task(self._reply(text))
        finally:
            self._running = False
            if pending is not None and not pending.done():
                pending.cancel()
                try:
                    await pending
                except asyncio.CancelledError:
                    pass
            # Let operator commands (e.g. a typed land) finish before exiting.
            for thread in operator_threads:
                await asyncio.to_thread(thread.join, 30)
            if self.toolkit.state.is_flying:
                print("Landing drone before exit...")
                result = await asyncio.to_thread(self.toolkit.operator_command, "land")
                print(f"  [operator] {result.get('message') or result.get('error')}")

    async def _reply(self, text: str):
        response = await self.process_message(text)
        print(f"\nTelloAI: {response}")
        _prompt()


def _prompt():
    print("\nYou: ", end="", flush=True)
