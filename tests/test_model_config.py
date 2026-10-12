import re
from pathlib import Path

from config import MODELS
from core.cost import MODEL_PRICING


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def test_gemini_model_defaults_are_current():
    assert MODELS["vision_reasoning"] == "gemini-3.5-flash"
    assert MODELS["vision_quick"] == "gemini-3.1-flash-lite"
    assert MODELS["live"] == "gemini-3.1-flash-live-preview"
    assert MODELS["vision_spatial"] == "gemini-robotics-er-1.6-preview"


def test_configured_gemini_models_have_pricing_entries():
    configured_gemini_models = {
        model for model in MODELS.values() if model.startswith("gemini-")
    }

    assert configured_gemini_models <= set(MODEL_PRICING)


def test_robotics_pricing_uses_standard_text_image_video_rates():
    assert MODEL_PRICING["gemini-robotics-er-1.6-preview"]["input"] == 1.00
    assert MODEL_PRICING["gemini-robotics-er-1.6-preview"]["output"] == 5.00


def test_google_genai_dependency_supports_interactions_api_floor():
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    match = re.search(r"^google-genai>=([0-9.]+)$", requirements, re.MULTILINE)

    assert match is not None
    assert _version_tuple(match.group(1)) >= (2, 3, 0)


def test_fastapi_tracks_current_line_and_owns_starlette_dependency():
    requirements = Path("requirements.txt").read_text(encoding="utf-8")

    assert "fastapi>=0.138,<0.139" in requirements
    assert not re.search(r"^starlette(?:[<>=~!].*)?$", requirements, re.MULTILINE)
