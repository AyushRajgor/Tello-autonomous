"""
LLM Vision

Sends drone camera frames to Gemini models for analysis.
Routes to different models based on task complexity:
- MODELS["vision_reasoning"] (thinking THINKING["reasoning"]) for scene
  description, object search and door finding
- MODELS["vision_quick"] (thinking THINKING["quick"]) for cheap yes/no checks

The text agent (core/agent.py) no longer uses this module for get_frame: it
hands the JPEG straight to the planner as a multimodal function response.
These helpers serve the object-search scaffold (features/search.py) and any
other caller that needs a one-shot frame analysis.

Structured answers (path check, object search, openings) use Gemini's
structured output (response_mime_type="application/json" plus
response_json_schema). A tolerant parser and safe defaults remain as a
safety net: if the answer can't be parsed, the path is NOT clear, the object
is NOT found and no opening is navigable.
"""

import base64
import io
import json
import logging
import time
import uuid
from typing import Any, Optional

from google.genai import types
from PIL import Image

from config import MODELS, THINKING, TRACING, VISION
from core.cost import calculate_cost
from core.genai_client import make_client
from core.tracing import Span

logger = logging.getLogger(__name__)

# Gemini client with retry/backoff on transient 429/5xx errors
client = make_client()

# Tracer will be injected from main.py
tracer = None

# max_output_tokens caps thinking + visible output combined, so the reasoning
# mode needs headroom or a medium-thinking answer gets truncated mid-JSON.
MAX_OUTPUT_TOKENS = {"quick": 1024, "reasoning": 4096}


# ── Structured-output schemas (JSON Schema subset accepted by Gemini) ──

PATH_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "clear": {
            "type": "boolean",
            "description": "True only if a small drone can safely fly about 1 meter straight ahead.",
        },
        "obstacle": {
            "type": "string",
            "description": "What blocks the path. Empty string if the path is clear.",
        },
        "distance_estimate": {
            "type": "string",
            "enum": ["close", "medium", "far", "none"],
            "description": "Distance to the nearest obstacle ahead; 'none' if nothing is in the way.",
        },
    },
    "required": ["clear", "obstacle", "distance_estimate"],
}

OBJECT_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean", "description": "True if the target is visible in this image."},
        "location_in_frame": {
            "type": "string",
            "enum": ["left", "center", "right", "none"],
            "description": "Where the target is in the frame; 'none' if not found.",
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "description": {"type": "string", "description": "Short description of what is visible."},
        "suggestion": {"type": "string", "description": "Where to look next if the target is not found."},
    },
    "required": ["found", "location_in_frame", "confidence", "description", "suggestion"],
}

OPENINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "openings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["door", "doorway", "hallway", "opening"]},
                    "position": {"type": "string", "enum": ["left", "center", "right"]},
                    "state": {"type": "string", "enum": ["open", "closed", "partially_open"]},
                    "estimated_width": {"type": "string", "enum": ["narrow", "medium", "wide"]},
                },
                "required": ["type", "position", "state", "estimated_width"],
            },
        },
        "room_type": {"type": "string", "description": "bedroom, kitchen, hallway, etc."},
        "navigable": {
            "type": "boolean",
            "description": "True if at least one opening is wide and open enough for a small drone.",
        },
    },
    "required": ["openings", "room_type", "navigable"],
}


# ── Helpers ──

def resolve_media_resolution(value: Any) -> Optional[types.MediaResolution]:
    """Map a config value ("medium", "MEDIA_RESOLUTION_LOW", enum) to the SDK enum."""
    if not value:
        return None
    if isinstance(value, types.MediaResolution):
        return value
    key = str(value).strip().upper()
    if not key.startswith("MEDIA_RESOLUTION_"):
        key = f"MEDIA_RESOLUTION_{key}"
    # Look up by name: the SDK enum would otherwise accept unknown values (with a warning).
    member = types.MediaResolution.__members__.get(key)
    if member is None:
        logger.warning(f"Unknown media_resolution {value!r}; using the model default")
    return member


def make_thumbnail_b64(image_bytes: Optional[bytes]) -> Optional[str]:
    """Small JPEG thumbnail (base64) for trace spans. Never raises."""
    if not image_bytes:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.thumbnail((TRACING.get("thumbnail_width", 160), TRACING.get("thumbnail_height", 120)))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=TRACING.get("thumbnail_quality", 40))
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.warning(f"Failed to generate thumbnail for trace: {e}")
        return None


def _parse_json_object(text: Optional[str]) -> Optional[dict]:
    """
    Parse a JSON object from model output. Structured output should make this
    a plain json.loads; markdown fences and surrounding prose are tolerated as
    a safety net. Returns None if no JSON object can be recovered.
    """
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        cleaned = cleaned.rsplit("```", 1)[0].strip()

    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if 0 <= start < end:
        candidates.append(cleaned[start:end + 1])

    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:  # json.JSONDecodeError is a ValueError
            continue
        if isinstance(value, dict):
            return value
    return None


def _choice(value: Any, allowed: tuple, default: str) -> str:
    """Return value if it is one of the allowed enum strings, else default."""
    return value if isinstance(value, str) and value in allowed else default


def _extract_text(response) -> tuple[str, Optional[str]]:
    """Return (answer text, thought summary) from the first candidate."""
    texts: list[str] = []
    thoughts: list[str] = []
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        content = getattr(candidates[0], "content", None)
        for part in (getattr(content, "parts", None) or []):
            if getattr(part, "text", None):
                (thoughts if getattr(part, "thought", None) else texts).append(part.text)
    return "".join(texts).strip(), ("\n\n".join(thoughts) if thoughts else None)


# ── Core call ──

async def analyze_frame(
    image_base64: str,
    prompt: str,
    mode: str = "reasoning",
    context: Optional[str] = None,
    trace_id: Optional[str] = None,
    response_schema: Optional[dict] = None,
) -> str:
    """
    Send a frame to Gemini for analysis.

    Args:
        image_base64: Base64 encoded JPEG from get_frame()
        prompt: What to analyze ("describe the scene", "is path clear?", etc.)
        mode: "reasoning" (MODELS["vision_reasoning"]) or "quick" (MODELS["vision_quick"])
        context: Optional additional context (drone state, search target, etc.)
        trace_id: Optional trace to attach a vision_call span to
        response_schema: Optional JSON Schema. When given, the model is asked
            for JSON matching it (structured output) and the raw JSON text is
            returned.

    Returns:
        LLM response text, or a string starting with "Vision error:" on failure.
    """
    if client is None:
        return "Error: GEMINI_API_KEY not configured"

    quick = mode == "quick"
    mode_key = "quick" if quick else "reasoning"
    model_name = MODELS["vision_quick"] if quick else MODELS["vision_reasoning"]
    # Never send THINKING["quick"] ("minimal") to the reasoning model: 3.8 Flash rejects it.
    thinking_level = THINKING["quick"] if quick else THINKING["reasoning"]
    max_output_tokens = MAX_OUTPUT_TOKENS[mode_key]
    media_resolution = resolve_media_resolution(VISION.get("media_resolution"))

    full_prompt = f"{context}\n\n{prompt}" if context else prompt

    model_params = {
        "thinking_level": thinking_level,
        "max_output_tokens": max_output_tokens,
        "media_resolution": media_resolution.value if media_resolution else None,
    }
    config_kwargs: dict[str, Any] = {
        "thinking_config": types.ThinkingConfig(thinking_level=thinking_level, include_thoughts=True),
        "max_output_tokens": max_output_tokens,
        "media_resolution": media_resolution,
    }
    if response_schema is not None:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["response_json_schema"] = response_schema
        model_params["response_mime_type"] = "application/json"

    start_time = time.time()
    image_bytes: Optional[bytes] = None
    try:
        image_bytes = base64.b64decode(image_base64)
        response = await client.aio.models.generate_content(
            model=model_name,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                        types.Part.from_text(text=full_prompt),
                    ],
                )
            ],
            config=types.GenerateContentConfig(**config_kwargs),
        )
    except Exception as e:
        end_time = time.time()
        logger.error(f"Vision analysis error ({model_name}): {e}")
        if tracer and trace_id:
            tracer.add_span(trace_id, Span(
                span_id=uuid.uuid4().hex[:8],
                trace_id=trace_id,
                span_type="vision_call",
                name="analyze_frame",
                start_time=start_time,
                end_time=end_time,
                duration_ms=(end_time - start_time) * 1000,
                status="error",
                error=str(e),
                vision_model=model_name,
                vision_mode=mode,
                vision_prompt=full_prompt,
                frame_thumbnail_b64=make_thumbnail_b64(image_bytes),
                model_parameters=model_params,
            ))
        return f"Vision error: {e}"

    elapsed = time.time() - start_time
    logger.info(f"Vision API ({model_name}) took {elapsed:.2f}s")

    result_text, thought_summary = _extract_text(response)
    result_text = result_text or "No analysis available"

    json_parse_success = None
    if response_schema is not None:
        json_parse_success = _parse_json_object(result_text) is not None

    if tracer and trace_id:
        usage = getattr(response, "usage_metadata", None)
        tokens_in = getattr(usage, "prompt_token_count", 0) or 0
        tokens_out = getattr(usage, "candidates_token_count", 0) or 0
        thinking_tokens = getattr(usage, "thoughts_token_count", 0) or 0
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
            frame_thumbnail_b64=make_thumbnail_b64(image_bytes),
            json_parse_success=json_parse_success,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            thinking_tokens=thinking_tokens,
            cost_usd=calculate_cost(model_name, tokens_in, tokens_out, thinking_tokens),
            model_parameters=model_params,
            thought_summary=thought_summary,
        ))

    return result_text


# ── Task helpers (signatures are stable: features/search.py uses them) ──

async def describe_scene(image_base64: str, drone_context: str = "", trace_id: Optional[str] = None) -> str:
    """Full scene description using the reasoning model (plain text)."""
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
    """
    Quick check if the path ahead is clear. Uses the quick model.

    Returns {"clear": bool, "obstacle": str, "distance_estimate": str}.
    Anything that isn't an explicit, parseable "clear": true counts as blocked.
    """
    response = await analyze_frame(
        image_base64=image_base64,
        prompt=(
            "You are the obstacle check for a small indoor drone. "
            "Is the path directly ahead clear for the drone to fly about 1 meter forward? "
            "If anything is in the way or you are unsure, answer clear=false."
        ),
        mode="quick",
        trace_id=trace_id,
        response_schema=PATH_CHECK_SCHEMA,
    )

    parsed = _parse_json_object(response)
    if parsed is None:
        # Fail safe: if we can't read the answer, the path is NOT clear.
        return {"clear": False, "obstacle": response, "distance_estimate": "unknown"}
    return {
        "clear": parsed.get("clear") is True,
        "obstacle": str(parsed.get("obstacle") or ""),
        "distance_estimate": _choice(parsed.get("distance_estimate"), ("close", "medium", "far", "none"), "unknown"),
    }


async def search_for_object(image_base64: str, target: str, drone_context: str = "", trace_id: Optional[str] = None) -> dict:
    """
    Search for a specific object in the frame.

    Returns {"found", "location_in_frame", "confidence", "description", "suggestion"}.
    """
    response = await analyze_frame(
        image_base64=image_base64,
        prompt=(
            f"I'm searching for: {target}\n"
            "Look carefully at this drone camera image. Report whether the target is visible, "
            "where it is in the frame, how confident you are, what you see, and where to look next "
            "if it isn't here."
        ),
        mode="reasoning",
        context=drone_context,
        trace_id=trace_id,
        response_schema=OBJECT_SEARCH_SCHEMA,
    )

    parsed = _parse_json_object(response)
    if parsed is None:
        return {
            "found": False,
            "location_in_frame": "none",
            "confidence": "low",
            "description": response,
            "suggestion": "Could not parse response",
        }
    return {
        "found": parsed.get("found") is True,
        "location_in_frame": _choice(parsed.get("location_in_frame"), ("left", "center", "right", "none"), "none"),
        "confidence": _choice(parsed.get("confidence"), ("high", "medium", "low"), "low"),
        "description": str(parsed.get("description") or ""),
        "suggestion": str(parsed.get("suggestion") or ""),
    }


async def find_doors_and_openings(image_base64: str, trace_id: Optional[str] = None) -> dict:
    """
    Identify doors, doorways, and openings for navigation.

    Returns {"openings": [...], "room_type": str, "navigable": bool}. Opening
    fields that don't match the schema become "unknown", so callers that look
    for open + medium/wide openings never pick a malformed one.
    """
    response = await analyze_frame(
        image_base64=image_base64,
        prompt=(
            "Identify any doors, doorways, hallways, or openings in this drone camera image "
            "that a small drone could fly through. For each, give its type, position in the "
            "frame, whether it is open, and its approximate width. Also name the room type."
        ),
        mode="reasoning",
        trace_id=trace_id,
        response_schema=OPENINGS_SCHEMA,
    )

    parsed = _parse_json_object(response)
    if parsed is None:
        return {"openings": [], "room_type": "unknown", "navigable": False}

    raw_openings = parsed.get("openings")
    openings = []
    for item in raw_openings if isinstance(raw_openings, list) else []:
        if not isinstance(item, dict):
            continue
        openings.append({
            "type": _choice(item.get("type"), ("door", "doorway", "hallway", "opening"), "opening"),
            "position": _choice(item.get("position"), ("left", "center", "right"), "unknown"),
            "state": _choice(item.get("state"), ("open", "closed", "partially_open"), "unknown"),
            "estimated_width": _choice(item.get("estimated_width"), ("narrow", "medium", "wide"), "unknown"),
        })
    return {
        "openings": openings,
        "room_type": str(parsed.get("room_type") or "unknown"),
        "navigable": parsed.get("navigable") is True,
    }
