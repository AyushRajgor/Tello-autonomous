"""
Shared Gemini client factory for the HTTP (generate_content) paths.
"""

from typing import Optional

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_HTTP_RETRY


def make_client() -> Optional[genai.Client]:
    """Gemini client with retry/backoff on transient errors, or None without a key."""
    if not GEMINI_API_KEY:
        return None
    return genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(**GEMINI_HTTP_RETRY)),
    )
