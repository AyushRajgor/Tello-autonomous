"""
LLM Vision

Sends drone camera frames to Gemini models for analysis.
Routes to different models based on task complexity:
- gemini-3.5-flash (reasoning) for scene description, planning
- gemini-3.1-flash-lite (quick) for simple yes/no checks
"""

import base64
import json
import logging
import time
from typing import Optional

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, MODELS, THINKING, VISION
from core.cost import calculate_cost
from core.tracing import Span
import uuid
import io
from PIL import Image

logger = logging.getLogger(__name__)

# Initialize Gemini client
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# Tracer will be injected from main.py
tracer = None


async def analyze_frame(
    image_base64: str,
    prompt: str,
    mode: str = "reasoning",
    context: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> str:
    """
    Send a frame to Gemini for analysis.

    Args:
        image_base64: Base64 encoded JPEG from get_frame()
        prompt: What to analyze ("describe the scene", "is path clear?", etc.)
        mode: "reasoning" (Gemini 3.5 Flash) or "quick" (3.1 Flash-Lite)
        context: Optional additional context (drone state, search target, etc.)

    Returns:
        LLM response text
    """
    if client is None:
        return "Error: GEMINI_API_KEY not configured"

    model_name = MODELS["vision_reasoning"] if mode == "reasoning" else MODELS["vision_quick"]
    thinking_level = THINKING.get(mode, "minimal")

    # Build the prompt with context
    full_prompt = prompt
    if context:
        full_prompt = f"{context}\n\n{prompt}"

    try:
        image_bytes = base64.b64decode(image_base64)

        start_time = time.time()
        
        model_params = {
            "temperature": 1.0,
            "thinking_level": thinking_level,
            "max_output_tokens": 1024,
        }
        
        response = await client.aio.models.generate_content(
            model=model_name,
            contents=[
                types.Content(
                    parts=[
                        types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                        types.Part.from_text(text=full_prompt),
                    ]
                )
            ],
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(
                    thinking_level=thinking_level,
                    include_thoughts=True,
                ),
                temperature=1.0,
                max_output_tokens=1024,
            ),
        )
        elapsed = time.time() - start_time
        logger.info(f"Vision API ({model_name}) took {elapsed:.2f}s")

        # Extract text and thought summary from response
        result_text = ""
        thought_parts = []
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if hasattr(part, "text") and part.text:
                    if hasattr(part, "thought") and part.thought:
                        thought_parts.append(part.text)
                    else:
                        result_text += part.text
        thought_summary = "\n\n".join(thought_parts) if thought_parts else None

        result_text = result_text.strip() if result_text else "No analysis available"

        json_parse_success = None
        if "JSON" in prompt:
            try:
                cleaned = result_text.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
                json.loads(cleaned)
                json_parse_success = True
            except:
                json_parse_success = False

        if tracer and trace_id:
            tokens_in = 0
            tokens_out = 0
            thinking_tokens = 0
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                tokens_in = getattr(response.usage_metadata, 'prompt_token_count', 0)
                tokens_out = getattr(response.usage_metadata, 'candidates_token_count', 0)
                thinking_tokens = getattr(response.usage_metadata, 'thoughts_token_count', 0)

            cost = calculate_cost(model_name, tokens_in, tokens_out, thinking_tokens)

            thumbnail_b64 = None
            try:
                img = Image.open(io.BytesIO(image_bytes))
                img.thumbnail((VISION.get("frame_width", 160), VISION.get("frame_height", 120)))
                # Force resize to 160x120 for thumbnails specifically
                img.thumbnail((160, 120))
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=40)
                thumbnail_b64 = base64.b64encode(buf.getvalue()).decode()
            except Exception as thumb_e:
                logger.warning(f"Failed to generate thumbnail for trace: {thumb_e}")

            tracer.add_span(trace_id, Span(
                span_id=uuid.uuid4().hex[:8],
                trace_id=trace_id,
                span_type="vision_call",
                name="analyze_frame",
                start_time=start_time,
                end_time=start_time + elapsed,
                duration_ms=elapsed * 1000,
                status="ok",
                vision_model=model_name,
                vision_mode=mode,
                vision_prompt=full_prompt,
                vision_response=result_text,
                frame_thumbnail_b64=thumbnail_b64,
                json_parse_success=json_parse_success,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                thinking_tokens=thinking_tokens,
                cost_usd=cost,
                model_parameters=model_params,
                thought_summary=thought_summary,
            ))

        return result_text

    except Exception as e:
        logger.error(f"Vision analysis error ({model_name}): {e}")
        if tracer and trace_id:
            tracer.add_span(trace_id, Span(
                span_id=uuid.uuid4().hex[:8],
                trace_id=trace_id,
                span_type="vision_call",
                name="analyze_frame",
                start_time=start_time,
                end_time=time.time(),
                duration_ms=(time.time() - start_time) * 1000,
                status="error",
                error=str(e),
                vision_model=model_name
            ))
        return f"Vision error: {e}"


async def describe_scene(image_base64: str, drone_context: str = "", trace_id: Optional[str] = None) -> str:
    """Full scene description using Gemini 3.5 Flash."""
    return await analyze_frame(
        image_base64=image_base64,
        prompt=(
            "Describe what you see in this drone camera image. "
            "Include: room type, major objects, their approximate positions (left/center/right, near/far), "
            "any doors or openings, and the general layout. Be concise but thorough."
        ),
        mode="reasoning",
        context=drone_context,
        trace_id=trace_id,
    )


async def check_path_clear(image_base64: str, trace_id: Optional[str] = None) -> dict:
    """Quick check if the path ahead is clear. Uses Flash-Lite for speed."""
    response = await analyze_frame(
        image_base64=image_base64,
        prompt=(
            "Is the path directly ahead clear for a small drone to fly through? "
            "Respond in JSON: {\"clear\": true/false, \"obstacle\": \"description if blocked\", "
            "\"distance_estimate\": \"close/medium/far\"}"
        ),
        mode="quick",
        trace_id=trace_id,
    )

    try:
        # Try to parse JSON response
        # Strip markdown code fences if present
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(cleaned)
    except (json.JSONDecodeError, IndexError):
        # Fallback: assume not clear if we can't parse
        return {"clear": False, "obstacle": response, "distance_estimate": "unknown"}


async def search_for_object(image_base64: str, target: str, drone_context: str = "", trace_id: Optional[str] = None) -> dict:
    """Search for a specific object in the frame."""
    response = await analyze_frame(
        image_base64=image_base64,
        prompt=(
            f"I'm searching for: {target}\n"
            "Look carefully at this drone camera image.\n"
            "Respond in JSON: {\n"
            f'  "found": true/false,\n'
            f'  "location_in_frame": "left/center/right" (if found),\n'
            f'  "confidence": "high/medium/low",\n'
            f'  "description": "what you see",\n'
            f'  "suggestion": "where to look next if not found"\n'
            "}"
        ),
        mode="reasoning",
        context=drone_context,
        trace_id=trace_id,
    )

    try:
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(cleaned)
    except (json.JSONDecodeError, IndexError):
        return {"found": False, "description": response, "suggestion": "Could not parse response"}


async def find_doors_and_openings(image_base64: str, trace_id: Optional[str] = None) -> dict:
    """Identify doors, doorways, and openings for navigation."""
    response = await analyze_frame(
        image_base64=image_base64,
        prompt=(
            "Identify any doors, doorways, hallways, or openings in this drone camera image "
            "that could be navigated through.\n"
            "Respond in JSON: {\n"
            '  "openings": [\n'
            '    {"type": "door/hallway/opening", "position": "left/center/right", '
            '"state": "open/closed/partially_open", "estimated_width": "narrow/medium/wide"}\n'
            "  ],\n"
            '  "room_type": "bedroom/kitchen/hallway/etc",\n'
            '  "navigable": true/false\n'
            "}"
        ),
        mode="reasoning",
        trace_id=trace_id,
    )

    try:
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(cleaned)
    except (json.JSONDecodeError, IndexError):
        return {"openings": [], "room_type": "unknown", "navigable": False}
