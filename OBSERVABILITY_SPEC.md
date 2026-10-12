# TelloAI Observability Spec

Hand this file to Cursor or Claude Code:
> "Implement the observability system described in OBSERVABILITY_SPEC.md"

---

## Goal

Full request tracing from user message → LLM calls → tool executions → response. Viewable in dashboard, terminal, and exported as JSON lines file.

---

## 1. Core: Trace System (`core/tracing.py`)

### Data Model

```python
@dataclass
class Span:
    span_id: str              # uuid4 short (8 chars)
    trace_id: str             # parent trace id
    span_type: str            # "llm_call" | "tool_call" | "vision_call" | "user_message" | "response"
    name: str                 # e.g. "gemini-3.5-flash", "get_frame", "describe_scene"
    start_time: float         # time.time()
    end_time: float | None
    duration_ms: float | None # computed: (end_time - start_time) * 1000
    status: str               # "ok" | "error"
    error: str | None

    # LLM-specific (populated only for span_type == "llm_call")
    model: str | None
    thinking_level: str | None
    tokens_in: int | None
    tokens_out: int | None
    thinking_tokens: int | None
    cost_usd: float | None          # computed from token counts

    # Tool-specific (populated only for span_type == "tool_call")
    tool_name: str | None
    tool_args: dict | None
    tool_result: dict | None         # full result dict from toolkit.execute()

    # Vision-specific (populated only for span_type == "vision_call")
    vision_model: str | None
    vision_prompt: str | None
    vision_response: str | None
    vision_mode: str | None          # "reasoning" | "quick"
    frame_thumbnail_b64: str | None  # downscaled to 160x120 JPEG, quality 40
    json_parse_success: bool | None  # did the response parse as valid JSON?

@dataclass
class Trace:
    trace_id: str             # uuid4 short (8 chars)
    timestamp: str            # ISO 8601
    user_message: str
    final_response: str | None
    total_duration_ms: float | None
    total_cost_usd: float | None    # sum of all span costs
    total_tokens_in: int | None     # sum across all LLM spans
    total_tokens_out: int | None
    spans: list[Span]
    drone_state_snapshot: dict      # toolkit.state.to_dict() at trace start
```

### Trace Manager Class

```python
class TraceManager:
    def __init__(self, log_dir: str = "logs"):
        self.current_traces: dict[str, Trace] = {}  # active traces
        self.completed_traces: list[Trace] = []      # recent completed (keep last 100)
        self.log_dir = log_dir  # create if not exists

    def start_trace(self, user_message: str, drone_state: dict) -> str:
        """Start a new trace. Returns trace_id."""

    def add_span(self, trace_id: str, span: Span):
        """Add a completed span to a trace."""

    def end_trace(self, trace_id: str, final_response: str):
        """Finalize trace, compute totals, write to file, log to terminal."""

    def get_recent_traces(self, limit: int = 50) -> list[dict]:
        """Return recent traces as dicts (for dashboard API)."""

    def get_trace(self, trace_id: str) -> dict | None:
        """Get a single trace with all spans (for detail view)."""
```

### File Output

Write to `logs/traces.jsonl` — one JSON object per line, one line per completed trace. Create the `logs/` directory on first write. Each line contains the full `Trace` as JSON including all spans.

### Terminal Output

On trace completion, print a compact summary to terminal:

```
[TRACE abc12345] "what do you see?" → 2230ms | $0.004 | 3 spans
  ├─ llm_call  gemini-3.5-flash  1200ms  850→120 tokens  $0.002
  ├─ tool_call get_frame          80ms   ok
  └─ llm_call  gemini-3.5-flash   900ms  1200→95 tokens  $0.002
```

Use Python `logging` with a custom formatter. Log at INFO level.

---

## 2. Cost Calculator (`core/cost.py`)

Pricing table (per million tokens, June 2026):

```python
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
    """Returns cost in USD."""
```

NOTE: These are approximate. Update when actual pricing is confirmed. The important thing is the infrastructure to track — exact numbers can be fixed later.

---

## 3. Integration Points

### 3a. In `core/agent.py` — Wrap the agent loop

The `process_message()` method needs to:

1. **Start trace** at the beginning with user_message and current drone state
2. **Before each `generate_content()` call**: record start time
3. **After each `generate_content()` call**: extract token usage from `response.usage_metadata` (if available), create an `llm_call` span with model, tokens, latency, cost
4. **Before each `toolkit.execute()` call**: record start time
5. **After each `toolkit.execute()` call**: create a `tool_call` span with function name, args, result, latency
6. **End trace** when returning the final response

Key: Extract token counts from the Gemini response:
```python
# After generate_content() call
if hasattr(response, 'usage_metadata') and response.usage_metadata:
    tokens_in = response.usage_metadata.prompt_token_count
    tokens_out = response.usage_metadata.candidates_token_count
    thinking_tokens = getattr(response.usage_metadata, 'thoughts_token_count', 0)
```

### 3b. In `vision/llm_vision.py` — Wrap vision calls

Every call to `analyze_frame()` should create a `vision_call` span including:
- Which model was used
- The prompt sent
- The raw response text
- Whether JSON parsing succeeded
- A **thumbnail** of the frame: resize the input image to 160x120, JPEG quality 40, store as base64. This lets you see what the drone saw without storing full frames.

```python
# Create thumbnail for trace
from PIL import Image
import io
img = Image.open(io.BytesIO(base64.b64decode(image_base64)))
img.thumbnail((160, 120))
buf = io.BytesIO()
img.save(buf, format="JPEG", quality=40)
thumbnail_b64 = base64.b64encode(buf.getvalue()).decode()
```

### 3c. In `dashboard/server.py` — Broadcast traces

- **On trace completion**: broadcast `{"type": "trace", "data": trace_dict}` to all WebSocket clients
- **New REST endpoint** `GET /api/traces?limit=50` — returns recent traces
- **New REST endpoint** `GET /api/traces/{trace_id}` — returns single trace with all spans

---

## 4. Dashboard Trace Viewer

Add a **Traces tab** to the dashboard. Two views:

### 4a. Trace List View (default)

Shows recent traces as cards/rows:

```
┌──────────────────────────────────────────────────────────────┐
│ abc123  "what do you see?"              2.2s  $0.004  3 spans│
│ def456  "take off and go forward"       3.8s  $0.006  5 spans│
│ ghi789  "find the red cup"             12.1s  $0.024  9 spans│
└──────────────────────────────────────────────────────────────┘
```

Each row shows: trace_id, user message (truncated), total duration, total cost, span count. Color-code: green for <1s, yellow for 1-3s, red for >3s.

Click a row → opens detail view.

### 4b. Trace Detail View (waterfall)

Shows spans as a horizontal waterfall timeline:

```
abc12345 — "what do you see?" — 2230ms total — $0.004

|████████████████████|                          llm_call  gemini-3.5-flash  1200ms  $0.002
                     |██|                       tool_call get_frame        80ms
                        |█████████████████|     llm_call  gemini-3.5-flash   900ms  $0.002

Tokens: 2050 in → 215 out | Thinking: 0
Drone state: Battery 85% | Pos (0, 100, 80) | Heading 90°
```

For `vision_call` spans, show:
- The thumbnail image (inline, small)
- The prompt sent
- The response (collapsible)
- JSON parse status: ✓ or ✗

For `tool_call` spans, show:
- Function name + args
- Result (collapsible JSON)

### 4c. Session Stats (top of traces tab)

Aggregate stats for the current session:

```
Session: 14m 32s | Traces: 23 | Total cost: $0.082
Avg latency: 1.8s | LLM calls: 47 | Tool calls: 31
Models used: gemini-3.5-flash (38), gemini-3.1-flash-lite (9)
```

---

## 5. Implementation Order

1. **`core/cost.py`** — Simple, no dependencies. Just the pricing dict and calculator function.
2. **`core/tracing.py`** — Trace/Span dataclasses, TraceManager with file output and terminal logging. Test independently.
3. **Instrument `core/agent.py`** — Add trace start/end and LLM call spans to `process_message()`.
4. **Instrument `vision/llm_vision.py`** — Add vision call spans with thumbnails to `analyze_frame()`.
5. **Dashboard API** — Add `/api/traces` endpoints and WebSocket broadcast.
6. **Dashboard UI** — Traces tab with list view, detail waterfall, and session stats.

---

## 6. File Changes Summary

| File | Action |
|------|--------|
| `core/tracing.py` | **NEW** — Trace, Span, TraceManager |
| `core/cost.py` | **NEW** — pricing table, calculate_cost() |
| `core/agent.py` | **MODIFY** — wrap process_message() with tracing |
| `vision/llm_vision.py` | **MODIFY** — wrap analyze_frame() with vision spans |
| `dashboard/server.py` | **MODIFY** — add /api/traces endpoints, broadcast traces, add Traces tab HTML |
| `config.py` | **MODIFY** — add TRACING config section (log_dir, max_traces, thumbnail_size) |
| `main.py` | **MODIFY** — initialize TraceManager, pass to agent and vision |
| `.gitignore` | **MODIFY** — add `logs/` |

---

## 7. Config Additions (in `config.py`)

```python
TRACING = {
    "enabled": True,
    "log_dir": "logs",
    "log_file": "traces.jsonl",
    "max_recent_traces": 100,      # keep in memory for dashboard
    "thumbnail_width": 160,
    "thumbnail_height": 120,
    "thumbnail_quality": 40,
    "terminal_output": True,        # print trace summaries to terminal
}
```

---

## 8. Example Trace JSON (one line in traces.jsonl)

```json
{
  "trace_id": "abc12345",
  "timestamp": "2026-03-04T22:15:30.123Z",
  "user_message": "what do you see?",
  "final_response": "I can see a bedroom with a bed, a nightstand with a lamp, and a window on the right side.",
  "total_duration_ms": 2230,
  "total_cost_usd": 0.004,
  "total_tokens_in": 2050,
  "total_tokens_out": 215,
  "drone_state_snapshot": {
    "position": {"x": 0, "y": 100, "z": 80},
    "heading_deg": 90,
    "battery_pct": 85,
    "is_flying": true
  },
  "spans": [
    {
      "span_id": "s1a2b3c4",
      "span_type": "llm_call",
      "name": "gemini-3.5-flash",
      "start_time": 1709589330.123,
      "duration_ms": 1200,
      "status": "ok",
      "model": "gemini-3.5-flash",
      "thinking_level": "medium",
      "tokens_in": 850,
      "tokens_out": 120,
      "thinking_tokens": 0,
      "cost_usd": 0.002
    },
    {
      "span_id": "s4d5e6f7",
      "span_type": "tool_call",
      "name": "get_frame",
      "start_time": 1709589331.323,
      "duration_ms": 80,
      "status": "ok",
      "tool_name": "get_frame",
      "tool_args": {},
      "tool_result": {"success": true, "message": "Frame captured and analyzed"}
    },
    {
      "span_id": "s7g8h9i0",
      "span_type": "vision_call",
      "name": "describe_scene",
      "start_time": 1709589331.403,
      "duration_ms": 900,
      "status": "ok",
      "vision_model": "gemini-3.5-flash",
      "vision_mode": "reasoning",
      "vision_prompt": "Describe what you see in this drone camera image...",
      "vision_response": "The image shows a bedroom with a bed centered in frame...",
      "frame_thumbnail_b64": "/9j/4AAQ...(small base64)...",
      "json_parse_success": null,
      "tokens_in": 1200,
      "tokens_out": 95,
      "cost_usd": 0.002
    }
  ]
}
```

---

## 9. Testing

Add `tests/test_tracing.py`:
- Test Trace/Span creation and serialization
- Test TraceManager start/add/end lifecycle
- Test JSONL file writing (use tmp directory)
- Test cost calculator with known inputs
- Test thumbnail generation dimensions and size
- Test terminal formatter output

---

## 10. Non-Goals (for now)

- No distributed tracing (no Jaeger/Zipkin/OpenTelemetry) — this is a single-process system
- No log rotation — file will be small for personal project use
- No auth on trace endpoints — dashboard is local only
- No trace search/filter — just chronological list
