import re
from pathlib import Path

from config import MODELS, THINKING
from core.cost import MODEL_PRICING


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


# Model IDs shut down or deprecated as of 2026-10 — must never come back.
RETIRED_MODELS = {
    "gemini-3.5-flash",                # deprecated 2026-10-08 (silently routed to 3.6)
    "gemini-3.1-flash-live-preview",   # shutdown 2026-11-17
    "gemini-robotics-er-1.6-preview",  # shut down 2026-08-31
    "gemini-2.5-flash-native-audio",
    "gemini-3-flash-preview",
}


def test_gemini_model_defaults_are_current():
    assert MODELS["vision_reasoning"] == "gemini-3.8-flash"
    assert MODELS["vision_quick"] == "gemini-3.5-flash-lite"
    assert MODELS["voice_fallback"] == "gpt-realtime-2.1"
    assert MODELS["live"] == "gemini-3.8-live"
    assert MODELS["vision_spatial"] == "gemini-robotics-er-2-preview"


def test_no_retired_models_configured():
    assert not set(MODELS.values()) & RETIRED_MODELS


def test_reasoning_model_thinking_level_is_supported():
    # gemini-3.8-flash rejects "minimal"; only low/medium/high are valid.
    assert THINKING["reasoning"] in {"low", "medium", "high"}


def test_configured_gemini_models_have_pricing_entries():
    configured_gemini_models = {
        model for model in MODELS.values() if model.startswith("gemini-")
    }

    assert configured_gemini_models <= set(MODEL_PRICING)


def test_robotics_pricing_uses_current_rates():
    assert MODEL_PRICING["gemini-robotics-er-2-preview"]["input"] == 1.00
    assert MODEL_PRICING["gemini-robotics-er-2-preview"]["output"] == 5.00


def test_google_genai_dependency_supports_live_interaction_status():
    # 2.23.0: Live receive() understands interaction_status (needed for 3.8 Live);
    # 3.x will remove Live methods, so stay below it until migrated.
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    match = re.search(r"^google-genai>=([0-9.]+),<3\.0\.0$", requirements, re.MULTILINE)

    assert match is not None
    assert _version_tuple(match.group(1)) >= (2, 23, 0)


def test_installed_google_genai_meets_floor():
    from importlib.metadata import version

    assert _version_tuple(version("google-genai").split("rc")[0]) >= (2, 23, 0)


def test_http_client_retries_transient_errors(monkeypatch):
    import core.genai_client as gc

    monkeypatch.setattr(gc, "GEMINI_API_KEY", "offline-test-key")
    client = gc.make_client()
    retry = client._api_client._http_options.retry_options
    assert retry.attempts >= 3
    assert 503 in retry.http_status_codes and 429 in retry.http_status_codes

    monkeypatch.setattr(gc, "GEMINI_API_KEY", "")
    assert gc.make_client() is None


def test_fastapi_owns_starlette_dependency():
    requirements = Path("requirements.txt").read_text(encoding="utf-8")

    assert re.search(r"^fastapi[<>=~!]", requirements, re.MULTILINE)
    assert not re.search(r"^starlette(?:[<>=~!].*)?$", requirements, re.MULTILINE)
