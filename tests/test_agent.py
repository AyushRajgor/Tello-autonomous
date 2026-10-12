"""
Tests for core/agent.py (text function-calling agent).

No network: the Gemini client is replaced by a fake whose
aio.models.generate_content returns crafted GenerateContentResponse objects.
"""

import asyncio
import json
import time
from types import SimpleNamespace

from google.genai import types

import core.agent as agent_module
from config import MODELS, THINKING, VISION
from core.agent import TelloAgent
from core.toolkit import TelloToolkit
from vision.llm_vision import resolve_media_resolution


# ── Fakes ──

def fc_response(*calls, signature=b"sig-1"):
    """Model turn with function calls [(id, name, args), ...]; first call is signed."""
    parts = []
    for i, (call_id, name, args) in enumerate(calls):
        part = types.Part(function_call=types.FunctionCall(id=call_id, name=name, args=args or {}))
        if i == 0:
            part.thought_signature = signature
        parts.append(part)
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
    )


def text_response(text):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(
            role="model",
            parts=[types.Part(text="thinking...", thought=True), types.Part(text=text)],
        ))]
    )


class FakeModels:
    def __init__(self, responses, on_call=None):
        self.responses = list(responses)
        self.calls = []
        self.on_call = on_call

    async def generate_content(self, *, model, contents, config):
        self.calls.append(SimpleNamespace(model=model, contents=list(contents), config=config))
        if self.on_call:
            await self.on_call(len(self.calls))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeTracer:
    def __init__(self):
        self.spans = []
        self.ended = []

    def start_trace(self, user_message, drone_state):
        return "trace-1"

    def add_span(self, trace_id, span):
        self.spans.append(span)

    def end_trace(self, trace_id, final_response):
        self.ended.append(final_response)


def make_agent(responses, toolkit=None, on_call=None, tracer=None):
    toolkit = toolkit or TelloToolkit(simulate=True)
    agent = TelloAgent(toolkit, tracer)
    models = FakeModels(responses, on_call)
    agent.client = SimpleNamespace(aio=SimpleNamespace(models=models))
    return agent, models, toolkit


def make_input(lines):
    it = iter(lines)

    def _input():
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    return _input


def _level(value):
    return str(getattr(value, "value", value)).lower()


# ── Event loop / concurrency ──

def test_tool_execution_does_not_block_event_loop():
    class SlowToolkit(TelloToolkit):
        def execute(self, name, args):
            time.sleep(0.3)  # blocking drone I/O
            return super().execute(name, args)

    agent, _, _ = make_agent(
        [fc_response(("c1", "get_battery", {})), text_response("ok")],
        toolkit=SlowToolkit(simulate=True),
    )

    async def scenario():
        ticks = 0
        done = asyncio.Event()

        async def ticker():
            nonlocal ticks
            while not done.is_set():
                ticks += 1
                await asyncio.sleep(0.01)

        task = asyncio.create_task(ticker())
        reply = await agent.process_message("battery?")
        done.set()
        await task
        return reply, ticks

    reply, ticks = asyncio.run(scenario())
    assert reply == "ok"
    assert ticks >= 10, f"event loop was blocked (only {ticks} ticks during a 0.3s tool call)"


def test_concurrent_messages_are_serialized():
    events = []

    async def on_call(n):
        events.append(("enter", n))
        await asyncio.sleep(0.05)
        events.append(("exit", n))

    agent, models, _ = make_agent([text_response("one"), text_response("two")], on_call=on_call)

    async def scenario():
        return await asyncio.gather(agent.process_message("first"), agent.process_message("second"))

    replies = asyncio.run(scenario())
    assert replies == ["one", "two"]
    assert events == [("enter", 1), ("exit", 1), ("enter", 2), ("exit", 2)]
    assert [c.role for c in agent.conversation_history] == ["user", "model", "user", "model"]
    # The second request saw the complete first turn plus its own message.
    assert len(models.calls[1].contents) == 3


# ── Request config ──

def test_request_config_uses_reasoning_model_without_temperature():
    agent, models, _ = make_agent([text_response("hi")])
    asyncio.run(agent.process_message("hello"))

    call = models.calls[0]
    cfg = call.config
    assert call.model == MODELS["vision_reasoning"]
    assert cfg.temperature is None
    assert _level(cfg.thinking_config.thinking_level) == THINKING["reasoning"]
    assert cfg.thinking_config.include_thoughts is True
    assert cfg.media_resolution is not None
    assert cfg.media_resolution == resolve_media_resolution(VISION["media_resolution"])
    status = call.contents[0].parts[0].text
    assert status.startswith("[Drone Status:") and "Heading:" in status and "User: hello" in status


def test_no_api_key_returns_error():
    agent, _, _ = make_agent([])
    agent.client = None
    assert "GEMINI_API_KEY" in asyncio.run(agent.process_message("hi"))


# ── Multimodal get_frame ──

def test_get_frame_is_sent_as_multimodal_function_response():
    agent, models, toolkit = make_agent([
        fc_response(("call-7", "get_frame", {})),
        text_response("I see a gray wall."),
    ])
    reply = asyncio.run(agent.process_message("what do you see?"))
    assert reply == "I see a gray wall."

    second = models.calls[1]
    fr_content = second.contents[-1]
    assert fr_content.role == "user"
    (part,) = fr_content.parts
    fr = part.function_response
    assert fr.id == "call-7"
    assert fr.name == "get_frame"

    (image_part,) = fr.parts
    assert image_part.inline_data.mime_type == "image/jpeg"
    assert image_part.inline_data.data[:2] == b"\xff\xd8"  # raw JPEG bytes, not base64

    assert "image_base64" not in fr.response
    dumped = json.dumps(fr.response)
    frame_b64 = toolkit.get_frame()["image_base64"]
    assert frame_b64[:40] not in dumped
    assert len(dumped) < 500
    assert fr.response["success"] is True

    # The model turn went back verbatim, thought signature included.
    assert second.contents[-2].parts[0].thought_signature == b"sig-1"


def test_parallel_calls_answered_in_one_turn_with_matching_ids():
    agent, models, _ = make_agent([
        fc_response(("a", "get_battery", {}), ("b", "get_height", {})),
        text_response("done"),
    ])
    asyncio.run(agent.process_message("status"))

    fr_parts = models.calls[1].contents[-1].parts
    assert [p.function_response.id for p in fr_parts] == ["a", "b"]
    assert [p.function_response.name for p in fr_parts] == ["get_battery", "get_height"]
    assert all(not p.function_response.parts for p in fr_parts)


def test_failed_frame_has_no_image_part():
    class NoCamera(TelloToolkit):
        def get_frame(self):
            return {"success": False, "error": "No frame available"}

    agent, models, _ = make_agent(
        [fc_response(("f1", "get_frame", {})), text_response("camera is down")],
        toolkit=NoCamera(simulate=True),
    )
    asyncio.run(agent.process_message("look"))
    fr = models.calls[1].contents[-1].parts[0].function_response
    assert fr.id == "f1"
    assert not fr.parts
    assert fr.response == {"success": False, "error": "No frame available"}


def test_images_replaced_after_turn_completes():
    first_model_turn = fc_response(("call-1", "get_frame", {}))
    agent, models, _ = make_agent([first_model_turn, text_response("seen"), text_response("again")])

    async def scenario():
        await agent.process_message("look")
        await agent.process_message("anything else?")

    asyncio.run(scenario())

    # During the turn the image was sent...
    assert models.calls[1].contents[-1].parts[0].function_response.parts
    # ...afterwards history keeps only a text note, with id/name intact.
    history_fr = agent.conversation_history[2].parts[0].function_response
    assert history_fr.id == "call-1" and history_fr.name == "get_frame"
    assert not history_fr.parts
    assert history_fr.response["image"] == agent_module.FRAME_DROPPED_NOTE
    # Model content is the exact object the API returned (signature untouched).
    assert agent.conversation_history[1] is first_model_turn.candidates[0].content
    # The next request carries no old frames.
    for content in models.calls[2].contents:
        for part in content.parts:
            assert not (part.function_response and part.function_response.parts)
            assert part.inline_data is None


def test_history_trimmed_at_user_text_boundaries(monkeypatch):
    monkeypatch.setattr(agent_module, "MAX_HISTORY_TURNS", 2)
    responses = []
    for i in range(3):
        responses += [fc_response((f"c{i}", "get_battery", {})), text_response(f"reply {i}")]
    agent, _, _ = make_agent(responses)

    async def scenario():
        for i in range(3):
            await agent.process_message(f"message {i}")

    asyncio.run(scenario())

    history = agent.conversation_history
    assert len(history) == 8  # 2 turns x (user text, model call, function response, model text)
    assert history[0].role == "user"
    assert "User: message 1" in history[0].parts[0].text
    assert sum(agent_module._is_user_text_turn(c) for c in history) == 2
    for i, content in enumerate(history):
        call_ids = [p.function_call.id for p in content.parts if p.function_call]
        if call_ids:
            assert [p.function_response.id for p in history[i + 1].parts] == call_ids


# ── Error rollback ──

def test_api_error_rolls_back_history():
    tracer = FakeTracer()
    agent, _, _ = make_agent(
        [text_response("hello"), fc_response(("c1", "get_battery", {})), RuntimeError("503 overloaded")],
        tracer=tracer,
    )

    async def scenario():
        await agent.process_message("hi")
        before = [id(c) for c in agent.conversation_history]
        reply = await agent.process_message("check battery")
        return before, reply

    before, reply = asyncio.run(scenario())
    assert "503 overloaded" in reply
    assert [id(c) for c in agent.conversation_history] == before
    assert len(agent.conversation_history) == 2
    assert tracer.ended[-1] == reply


def test_empty_model_response_rolls_back_history():
    empty = types.GenerateContentResponse(candidates=[types.Candidate(content=None, finish_reason="SAFETY")])
    agent, _, _ = make_agent([empty])
    reply = asyncio.run(agent.process_message("hi"))
    assert "No response from model" in reply
    assert agent.conversation_history == []


# ── Operator hold ──

def test_iteration_limit_closes_turn_with_model_message(monkeypatch):
    monkeypatch.setattr(agent_module, "MAX_ITERATIONS", 2)
    responses = [
        fc_response(("c1", "get_battery", {})),
        fc_response(("c2", "get_battery", {})),
    ]
    agent, _, _ = make_agent(responses)

    reply = asyncio.run(agent.process_message("keep checking the battery"))

    assert reply == agent_module.TIMEOUT_MESSAGE
    last = agent.conversation_history[-1]
    assert last.role == "model"
    assert last.parts[0].text == agent_module.TIMEOUT_MESSAGE


def test_message_queued_before_a_stop_does_not_release_its_hold():
    # Review finding: "fly forward 1m" queued behind a running turn, then STOP —
    # the queued turn used to clear the hold and fly.
    responses = [
        fc_response(("c1", "get_battery", {})),
        text_response("Battery is fine."),
        fc_response(("c2", "move", {"direction": "forward", "cm": 100})),
        text_response("Holding — the operator stopped me."),
    ]
    gate = {}

    async def hold_first_turn(call_number):
        if call_number == 1:  # keep turn 1 running until the STOP has happened
            await gate["stop_done"].wait()

    agent, _, toolkit = make_agent(responses, on_call=hold_first_turn)
    toolkit.connect()
    toolkit.takeoff()  # airborne, so only the hold can stop the queued move

    async def scenario():
        gate["stop_done"] = asyncio.Event()
        first = asyncio.create_task(agent.process_message("check the battery"))
        await asyncio.sleep(0.01)
        queued = asyncio.create_task(agent.process_message("fly forward 1 m"))  # waits behind turn 1
        await asyncio.sleep(0.01)
        await asyncio.to_thread(toolkit.operator_command, "hover")  # STOP after both were sent
        gate["stop_done"].set()
        await first
        return await queued

    asyncio.run(scenario())
    assert toolkit.state.operator_hold is True
    assert toolkit.state.position.y == 0


def test_operator_hold_cleared_at_message_start():
    toolkit = TelloToolkit(simulate=True)
    toolkit.state.operator_hold = True
    seen = []

    async def on_call(n):
        seen.append(toolkit.state.operator_hold)

    agent, models, _ = make_agent([text_response("ok")], toolkit=toolkit, on_call=on_call)
    asyncio.run(agent.process_message("take off again"))

    assert seen == [False]
    first_message = models.calls[0].contents[0].parts[0].text
    assert "OPERATOR HOLD" not in first_message
    assert "released" in first_message


def test_operator_hold_mid_turn_refuses_motion():
    toolkit = TelloToolkit(simulate=True)
    toolkit.takeoff()

    async def on_call(n):
        if n == 1:
            toolkit.state.operator_hold = True  # operator pressed stop while the model was thinking

    agent, models, _ = make_agent(
        [fc_response(("m1", "move", {"direction": "forward", "cm": 50})), text_response("stopped")],
        toolkit=toolkit,
        on_call=on_call,
    )
    asyncio.run(agent.process_message("fly forward"))

    fr = models.calls[1].contents[-1].parts[0].function_response
    assert fr.id == "m1"
    assert fr.response["success"] is False
    assert "Operator hold" in fr.response["error"]
    assert toolkit.state.position.y == 0


# ── Tracing ──

def test_trace_spans_for_llm_and_tool_calls():
    tracer = FakeTracer()
    agent, _, _ = make_agent([fc_response(("c1", "get_frame", {})), text_response("ok")], tracer=tracer)
    asyncio.run(agent.process_message("look"))

    assert [s.span_type for s in tracer.spans] == ["llm_call", "tool_call", "llm_call"]
    llm = tracer.spans[0]
    assert llm.model == MODELS["vision_reasoning"]
    assert "temperature" not in llm.model_parameters
    assert llm.output_message == [{"function_call": "get_frame", "args": {}}]
    assert tracer.spans[2].thought_summary == "thinking..."
    tool = tracer.spans[1]
    assert tool.tool_name == "get_frame"
    assert "image_base64" not in tool.tool_result
    assert tool.frame_thumbnail_b64
    assert "+1 image" in tracer.spans[2].input_messages[-1]["content"]
    assert tracer.ended == ["ok"]


# ── Interactive CLI ──

def test_run_interactive_operator_command_skips_llm(capsys):
    toolkit = TelloToolkit(simulate=True)
    toolkit.takeoff()
    agent, models, _ = make_agent([], toolkit=toolkit)

    asyncio.run(agent.run_interactive(input_fn=make_input(["land"])))

    assert models.calls == []
    assert not toolkit.state.is_flying
    assert toolkit.state.operator_hold  # operator land blocks further AI motion
    out = capsys.readouterr().out
    assert "[operator]" in out
    assert "'!estop'" in out  # startup hint


def test_run_interactive_quit_lands_when_flying():
    toolkit = TelloToolkit(simulate=True)
    toolkit.takeoff()
    agent, models, _ = make_agent([], toolkit=toolkit)

    asyncio.run(agent.run_interactive(input_fn=make_input(["quit", "never read"])))

    assert models.calls == []
    assert not toolkit.state.is_flying


def test_run_interactive_sends_text_to_agent(capsys):
    agent, models, _ = make_agent([text_response("hi there")])

    asyncio.run(agent.run_interactive(input_fn=make_input(["hello"])))

    assert len(models.calls) == 1
    assert "TelloAI: hi there" in capsys.readouterr().out
