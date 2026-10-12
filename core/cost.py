"""
Cost Calculator for LLM calls based on tokens.
"""

# Pricing table (USD per million tokens, verified October 2026 at
# ai.google.dev/gemini-api/docs/pricing). Output rates include thinking tokens.
# Scheduled increases (not yet applied): gemini-3.8-flash goes to $1.50 / $7.50
# and gemini-robotics-er-2-preview to $2.00 / $10.00 on 2027-01-01.
MODEL_PRICING = {
    "gemini-3.8-flash": {
        "input": 0.75,
        "output": 3.75,
        "thinking": 3.75,
    },
    "gemini-3.1-flash-lite": {
        "input": 0.25,
        "output": 1.50,
        "thinking": 1.50,
    },
    # Live API text rates. Audio is billed at $3.00/M in and $12.00/M out,
    # image/video input at $1.00/M.
    "gemini-3.8-live": {
        "input": 0.75,
        "output": 4.50,
        "thinking": 0.00,
    },
    "gemini-robotics-er-2-preview": {
        "input": 1.00,
        "output": 5.00,
        "thinking": 5.00,
    },
}

def calculate_cost(model: str, tokens_in: int, tokens_out: int, thinking_tokens: int = 0) -> float:
    """Returns cost in USD based on input, output, and thinking tokens."""
    tokens_in = tokens_in or 0
    tokens_out = tokens_out or 0
    thinking_tokens = thinking_tokens or 0

    if not model or model not in MODEL_PRICING:
        # Default to a generic cheap rate if model not found
        cost_in = (tokens_in / 1_000_000) * 0.50
        cost_out = (tokens_out / 1_000_000) * 1.50
        return round(cost_in + cost_out, 6)

    pricing = MODEL_PRICING[model]

    cost_in = (tokens_in / 1_000_000) * pricing["input"]
    cost_out = (tokens_out / 1_000_000) * pricing["output"]
    cost_thinking = (thinking_tokens / 1_000_000) * pricing["thinking"]

    return round(cost_in + cost_out + cost_thinking, 6)
