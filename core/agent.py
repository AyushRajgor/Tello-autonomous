"""
Agent Core

The main orchestration loop:
1. User sends message (text or voice)
2. LLM reasons about what to do
3. LLM calls toolkit functions
4. Results fed back to LLM
5. LLM responds to user

Uses Gemini 3.5 Flash with function calling for the agent loop.
Vision calls are routed through vision/llm_vision.py.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Optional

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, MODELS, THINKING, TOOL_SCHEMAS
from core.toolkit import TelloToolkit
from core.state import DroneState

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are TelloAI, an AI agent controlling a DJI Tello drone. You can fly, see, and talk to the user in real-time.

## Context provided automatically
Every user message starts with [Drone Status: Battery: X% | Distance from home: Y/800cm | Altitude: Z/200cm | Flying: true/false].
Use these values directly — do NOT call get_battery(), get_height(), or get_speed() just to read status you already have.
Only call sensor tools (get_battery, get_height, etc.) if you need a live mid-flight refresh after extended operations.

## Flight rules
- If battery is below 20% (shown in Drone Status), warn the user and land immediately.
- NEVER move more than 100cm in a single burst without calling get_frame() first. This is your only obstacle avoidance — move in short increments, check the frame, then move again.
- Do NOT take off if already flying. Do NOT land if already on the ground.
- Never exceed 8m (800cm) from the takeoff point — tracked in Drone Status.
- You have NO depth sensor. Estimate distance from object size in the frame. When in doubt, move 20–30cm at a time.
- Altitude cap is 200cm. Never move up if already at or near that limit.

## Safe action protocol
- Always confirm destructive or irreversible actions before executing: "I'm about to fly through the doorway — should I proceed?"
- If unsure about the environment or next step, hover and ask the user.
- For flips: only attempt if battery > 50%.

## Exploration strategy (for "find X" tasks)
1. Scan current room: rotate 360° in six 60° steps, call get_frame() at each step.
2. Build a mental map: note objects, doors, and openings from each frame.
3. If target not found, choose the most promising exit and navigate through it.
4. Repeat in the new room. Report what you see to the user throughout.

## Response style
- During flight, keep responses short and actionable.
- After executing actions, briefly confirm what happened.
- Describe interesting frame observations naturally (no need to list every pixel).
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


class TelloAgent:
    """Main agent that orchestrates LLM ↔ drone interaction."""

    def __init__(self, toolkit: TelloToolkit, tracer=None):
        self.toolkit = toolkit
        self.tracer = tracer
        self.client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
        self.conversation_history: list[types.Content] = []
        self.tools = _build_gemini_tools()
        self._running = False

    async def process_message(self, user_message: str) -> str:
        """
        Process a user message through the agent loop.
        Handles multi-turn function calling until LLM gives a final text response.
        """
        if self.client is None:
            return "Error: GEMINI_API_KEY not configured. Set it in .env file."

        # Add drone state context to the user message
        state_ctx = self.toolkit.safety.get_status_summary()
        drone_state = self.toolkit.state.to_dict()
        augmented_message = f"[Drone Status: {state_ctx}]\n\nUser: {user_message}"

        trace_id = None
        if self.tracer:
            trace_id = self.tracer.start_trace(user_message, drone_state)

        # Add user message to history
        self.conversation_history.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=augmented_message)],
            )
        )

        # Agent loop — keep going until we get a text response (not a function call)
        max_iterations = 15  # Safety limit
        iteration = 0

        while iteration < max_iterations:
            iteration += 1

            try:
                start_time = time.time()
                
                # Build serializable input messages (system prompt shown first for trace clarity)
                serializable_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                contents_list = list(self.conversation_history)
                for content in contents_list:
                    role = getattr(content, "role", "unknown")
                    parts_text = []
                    for p in content.parts:
                        if hasattr(p, "text") and p.text:
                            if not (hasattr(p, "thought") and p.thought):
                                parts_text.append(p.text)
                        elif hasattr(p, "function_call") and p.function_call:
                            parts_text.append(f"[Call: {p.function_call.name}]")
                        elif hasattr(p, "function_response") and p.function_response:
                            parts_text.append(f"[Return: {p.function_response.name}]")
                    serializable_messages.append({"role": role, "content": " | ".join(parts_text)})

                model_params = {
                    "temperature": 1.0,
                    "thinking_level": THINKING["reasoning"],
                    "max_output_tokens": 2048,
                }

                response = await self.client.aio.models.generate_content(
                    model=MODELS["vision_reasoning"],
                    contents=contents_list,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        tools=self.tools,
                        thinking_config=types.ThinkingConfig(
                            thinking_level=THINKING["reasoning"],
                            include_thoughts=True,
                        ),
                        temperature=1.0,
                        max_output_tokens=2048,
                    ),
                )
                elapsed = time.time() - start_time
                logger.info(f"Gemini API (aio.models.generate_content) took {elapsed:.2f}s")
                
                # Build serializable output message; also collect thought summary
                serializable_output = []
                thought_parts = []
                if hasattr(response, "candidates") and response.candidates:
                    cand = response.candidates[0]
                    if hasattr(cand, "content") and cand.content and hasattr(cand.content, "parts"):
                        for p in cand.content.parts:
                            if hasattr(p, "text") and p.text:
                                if hasattr(p, "thought") and p.thought:
                                    thought_parts.append(p.text)
                                else:
                                    serializable_output.append({"text": p.text})
                            elif hasattr(p, "function_call") and p.function_call:
                                fn_args = dict(p.function_call.args) if p.function_call.args else {}
                                serializable_output.append({"function_call": p.function_call.name, "args": fn_args})
                thought_summary = "\n\n".join(thought_parts) if thought_parts else None

                if self.tracer and trace_id:
                    from core.tracing import Span
                    from core.cost import calculate_cost
                    tokens_in = 0
                    tokens_out = 0
                    thinking_tokens = 0
                    if hasattr(response, 'usage_metadata') and response.usage_metadata:
                        tokens_in = getattr(response.usage_metadata, 'prompt_token_count', 0)
                        tokens_out = getattr(response.usage_metadata, 'candidates_token_count', 0)
                        thinking_tokens = getattr(response.usage_metadata, 'thoughts_token_count', 0)
                    cost = calculate_cost(MODELS["vision_reasoning"], tokens_in, tokens_out, thinking_tokens)
                    self.tracer.add_span(trace_id, Span(
                        span_id=uuid.uuid4().hex[:8],
                        trace_id=trace_id,
                        span_type="llm_call",
                        name=MODELS["vision_reasoning"],
                        start_time=start_time,
                        end_time=start_time + elapsed,
                        duration_ms=elapsed * 1000,
                        model=MODELS["vision_reasoning"],
                        thinking_level=THINKING["reasoning"],
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        thinking_tokens=thinking_tokens,
                        cost_usd=cost,
                        input_messages=serializable_messages,
                        output_message=serializable_output,
                        model_parameters=model_params,
                        thought_summary=thought_summary,
                    ))
            except Exception as e:
                logger.error(f"Gemini API error: {e}")
                error_msg = f"Sorry, I encountered an error: {e}"
                if self.tracer and trace_id:
                    self.tracer.end_trace(trace_id, error_msg)
                return error_msg

            # Check what we got back
            candidate = response.candidates[0] if response.candidates else None
            if candidate is None:
                err = "No response from model."
                if self.tracer and trace_id:
                    self.tracer.end_trace(trace_id, err)
                return err

            # Collect function calls and text from the response
            function_calls = []
            text_parts = []
            all_parts = []

            for part in candidate.content.parts:
                all_parts.append(part)
                if hasattr(part, "function_call") and part.function_call:
                    function_calls.append(part.function_call)
                elif hasattr(part, "text") and part.text:
                    if not (hasattr(part, "thought") and part.thought):
                        text_parts.append(part.text)

            # Add model response to history
            self.conversation_history.append(candidate.content)

            # If no function calls, we have the final response
            if not function_calls:
                final_text = "\n".join(text_parts) if text_parts else "Done."
                if self.tracer and trace_id:
                    self.tracer.end_trace(trace_id, final_text)
                return final_text

            # Execute function calls and feed results back
            function_responses = []
            for fc in function_calls:
                fn_name = fc.name
                fn_args = dict(fc.args) if fc.args else {}

                logger.info(f"Executing: {fn_name}({fn_args})")
                fn_start_time = time.time()
                result = self.toolkit.execute(fn_name, fn_args)
                fn_elapsed = time.time() - fn_start_time
                
                # Prevent base64 spam in terminal
                log_result = result.copy() if isinstance(result, dict) else result
                if isinstance(log_result, dict) and "image_base64" in log_result and log_result["image_base64"]:
                    log_result["image_base64"] = f"<base64_data_omitted_len_{len(log_result['image_base64'])}>"
                    
                if self.tracer and trace_id:
                    from core.tracing import Span
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
                        status="error" if isinstance(result, dict) and not result.get("success", True) else "ok"
                    ))

                logger.info(f"Result (took {fn_elapsed:.2f}s): {log_result}")

                # For get_frame, do vision analysis instead of returning raw base64
                if fn_name == "get_frame" and result.get("success"):
                    from vision.llm_vision import describe_scene
                    image_b64 = result.get("image_base64", "")
                    if image_b64:
                        description = await describe_scene(
                            image_b64,
                            drone_context=f"Drone state: {self.toolkit.state.to_dict()}",
                            trace_id=trace_id
                        )
                        result = {
                            "success": True,
                            "scene_description": description,
                            "message": "Frame captured and analyzed",
                        }

                function_responses.append(
                    types.Part.from_function_response(
                        name=fn_name,
                        response=result,
                    )
                )

            # Add function results to history
            self.conversation_history.append(
                types.Content(role="user", parts=function_responses)
            )

        timeout_msg = "I've been thinking too long — stopping here for safety. Please give me a simpler instruction."
        if self.tracer and trace_id:
            self.tracer.end_trace(trace_id, timeout_msg)
        return timeout_msg

    def reset_conversation(self):
        """Clear conversation history."""
        self.conversation_history = []

    async def run_interactive(self):
        """Run interactive text-based agent loop (Phase 1 testing)."""
        print("\n" + "=" * 60)
        print("  TelloAI Agent — Interactive Mode")
        print("  Type commands. 'quit' to exit. 'reset' to clear history.")
        print("=" * 60 + "\n")

        self._running = True

        while self._running:
            try:
                user_input = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting...")
                break

            if not user_input:
                continue
            if user_input.lower() == "quit":
                # Auto-land if flying
                if self.toolkit.state.is_flying:
                    print("Landing drone before exit...")
                    self.toolkit.land()
                break
            if user_input.lower() == "reset":
                self.reset_conversation()
                print("Conversation history cleared.")
                continue
            if user_input.lower() == "status":
                print(f"State: {json.dumps(self.toolkit.state.to_dict(), indent=2)}")
                continue

            response = await self.process_message(user_input)
            print(f"\nTelloAI: {response}")
