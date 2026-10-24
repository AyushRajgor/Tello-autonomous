"""
Tests for vision/llm_vision.py. No network: the module-level Gemini client is
replaced by a fake.
"""

import asyncio
import base64
from types import SimpleNamespace

import pytest
from google.genai import types

import vision.llm_vision as vision
from config import MODELS, THINKING, VISION
from core.toolkit import TelloToolkit

FRAME_B64 = TelloToolkit(simulate=True).get_frame()["image_base64"]


def text_resp(text, thought=None):
    parts = []
    if thought:
        parts.append(types.Part(text=thought, thought=True))
    parts.append(types.Part(text=text))
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=600, candidates_token_count=20, thoughts_token_count=40
        ),
    )


class FakeModels:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def generate_content(self, *, model, contents, config):
        self.calls.append(SimpleNamespace(model=model, contents=contents, config=config))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeTracer:
    def __init__(self):
        self.spans = []

    def add_span(self, trace_id, span):
        self.spans.append(span)


@pytest.fixture
def fake_client(monkeypatch):
    def install(*responses):
        models = FakeModels(responses)
        monkeypatch.setattr(vision, "client", SimpleNamespace(aio=SimpleNamespace(models=models)))
        return models

    return install


@pytest.fixture
def tracer(monkeypatch):
    t = FakeTracer()
    monkeypatch.setattr(vision, "tracer", t)
    return t


def _level(value):
    return str(getattr(value, "value", value)).lower()


# ── Structured output ──

def test_check_path_clear_uses_structured_output(fake_client):
    models = fake_client(text_resp('{"clear": true, "obstacle": "", "distance_estimate": "none"}'))

    result = asyncio.run(vision.check_path_clear(FRAME_B64))

    assert result == {"clear": True, "obstacle": "", "distance_estimate": "none"}
    call = models.calls[0]
    cfg = call.config
    assert call.model == MODELS["vision_quick"]
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_json_schema == vision.PATH_CHECK_SCHEMA
    assert cfg.response_schema is None
    assert _level(cfg.thinking_config.thinking_level) == THINKING["quick"]
    assert cfg.temperature is None
    assert cfg.media_resolution == vision.resolve_media_resolution(VISION["media_resolution"])
    image_part = call.contents[0].parts[0]
    assert image_part.inline_data.mime_type == "image/jpeg"
    assert image_part.inline_data.data == base64.b64decode(FRAME_B64)


def test_check_path_clear_unparseable_answer_is_not_clear(fake_client):
    fake_client(text_resp("Looks clear to me!"))
    result = asyncio.run(vision.check_path_clear(FRAME_B64))
    assert result["clear"] is False
    assert result["distance_estimate"] == "unknown"


def test_check_path_clear_non_boolean_is_not_clear(fake_client):
    fake_client(text_resp('{"clear": "yes", "obstacle": "", "distance_estimate": "far"}'))
    assert asyncio.run(vision.check_path_clear(FRAME_B64))["clear"] is False


def test_fenced_json_fallback_is_parsed(fake_client):
    fake_client(text_resp('```json\n{"clear": true, "obstacle": "", "distance_estimate": "far"}\n```'))
    assert asyncio.run(vision.check_path_clear(FRAME_B64))["clear"] is True


def test_search_for_object_uses_reasoning_model(fake_client):
    models = fake_client(text_resp(
        '{"found": true, "location_in_frame": "left", "confidence": "high", '
        '"description": "a red cup on a table", "suggestion": ""}',
        thought="Looking for a cup.",
    ))

    result = asyncio.run(vision.search_for_object(FRAME_B64, "red cup", drone_context="Heading 90"))

    assert result == {
        "found": True,
        "location_in_frame": "left",
        "confidence": "high",
        "description": "a red cup on a table",
        "suggestion": "",
    }
    call = models.calls[0]
    assert call.model == MODELS["vision_reasoning"]
    assert _level(call.config.thinking_config.thinking_level) == THINKING["reasoning"]
    assert _level(call.config.thinking_config.thinking_level) != "minimal"
    assert call.config.response_json_schema == vision.OBJECT_SEARCH_SCHEMA
    prompt = call.contents[0].parts[1].text
    assert prompt.startswith("Heading 90") and "red cup" in prompt


def test_search_for_object_bad_json_defaults_to_not_found(fake_client):
    fake_client(text_resp("I can't tell"))
    result = asyncio.run(vision.search_for_object(FRAME_B64, "cup"))
    assert result["found"] is False
    assert result["confidence"] == "low"


def test_find_doors_normalizes_openings(fake_client):
    fake_client(text_resp(
        '{"openings": ['
        '{"type": "door", "position": "right", "state": "open", "estimated_width": "wide"},'
        '{"type": "door", "position": "behind", "state": "ajar", "estimated_width": "huge"},'
        '"junk"],'
        '"room_type": "kitchen", "navigable": true}'
    ))
    result = asyncio.run(vision.find_doors_and_openings(FRAME_B64))
    assert result["room_type"] == "kitchen"
    assert result["navigable"] is True
    assert result["openings"] == [
        {"type": "door", "position": "right", "state": "open", "estimated_width": "wide"},
        {"type": "door", "position": "unknown", "state": "unknown", "estimated_width": "unknown"},
    ]


def test_find_doors_bad_json_defaults(fake_client):
    models = fake_client(text_resp("{not json"))
    result = asyncio.run(vision.find_doors_and_openings(FRAME_B64))
    assert result == {"openings": [], "room_type": "unknown", "navigable": False}
    assert models.calls[0].config.response_json_schema == vision.OPENINGS_SCHEMA


def test_describe_scene_is_plain_text(fake_client):
    models = fake_client(text_resp("A gray wall.", thought="Hmm."))
    assert asyncio.run(vision.describe_scene(FRAME_B64)) == "A gray wall."
    cfg = models.calls[0].config
    assert cfg.response_mime_type is None
    assert cfg.response_json_schema is None


# ── Errors and tracing ──

def test_bad_base64_reports_error_without_unbound_start_time(fake_client, tracer):
    models = fake_client()
    result = asyncio.run(vision.analyze_frame("!!!notbase64", "describe", trace_id="t1"))
    assert result.startswith("Vision error:")
    assert models.calls == []
    (span,) = tracer.spans
    assert span.status == "error"
    assert span.duration_ms is not None and span.duration_ms >= 0


def test_api_error_keeps_safe_default(fake_client, tracer):
    fake_client(RuntimeError("503 overloaded"))
    result = asyncio.run(vision.check_path_clear(FRAME_B64, trace_id="t1"))
    assert result["clear"] is False
    assert "503 overloaded" in result["obstacle"]
    assert tracer.spans[0].status == "error"


def test_success_span_has_thumbnail_and_parse_flag(fake_client, tracer):
    fake_client(text_resp('{"clear": false, "obstacle": "chair", "distance_estimate": "close"}', thought="A chair."))
    asyncio.run(vision.check_path_clear(FRAME_B64, trace_id="t1"))
    (span,) = tracer.spans
    assert span.status == "ok"
    assert span.span_type == "vision_call"
    assert span.json_parse_success is True
    assert span.frame_thumbnail_b64
    assert span.thought_summary == "A chair."
    assert span.tokens_in == 600
    assert "temperature" not in span.model_parameters


def test_resolve_media_resolution():
    assert vision.resolve_media_resolution("medium") == types.MediaResolution.MEDIA_RESOLUTION_MEDIUM
    assert vision.resolve_media_resolution("MEDIA_RESOLUTION_LOW") == types.MediaResolution.MEDIA_RESOLUTION_LOW
    assert vision.resolve_media_resolution("bogus") is None
    assert vision.resolve_media_resolution(None) is None
