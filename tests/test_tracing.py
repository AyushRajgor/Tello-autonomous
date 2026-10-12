import json
import logging
import os
import tempfile
import time
from unittest.mock import patch

from core.cost import calculate_cost, MODEL_PRICING
from core.tracing import Span, Trace, TraceManager
import vision.llm_vision as vision

def test_cost_calculator():
    # gemini-3.5-flash: 1.50/M input, 9.00/M output
    cost = calculate_cost("gemini-3.5-flash", 2_000_000, 1_000_000, 0)
    assert cost == 12.00  # (2 * 1.5 + 1 * 9.0)

    cost_lite = calculate_cost("gemini-3.1-flash-lite", 1_000_000, 1_000_000, 0)
    assert cost_lite == 1.75

    # with thinking tokens
    cost_think = calculate_cost("gemini-3.5-flash", 1_000_000, 0, 1_000_000)
    assert cost_think == 10.50  # 1.5 + 9.0

def test_trace_manager_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = TraceManager(log_dir=tmpdir)
        manager.log_file = os.path.join(tmpdir, "traces.jsonl")
        
        # Start trace
        trace_id = manager.start_trace("fly forward", {"battery_pct": 95})
        assert trace_id in manager.current_traces
        
        # Add span
        span = Span(
            span_id="s123",
            trace_id=trace_id,
            span_type="llm_call",
            name="gemini-3.5-flash",
            start_time=time.time(),
            end_time=time.time() + 1.5,
            duration_ms=1500,
            status="ok",
            tokens_in=100,
            tokens_out=50,
            cost_usd=0.01
        )
        manager.add_span(trace_id, span)
        
        assert len(manager.current_traces[trace_id].spans) == 1
        
        # End trace
        with patch.object(manager, '_log_to_terminal') as mock_log:
            trace = manager.end_trace(trace_id, "done")
            
            assert trace is not None
            assert trace.total_cost_usd == 0.01
            assert trace.total_tokens_in == 100
            assert trace.total_duration_ms > 0
            
            # File writing validation
            assert os.path.exists(manager.log_file)
            with open(manager.log_file, "r") as f:
                lines = f.readlines()
                assert len(lines) == 1
                data = json.loads(lines[0])
                assert data["trace_id"] == trace_id
                assert len(data["spans"]) == 1
                assert data["total_cost_usd"] == 0.01

            mock_log.assert_called_once()
            
def test_trace_manager_terminal_formatter(caplog):
    manager = TraceManager()
    logging.getLogger("tracing").propagate = True
    with caplog.at_level(logging.INFO, logger="tracing"):
        trace_id = manager.start_trace("short message", {"battery_pct": 100})
        span = Span(
            span_id="s123",
            trace_id=trace_id,
            span_type="llm_call",
            name="gemini",
            start_time=1000,
            end_time=1001,
            duration_ms=1000,
            status="ok",
        )
        manager.add_span(trace_id, span)
        manager.end_trace(trace_id, "done")
        
        assert "short message" in caplog.text
        assert "llm_call" in caplog.text
        assert "spans" in caplog.text

def test_trace_and_span_dataclasses():
    trace = Trace(trace_id="1", timestamp="now", user_message="msg", drone_state_snapshot={})
    assert trace.total_cost_usd == 0.0
    
    span = Span(span_id="2", trace_id="1", span_type="tool_call", name="test", start_time=100)
    assert span.status == "ok"

# Could add more vision tests here using PIL if necessary.
