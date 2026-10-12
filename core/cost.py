"""
Cost Calculator for LLM calls based on tokens.
"""

# Pricing table (per million tokens, June 2026).
# Output rates include thinking tokens for current Gemini models.
MODEL_PRICING = {
    "gemini-3.5-flash": {
        "input": 1.50,
        "output": 9.00,
        "thinking": 9.00,
    },
    "gemini-3.1-flash-lite": {
        "input": 0.25,
        "output": 1.50,
        "thinking": 1.50,
    },
    "gemini-3.1-flash-live-preview": {
        "input": 0.75,
        "output": 4.50,
        "thinking": 0.00,
    },
    "gemini-robotics-er-1.6-preview": {
        "input": 1.00,
        "output": 5.00,
        "thinking": 0.00,
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
