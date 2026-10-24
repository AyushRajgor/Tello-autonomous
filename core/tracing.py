"""
Tracers and Spans mapping to track agent's execution.
"""
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from config import TRACING

# Define custom log format for tracing
logger = logging.getLogger("tracing")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    # Prevent duplicate logging to root logger
    logger.propagate = False


@dataclass
class Span:
    span_id: str
    trace_id: str
    span_type: str            # "llm_call" | "tool_call" | "vision_call" | "user_message" | "response"
    name: str
    start_time: float
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    status: str = "ok"        # "ok" | "error"
    error: Optional[str] = None

    # LLM-specific
    model: Optional[str] = None
    input_messages: Optional[list] = None
    output_message: Optional[list] = None
    model_parameters: Optional[dict] = None
    thinking_level: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    thinking_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    thought_summary: Optional[str] = None

    # Tool-specific
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    tool_result: Optional[dict] = None

    # Vision-specific
    vision_model: Optional[str] = None
    vision_prompt: Optional[str] = None
    vision_response: Optional[str] = None
    vision_mode: Optional[str] = None
    frame_thumbnail_b64: Optional[str] = None
    json_parse_success: Optional[bool] = None


@dataclass
class Trace:
    trace_id: str
    timestamp: str            # ISO 8601
    user_message: str
    drone_state_snapshot: dict
    final_response: Optional[str] = None
    total_duration_ms: Optional[float] = None
    total_cost_usd: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    spans: list[Span] = field(default_factory=list)


class TraceManager:
    def __init__(self, log_dir: str = "logs"):
        self.current_traces: dict[str, Trace] = {}
        self.completed_traces: list[Trace] = []
        
        self.log_dir = log_dir
        if TRACING["enabled"]:
            os.makedirs(self.log_dir, exist_ok=True)
            
        self.max_traces = TRACING.get("max_recent_traces", 100)
        self.terminal_output = TRACING.get("terminal_output", True)
        self.log_file = os.path.join(self.log_dir, TRACING.get("log_file", "traces.jsonl"))

    def start_trace(self, user_message: str, drone_state: dict) -> str:
        """Start a new trace. Returns trace_id."""
        trace_id = uuid.uuid4().hex[:8]
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        self.current_traces[trace_id] = Trace(
            trace_id=trace_id,
            timestamp=timestamp,
            user_message=user_message,
            drone_state_snapshot=drone_state,
        )
        return trace_id

    def add_span(self, trace_id: str, span: Span):
        """Add a completed span to a trace."""
        if trace_id in self.current_traces:
            self.current_traces[trace_id].spans.append(span)

    def end_trace(self, trace_id: str, final_response: str) -> Optional[Trace]:
        """Finalize trace, compute totals, write to file, log to terminal."""
        if trace_id not in self.current_traces:
            return None
            
        trace = self.current_traces.pop(trace_id)
        trace.final_response = final_response
        
        # Calculate totals
        if trace.spans:
            first_start = min(s.start_time for s in trace.spans)
            # Find max end_time, fallback to start_time if missing
            last_end = max((s.end_time if s.end_time else s.start_time) for s in trace.spans)
            trace.total_duration_ms = (last_end - first_start) * 1000
        else:
            trace.total_duration_ms = 0.0

        for span in trace.spans:
            if span.cost_usd:
                trace.total_cost_usd += span.cost_usd
            if span.tokens_in:
                trace.total_tokens_in += span.tokens_in
            if span.tokens_out:
                trace.total_tokens_out += span.tokens_out
                
        # Store in recent memory
        self.completed_traces.append(trace)
        if len(self.completed_traces) > self.max_traces:
            self.completed_traces = self.completed_traces[-self.max_traces:]
            
        if TRACING["enabled"]:
            self._write_trace_to_file(trace)
            if self.terminal_output:
                self._log_to_terminal(trace)
                
        return trace

    def _write_trace_to_file(self, trace: Trace):
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(trace)) + "\n")
        except Exception as e:
            # We use root logger here just in case tracing logger misses it
            logging.getLogger(__name__).error(f"Failed to write trace: {e}")

    def _log_to_terminal(self, trace: Trace):
        truncated_msg = trace.user_message[:30] + "..." if len(trace.user_message) > 30 else trace.user_message
        
        dur = trace.total_duration_ms or 0
        spans_count = len(trace.spans)
        
        # Header
        logger.info(f'[TRACE {trace.trace_id}] "{truncated_msg}" → {dur:.0f}ms | ${trace.total_cost_usd:.4f} | {spans_count} spans')
        
        # Each span
        for i, span in enumerate(trace.spans):
            is_last = (i == len(trace.spans) - 1)
            tree_char = "└─" if is_last else "├─"
            
            span_dur = span.duration_ms or 0
            
            if span.span_type == "llm_call":
                cost = span.cost_usd or 0
                t_in = span.tokens_in or 0
                t_out = span.tokens_out or 0
                logger.info(f"  {tree_char} {span.span_type:<10} {str(span.model)[:15]:<15} {span_dur:>5.0f}ms {t_in:>4}→{t_out:<4} tokens  ${cost:.4f}")
            elif span.span_type == "vision_call":
                cost = span.cost_usd or 0
                t_in = span.tokens_in or 0
                t_out = span.tokens_out or 0
                logger.info(f"  {tree_char} {span.span_type:<10} {str(span.vision_model)[:15]:<15} {span_dur:>5.0f}ms {t_in:>4}→{t_out:<4} tokens  ${cost:.4f}")
            elif span.span_type == "tool_call":
                status = span.status
                logger.info(f"  {tree_char} {span.span_type:<10} {str(span.tool_name)[:15]:<15} {span_dur:>5.0f}ms   {status}")
            else:
                logger.info(f"  {tree_char} {span.span_type:<10} {str(span.name)[:15]:<15} {span_dur:>5.0f}ms")

    def get_recent_traces(self, limit: int = 50) -> list[dict]:
        """Return recent traces as dicts (for dashboard API)."""
        limit = min(limit, len(self.completed_traces))
        return [asdict(t) for t in reversed(self.completed_traces[-limit:])]

    def get_trace(self, trace_id: str) -> Optional[dict]:
        """Get a single trace with all spans (for detail view)."""
        for trace in self.completed_traces:
            if trace.trace_id == trace_id:
                return asdict(trace)
        return None
