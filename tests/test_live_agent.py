"""
Tests for voice/gemini_live.py (LiveAgent) against a fake Live client — no network.

Run: python -m pytest tests/test_live_agent.py -v
"""

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
from google import genai
from google.genai import types

import voice.gemini_live as gl
from config import LIVE, MODELS
from core.toolkit import TelloToolkit
from voice.gemini_live import LiveAgent, build_live_config, match_stop_word


# ── Fakes ──────────────────────────────────────────────────────────────────

class FakeSession:
    """Stands in for google.genai.live.AsyncSession."""

    def __init__(self, name="session"):
        self.name = name
        self.sent = []                      # [(kind, payload)]
        self.inbox = asyncio.Queue()        # server -> client messages (or exceptions to raise)
        self.closed = False

    async def send_realtime_input(self, **kwargs):
        assert len(kwargs) == 1, f"SDK allows one argument per call, got {list(kwargs)}"
        (kind, value), = kwargs.items()
        self.sent.append((kind, value))

    async def send_client_content(self, *, turns=None, turn_complete=True):
        self.sent.append(("client_content", (turns, turn_complete)))

    async def send_tool_response(self, *, function_responses):
        for response in function_responses:
            assert response.id, "Developer API requires FunctionResponse.id"
        self.sent.append(("tool_response", function_responses))

    def push(self, message):
        self.inbox.put_nowait(message)

    async def receive(self):
        # Like the SDK: yields one turn, then returns after turn_complete.
        while True:
            item = await self.inbox.get()
            if isinstance(item, BaseException):
                raise item
            yield item
            sc = getattr(item, "server_content", None)
            if sc is not None and sc.turn_complete:
                return

    def kinds(self):
        return [kind for kind, _ in self.sent]

    def of(self, kind):
        return [payload for k, payload in self.sent if k == kind]


class _FakeConnect:
    def __init__(self, item):
        self.item = item

    async def __aenter__(self):
        if isinstance(self.item, BaseException):
            raise self.item
        return self.item

    async def __aexit__(self, *exc):
        if isinstance(self.item, FakeSession):
            self.item.closed = True
        return False


class FakeLive:
    """client.aio.live: hands out planned sessions (or connect errors) in order."""

    def __init__(self, plan):
        self.plan = list(plan)
        self.models = []
        self.configs = []

    def connect(self, *, model, config):
        self.models.append(model)
        self.configs.append(config)
        item = self.plan.pop(0) if self.plan else ConnectionError("no more fake sessions")
        return _FakeConnect(item)


def make_agent(plan, push_to_talk=False):
    toolkit = TelloToolkit(tello=None, simulate=True)
    toolkit.connect()
    agent = LiveAgent(toolkit, push_to_talk=push_to_talk)
    live = FakeLive(plan)
    agent.client = SimpleNamespace(aio=SimpleNamespace(live=live))
    agent._frame_interval = 0.05  # keep the 1-FPS cadence out of test runtime
    texts = []
    agent.on_text(lambda role, text: texts.append((role, text)))
    return agent, live, texts


async def wait_until(predicate, timeout=3.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


def system_texts(texts):
    return [text for role, text in texts if role == "system"]


# Real SDK message types, so field-name mistakes fail here too.
def msg_resumption(handle, resumable=True):
    return types.LiveServerMessage(
        session_resumption_update=types.LiveServerSessionResumptionUpdate(new_handle=handle, resumable=resumable)
    )


def msg_go_away(time_left="20s"):
    return types.LiveServerMessage(go_away=types.LiveServerGoAway(time_left=time_left))


def msg_tool_call(*calls):
    return types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[
        types.FunctionCall(id=call_id, name=name, args=args) for call_id, name, args in calls
    ]))


def msg_cancel(*ids):
    return types.LiveServerMessage(tool_call_cancellation=types.LiveServerToolCallCancellation(ids=list(ids)))


def msg_heard(text, finished=True):
    return types.LiveServerMessage(server_content=types.LiveServerContent(
        input_transcription=types.Transcription(text=text, finished=finished)
    ))


@pytest.fixture
def fast_reconnect(monkeypatch):
    monkeypatch.setitem(LIVE, "reconnect_backoff_s", 0.0)
    monkeypatch.setitem(LIVE, "reconnect_max_attempts", 3)


# ── Connect config ─────────────────────────────────────────────────────────

class TestConfig:
    def test_uses_live_model_and_typed_config(self):
        async def scenario():
            session = FakeSession()
            agent, live, _ = make_agent([session])
            await agent.start()
            await agent.stop()
            return live

        live = asyncio.run(scenario())
        assert live.models == [MODELS["live"]]
        assert isinstance(live.configs[0], types.LiveConnectConfig)

    def test_start_without_api_key_raises(self, monkeypatch):
        monkeypatch.setattr(gl, "GEMINI_API_KEY", "")
        agent = LiveAgent(TelloToolkit(tello=None, simulate=True))
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            asyncio.run(agent.start())
        assert agent._running is False

    def test_every_tool_is_blocking_and_operator_actions_are_absent(self):
        cfg = build_live_config()
        decls = [d for tool in cfg.tools for d in tool.function_declarations]
        assert decls
        assert all(d.behavior == types.Behavior.BLOCKING for d in decls)
        names = {d.name for d in decls}
        assert {"takeoff", "land", "move", "rotate"} <= names
        assert not names & {"emergency_stop", "flip", "hover"}
        assert not names & set(LIVE["disabled_tools"])

    def test_compression_resumption_and_no_thinking(self):
        cfg = build_live_config(resume_handle="handle-1")
        assert cfg.context_window_compression.trigger_tokens == LIVE["compression_trigger_tokens"]
        assert cfg.context_window_compression.sliding_window.target_tokens == LIVE["compression_target_tokens"]
        assert cfg.session_resumption.handle == "handle-1"
        assert build_live_config().session_resumption is not None  # updates requested from the start
        assert cfg.thinking_config is None
        assert cfg.temperature is None
        assert cfg.enable_affective_dialog is None
        assert cfg.proactivity is None
        assert cfg.generation_config is None
        assert cfg.response_modalities == [types.Modality.AUDIO]
        assert cfg.media_resolution == types.MediaResolution[LIVE["media_resolution"]]
        assert cfg.input_audio_transcription is not None
        assert cfg.output_audio_transcription is not None
        assert cfg.speech_config.voice_config.prebuilt_voice_config.voice_name == LIVE["voice_name"]
        assert cfg.system_instruction == gl.VOICE_SYSTEM_PROMPT

    def test_push_to_talk_disables_automatic_activity_detection(self):
        rt = build_live_config(push_to_talk=True).realtime_input_config
        assert rt.automatic_activity_detection.disabled is True
        assert rt.activity_handling == types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS
        # Frames are rationed client-side in PTT, so all of them must count
        # (including the one sent after a move, outside user speech).
        assert rt.turn_coverage == types.TurnCoverage.TURN_INCLUDES_AUDIO_ACTIVITY_AND_ALL_VIDEO
        assert rt.turn_coverage == types.TurnCoverage[LIVE["turn_coverage_ptt"]]

    def test_open_mic_keeps_automatic_vad_with_live_settings(self):
        rt = build_live_config(push_to_talk=False).realtime_input_config
        assert rt.turn_coverage == types.TurnCoverage[LIVE["turn_coverage"]]
        aad = rt.automatic_activity_detection
        assert aad.disabled is not True
        assert aad.start_of_speech_sensitivity == types.StartSensitivity[LIVE["vad_start_sensitivity"]]
        assert aad.silence_duration_ms == LIVE["vad_silence_duration_ms"]
        assert aad.prefix_padding_ms == LIVE["vad_prefix_padding_ms"]

    def test_config_serializes_for_the_gemini_developer_api(self):
        # Runs the installed SDK's own setup-message converter offline, so a
        # field the Developer API rejects (Vertex-only options) raises here.
        common = pytest.importorskip("google.genai._common")
        converters = pytest.importorskip("google.genai._live_converters")
        api_client = genai.Client(api_key="offline-test-key")._api_client
        for push_to_talk in (False, True):
            cfg = build_live_config(push_to_talk=push_to_talk, resume_handle="h")
            params = types.LiveConnectParameters(
                model=f"models/{MODELS['live']}", config=cfg,
            ).model_dump(exclude_none=True)
            request = common.convert_to_dict(
                converters._LiveConnectParameters_to_mldev(api_client=api_client, from_object=params)
            )
            request.pop("config", None)
            setup = json.loads(json.dumps(common.encode_unserializable_types(request)))["setup"]
            declarations = setup["tools"][0]["functionDeclarations"]
            assert {d["behavior"] for d in declarations} == {"BLOCKING"}
            assert "thinkingConfig" not in setup.get("generationConfig", {})
            assert setup["sessionResumption"] == {"handle": "h"}
            assert "contextWindowCompression" in setup
            assert setup["realtimeInputConfig"]


# ── Push-to-talk and open-mic turn handling ────────────────────────────────

class TestTurns:
    def test_ptt_begin_and_end_send_activity_signals_and_flush(self, monkeypatch):
        monkeypatch.setitem(LIVE, "ptt_tail_ms", 50)

        async def scenario():
            session = FakeSession()
            agent, _, _ = make_agent([session], push_to_talk=True)
            flushes = []
            agent.on_interrupt(lambda: flushes.append(True))
            await agent.start()
            try:
                await asyncio.sleep(0.3)
                assert "video" not in session.kinds()   # no frames (tokens) while idle in PTT mode

                agent.toolkit.state.operator_hold = True
                await agent.begin_user_turn()
                assert flushes == [True]
                assert agent.toolkit.state.operator_hold is False
                assert session.kinds()[0] == "activity_start"
                await wait_until(lambda: "video" in session.kinds())  # one fresh frame for the turn

                # No half-duplex echo gate in push-to-talk: this is deliberate barge-in.
                agent._model_speaking = True
                agent._last_model_audio_ts = time.time()
                await agent.send_audio(b"\x01\x00" * 160)
                assert "audio" in session.kinds()

                await agent.end_user_turn()          # returns at once; the tail runs in the background
                await agent.send_audio(b"tail")      # still forwarded during the tail
                assert "activity_end" not in session.kinds()
                await wait_until(lambda: "activity_end" in session.kinds())
                kinds = session.kinds()
                last_audio = max(i for i, k in enumerate(kinds) if k == "audio")
                assert last_audio < kinds.index("activity_end")
                assert session.of("audio")[-1].data == b"tail"
                assert agent._turn_user_ts > 0       # response-latency timer running

                await agent.send_audio(b"after")     # outside a turn: dropped
                assert session.of("audio")[-1].data == b"tail"
            finally:
                await agent.stop()

        asyncio.run(scenario())

    def test_ptt_repress_during_tail_keeps_activity_open(self, monkeypatch):
        monkeypatch.setitem(LIVE, "ptt_tail_ms", 80)

        async def scenario():
            session = FakeSession()
            agent, _, _ = make_agent([session], push_to_talk=True)
            await agent.start()
            try:
                await agent.begin_user_turn()
                await agent.end_user_turn()
                await agent.begin_user_turn()        # pressed again before activity_end went out
                await asyncio.sleep(0.25)
                assert session.kinds().count("activity_start") == 1
                assert "activity_end" not in session.kinds()
                await agent.end_user_turn(wait=True)
                assert session.kinds().count("activity_end") == 1
            finally:
                await agent.stop()

        asyncio.run(scenario())

    def test_open_mic_turn_methods_only_do_latency_bookkeeping(self):
        async def scenario():
            session = FakeSession()
            agent, _, _ = make_agent([session], push_to_talk=False)
            flushes = []
            agent.on_interrupt(lambda: flushes.append(True))
            await agent.start()
            try:
                agent.toolkit.state.operator_hold = True
                await agent.begin_user_turn()
                await agent.end_user_turn()
                assert flushes == []
                assert agent.toolkit.state.operator_hold is True
                assert not {"activity_start", "activity_end"} & set(session.kinds())
                assert agent._turn_user_ts > 0

                # Half-duplex echo gate stays on for laptop speakers.
                agent._model_speaking = True
                await agent.send_audio(b"echo")
                assert "audio" not in session.kinds()
            finally:
                await agent.stop()

        asyncio.run(scenario())

    @pytest.mark.parametrize("push_to_talk", [False, True])
    def test_send_text_clears_operator_hold_and_sends_a_complete_turn(self, push_to_talk):
        async def scenario():
            session = FakeSession()
            agent, _, texts = make_agent([session], push_to_talk=push_to_talk)
            await agent.start()
            try:
                agent.toolkit.state.operator_hold = True
                await agent.send_text("take off")
                assert agent.toolkit.state.operator_hold is False
                (turns, turn_complete), = session.of("client_content")
                assert turn_complete is True
                assert turns.role == "user"
                assert turns.parts[0].text == "take off"
                assert ("user", "take off") in texts
            finally:
                await agent.stop()

        asyncio.run(scenario())

    def test_send_text_with_release_hold_false_keeps_operator_hold(self):
        # Dashboard "Observe": camera-only, must not lift a human STOP.
        async def scenario():
            session = FakeSession()
            agent, _, texts = make_agent([session], push_to_talk=True)
            await agent.start()
            try:
                agent.toolkit.state.operator_hold = True
                await agent.send_text("Describe what you see.", release_hold=False)
                assert agent.toolkit.state.operator_hold is True
                (turns, turn_complete), = session.of("client_content")
                assert turn_complete is True
                assert turns.parts[0].text == "Describe what you see."
                assert ("user", "Describe what you see.") in texts
            finally:
                await agent.stop()

        asyncio.run(scenario())

    def test_send_system_notice_keeps_operator_hold(self):
        async def scenario():
            session = FakeSession()
            agent, _, texts = make_agent([session], push_to_talk=True)
            await agent.start()
            try:
                agent.toolkit.state.operator_hold = True
                await agent.send_system_notice("Battery low (19%)")
                assert agent.toolkit.state.operator_hold is True
                (turns, turn_complete), = session.of("client_content")
                assert turn_complete is True
                text = turns.parts[0].text
                assert text.startswith("[SAFETY NOTICE")
                assert "Battery low (19%)" in text
                assert not [t for role, t in texts if role == "user"]
            finally:
                await agent.stop()

        asyncio.run(scenario())

    def test_interrupted_always_flushes_playback(self):
        async def scenario():
            agent, _, _ = make_agent([])
            flushes = []
            agent.on_interrupt(lambda: flushes.append(True))
            agent._model_speaking = True
            await agent._handle_response(types.LiveServerMessage(
                server_content=types.LiveServerContent(interrupted=True)
            ))
            return agent, flushes

        agent, flushes = asyncio.run(scenario())
        assert flushes == [True]
        assert agent._model_speaking is False


# ── Tool calls ─────────────────────────────────────────────────────────────

class TestToolCalls:
    def test_tool_call_runs_as_task_and_responds_with_matching_id(self):
        async def scenario():
            session = FakeSession()
            agent, _, _ = make_agent([session], push_to_talk=True)
            agent.toolkit.takeoff()
            gate = threading.Event()
            real_execute = agent.toolkit.execute

            def slow_execute(name, args):
                gate.wait(5)
                result = real_execute(name, args)
                session.sent.append(("executed", name))
                return result

            agent.toolkit.execute = slow_execute
            seen = []
            agent.on_tool(lambda name, args, result: seen.append((name, args, result)))
            await agent.start()
            try:
                session.push(msg_tool_call(("call-1", "move", {"direction": "forward", "cm": 50})))
                await wait_until(lambda: "call-1" in agent._tool_calls and agent._tool_calls["call-1"].started)

                # The receive loop keeps reading while the drone is moving.
                session.push(msg_resumption("handle-during-move"))
                await wait_until(lambda: agent._resume_handle == "handle-during-move")
                assert "tool_response" not in session.kinds()
            finally:
                gate.set()
            try:
                await wait_until(lambda: "tool_response" in session.kinds())
                (responses,) = session.of("tool_response")
                assert len(responses) == 1
                assert responses[0].id == "call-1"
                assert responses[0].name == "move"
                result = responses[0].response["result"]
                assert result["success"] is True
                assert result["drone"]["is_flying"] is True

                kinds = session.kinds()
                executed, responded = kinds.index("executed"), kinds.index("tool_response")
                assert "video" in kinds[executed:responded]  # fresh frame after the move, before the response
                assert [name for name, _, _ in seen] == ["move"]
                assert not agent._tool_calls
            finally:
                await agent.stop()

        asyncio.run(scenario())

    def test_cancelling_an_executing_move_hovers_and_skips_queued_calls(self):
        async def scenario():
            session = FakeSession()
            agent, _, texts = make_agent([session], push_to_talk=True)
            started = threading.Event()
            release = threading.Event()
            executed = []
            hovers = []

            def blocking_execute(name, args):
                executed.append(name)
                started.set()
                release.wait(5)
                return {"success": True, "message": f"{name} done"}

            def fake_hover(source="operator"):
                hovers.append(source)
                release.set()   # the stop command aborts the in-flight move
                return {"success": True, "message": "Hovering in place"}

            agent.toolkit.execute = blocking_execute
            agent.toolkit.hover = fake_hover
            await agent.start()
            try:
                session.push(msg_tool_call(
                    ("m1", "move", {"direction": "forward", "cm": 100}),
                    ("r1", "rotate", {"degrees": 90}),
                ))
                await wait_until(started.is_set)
                session.push(msg_cancel("m1", "r1"))
                await wait_until(lambda: bool(hovers))
                await wait_until(lambda: not agent._tool_calls)
            finally:
                release.set()
                await agent.stop()
            return session, executed, hovers, texts, agent

        session, executed, hovers, texts, agent = asyncio.run(scenario())
        assert executed == ["move"]                  # queued rotate never ran
        assert hovers == ["voice_interrupt"]  # logged as a voice abort, not an operator stop
        assert "tool_response" not in session.kinds()  # no responses for cancelled ids
        assert agent.toolkit.state.operator_hold is False  # a barge-in is not an operator stop
        assert any("aborting move" in t for t in system_texts(texts))


# ── Session resumption / reconnect ─────────────────────────────────────────

class TestReconnect:
    def test_go_away_reconnects_with_latest_resumable_handle(self):
        async def scenario():
            first, second = FakeSession("first"), FakeSession("second")
            agent, live, texts = make_agent([first, second])
            await agent.start()
            try:
                first.push(msg_resumption("h1"))
                first.push(msg_resumption("", resumable=False))   # mid-generation: not resumable
                first.push(msg_resumption("h2"))
                first.push(msg_go_away("20s"))
                await wait_until(lambda: agent._session is second)

                assert live.configs[0].session_resumption.handle is None
                assert live.configs[1].session_resumption.handle == "h2"
                assert first.closed
                assert system_texts(texts) == []   # routine connection refresh is silent

                # Video pump and receive loop survive the reconnect.
                await wait_until(lambda: "video" in second.kinds())
                second.push(msg_resumption("h3"))
                await wait_until(lambda: agent._resume_handle == "h3")
            finally:
                await agent.stop()
            assert second.closed

        asyncio.run(scenario())

    def test_go_away_lets_an_in_flight_tool_finish_on_the_old_connection(self):
        async def scenario():
            first, second = FakeSession("first"), FakeSession("second")
            agent, live, _ = make_agent([first, second], push_to_talk=True)
            gate = threading.Event()
            agent.toolkit.execute = lambda name, args: (gate.wait(5), {"success": True, "battery_pct": 90})[1]
            await agent.start()
            try:
                first.push(msg_resumption("h1"))
                first.push(msg_tool_call(("b1", "get_battery", {})))
                first.push(msg_resumption("", resumable=False))
                first.push(msg_go_away("30s"))
                await wait_until(lambda: agent._go_away_session is first)
                await asyncio.sleep(0.1)
                assert agent._session is first             # still draining the tool
                gate.set()
                await wait_until(lambda: "tool_response" in first.kinds())
                assert agent._session is first             # waits for a resumable handle
                first.push(msg_resumption("h2"))
                await wait_until(lambda: agent._session is second)
                assert live.configs[1].session_resumption.handle == "h2"
                assert first.of("tool_response")[0][0].id == "b1"
            finally:
                gate.set()
                await agent.stop()

        asyncio.run(scenario())

    @pytest.mark.parametrize("drop", ["receive_error", "go_away_deadline"])
    def test_dropped_connection_cancels_queued_calls_and_reports_once_on_the_new_one(
        self, drop, fast_reconnect, monkeypatch,
    ):
        monkeypatch.setattr(gl, "_GO_AWAY_MARGIN_S", 0.0)

        async def scenario():
            first, second = FakeSession("first"), FakeSession("second")
            agent, live, _ = make_agent([first, second], push_to_talk=True)
            started, release = threading.Event(), threading.Event()
            executed, hovers = [], []

            def gated_execute(name, args):
                executed.append((name, dict(args)))
                started.set()
                release.wait(5)
                return {"success": True, "message": f"Moved {args['direction']} {args['cm']}cm"}

            def fake_hover(source="operator"):
                hovers.append(source)
                return {"success": True, "message": "Hovering in place"}

            agent.toolkit.execute = gated_execute
            agent.toolkit.hover = fake_hover
            await agent.start()
            try:
                first.push(msg_resumption("h1"))
                first.push(msg_tool_call(
                    ("c1", "move", {"direction": "forward", "cm": 50}),
                    ("c2", "move", {"direction": "left", "cm": 30}),
                ))
                await wait_until(started.is_set)          # c1 is flying, c2 is queued
                if drop == "receive_error":
                    first.push(RuntimeError("websocket closed (1006)"))
                else:
                    first.push(msg_go_away("0.2s"))       # deadline passes while c1 still runs
                await wait_until(lambda: agent._session is second)
                assert live.configs[1].session_resumption.handle == "h1"
                assert not [rec for rec in agent._tool_calls.values() if rec.name == "move" and not rec.started]

                # The new connection can't cancel (or answer) calls the old one issued.
                second.push(msg_cancel("c1", "c2"))
                second.push(msg_resumption("h2"))
                await wait_until(lambda: agent._resume_handle == "h2")
                assert hovers == []
                assert "client_content" not in second.kinds()   # notice waits for c1's result

                release.set()
                await wait_until(lambda: "client_content" in second.kinds())
                await wait_until(lambda: not agent._tool_calls and not agent._bg_tasks)
                await asyncio.sleep(0.1)                  # nothing else trickles in late
            finally:
                release.set()
                await agent.stop()
            return first, second, executed

        first, second, executed = asyncio.run(scenario())
        assert executed == [("move", {"direction": "forward", "cm": 50})]   # c2 never executed
        assert "tool_response" not in first.kinds()
        assert "tool_response" not in second.kinds()     # old ids never answered on the new connection
        (turns, turn_complete), = second.of("client_content")  # exactly one notice
        assert turn_complete is True
        notice = turns.parts[0].text
        assert notice.startswith("[SAFETY NOTICE")
        assert "Voice link dropped mid-action." in notice
        assert "Completed: move(forward 50) → success" in notice
        assert "Not executed (cancelled): move(left 30)." in notice
        assert "Current drone status:" in notice
        assert "Do not repeat actions without checking." in notice

    def test_reconnect_without_pending_tool_calls_sends_no_notice(self, fast_reconnect):
        async def scenario():
            first, second = FakeSession("first"), FakeSession("second")
            agent, _, _ = make_agent([first, second], push_to_talk=True)
            await agent.start()
            try:
                first.push(msg_tool_call(("b1", "get_battery", {})))
                await wait_until(lambda: "tool_response" in first.kinds())
                first.push(RuntimeError("websocket closed (1006)"))
                await wait_until(lambda: agent._session is second)
                await asyncio.sleep(0.2)
                assert "client_content" not in second.kinds()
            finally:
                await agent.stop()

        asyncio.run(scenario())

    def test_receive_error_backs_off_and_gives_up_after_max_attempts(self, fast_reconnect, monkeypatch):
        monkeypatch.setitem(LIVE, "reconnect_backoff_s", 0.125)
        monkeypatch.setattr(gl, "_SEND_WAIT_S", 0.05)
        real_sleep = asyncio.sleep
        sleeps = []

        async def recording_sleep(delay, *args, **kwargs):
            sleeps.append(delay)
            await real_sleep(0)

        async def scenario():
            first = FakeSession("first")
            plan = [first, ConnectionError("refused 1"), ConnectionError("refused 2"), ConnectionError("refused 3")]
            agent, live, texts = make_agent(plan)
            await agent.start()
            try:
                first.push(msg_resumption("h1"))
                await wait_until(lambda: agent._resume_handle == "h1")
                monkeypatch.setattr(asyncio, "sleep", recording_sleep)
                first.push(RuntimeError("websocket closed (1006)"))
                await wait_until(lambda: agent.link_failed)
                monkeypatch.setattr(asyncio, "sleep", real_sleep)

                assert len(live.configs) == 4
                # Two failed resumes with h1, then the handle is dropped for a fresh session.
                assert [c.session_resumption.handle for c in live.configs[1:]] == ["h1", "h1", None]
                backoff = [s for s in sleeps if s in (0.125, 0.25, 0.5)]
                assert backoff == [0.125, 0.25, 0.5]
                system = system_texts(texts)
                assert system[0] == "Voice link reconnecting…"
                assert system[-1] == "Voice link failed — restart required"
                assert agent._session is None

                # Input while the link is down must not crash.
                await agent.send_audio(b"pcm")
                assert await agent.send_video_frame(b"jpeg") is False
                await agent.send_text("hello")
                assert "restart required" in system_texts(texts)[-1]
            finally:
                monkeypatch.setattr(asyncio, "sleep", real_sleep)
                await agent.stop()

        asyncio.run(scenario())

    def test_receive_error_reconnects_resumes_and_reopens_ptt_activity(self, fast_reconnect):
        async def scenario():
            first, second = FakeSession("first"), FakeSession("second")
            agent, live, texts = make_agent([first, ConnectionError("refused"), second], push_to_talk=True)
            await agent.start()
            try:
                first.push(msg_resumption("h1"))
                await wait_until(lambda: agent._resume_handle == "h1")
                await agent.begin_user_turn()            # user is holding the button
                first.push(RuntimeError("websocket closed (1011)"))
                await wait_until(lambda: agent._session is second)

                assert [c.session_resumption.handle for c in live.configs] == [None, "h1", "h1"]
                assert system_texts(texts) == ["Voice link reconnecting…", "Voice link restored"]
                assert second.kinds()[0] == "activity_start"   # open turn carried over
                await agent.end_user_turn(wait=True)
                assert "activity_end" in second.kinds()
            finally:
                await agent.stop()

        asyncio.run(scenario())

    def test_send_text_while_reconnecting_reports_instead_of_crashing(self, monkeypatch):
        monkeypatch.setattr(gl, "_SEND_WAIT_S", 0.05)

        async def scenario():
            agent, _, texts = make_agent([])
            agent._running = True        # mid-reconnect: running, but no connection
            await agent.send_text("go forward")
            await agent.send_system_notice("Battery low")
            await agent.send_audio(b"pcm")
            await agent.begin_user_turn()
            await agent.end_user_turn()
            agent._running = False
            await agent.stop()
            return texts

        texts = asyncio.run(scenario())
        assert any("reconnecting" in t for t in system_texts(texts))
        assert not [t for role, t in texts if role == "user"]

    def test_stop_cancels_everything(self):
        async def scenario():
            session = FakeSession()
            agent, _, _ = make_agent([session], push_to_talk=True)
            gate = threading.Event()
            agent.toolkit.execute = lambda name, args: (gate.wait(5), {"success": True})[1]
            await agent.start()
            try:
                session.push(msg_tool_call(("g1", "get_height", {})))
                await wait_until(lambda: agent._executing == 1)
                await agent.begin_user_turn()
                await agent.stop()
            finally:
                gate.set()
            assert agent._supervisor_task is None and agent._video_task is None
            assert not agent._bg_tasks and not agent._tool_calls
            assert session.closed
            assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

        asyncio.run(scenario())


# ── Stop-word fast path ────────────────────────────────────────────────────

class TestStopWords:
    @pytest.mark.parametrize("heard, action", [
        ("Stop!", "hover"),
        ("  stop now. ", "hover"),
        ("STOP STOP", "hover"),
        ("Halt.", "hover"),
        ("hover", "hover"),
        ("Freeze!", "hover"),
        ("please stop", "hover"),
        ("Land.", "land"),
        ("land now!", "land"),
    ])
    def test_stop_words_match(self, heard, action):
        assert match_stop_word(heard) == action

    @pytest.mark.parametrize("heard", [
        "don't stop", "stop and turn left", "land on the table", "what do you see", "", "stopwatch",
    ])
    def test_other_utterances_do_not_match(self, heard):
        assert match_stop_word(heard) is None

    def test_spoken_stop_hovers_and_land_lands_without_the_model(self):
        async def scenario():
            agent, _, texts = make_agent([], push_to_talk=True)
            agent.toolkit.takeoff()
            calls = []
            real_operator = agent.toolkit.operator_command

            def recording_operator(action, args=None, source="operator"):
                calls.append(action)
                return real_operator(action, args, source=source)

            agent.toolkit.operator_command = recording_operator

            await agent._handle_response(msg_heard("Stop!"))
            await wait_until(lambda: agent.toolkit.state.operator_hold)
            assert calls == ["hover"]
            assert agent.toolkit.state.is_flying            # hover, not land

            # A transcript delivered in chunks is matched as one utterance.
            await agent._handle_response(msg_heard("Land", finished=False))
            await agent._handle_response(msg_heard(" now.", finished=True))
            await wait_until(lambda: not agent.toolkit.state.is_flying)
            assert calls == ["hover", "land"]

            # Not a bare stop word: left to the model.
            await agent._handle_response(msg_heard("stop and turn left"))
            await asyncio.sleep(0.05)
            assert calls == ["hover", "land"]

            assert any('Heard "Stop!"' in t for t in system_texts(texts))
            # The hold lifts when the user starts a new push-to-talk turn.
            await agent.begin_user_turn()
            assert agent.toolkit.state.operator_hold is False
            await agent.stop()

        asyncio.run(scenario())

    def test_send_text_keeps_hold_when_no_session(self):
        async def scenario():
            agent, _, _ = make_agent([], push_to_talk=True)
            agent.toolkit.operator_command("hover")
            agent._wait_for_session = lambda *a, **k: _none()  # link down
            await agent.send_text("fly forward")
            return agent.toolkit.state.operator_hold

        async def _none():
            return None

        assert asyncio.run(scenario()) is True

    def test_open_mic_new_spoken_instruction_releases_hold(self):
        async def scenario():
            agent, _, _ = make_agent([], push_to_talk=False)
            agent.toolkit.takeoff()
            await agent._handle_response(msg_heard("Stop!"))
            await wait_until(lambda: agent.toolkit.state.operator_hold)
            await asyncio.sleep(0.05)  # let the hover command settle
            await agent._handle_response(msg_heard("okay, turn left a bit"))
            hold_after = agent.toolkit.state.operator_hold
            await agent.stop()
            return hold_after

        assert asyncio.run(scenario()) is False

    def test_push_to_talk_utterance_does_not_release_hold_by_itself(self):
        # In PTT the hold is released by begin_user_turn(), not transcripts.
        async def scenario():
            agent, _, _ = make_agent([], push_to_talk=True)
            agent.toolkit.takeoff()
            agent.toolkit.operator_command("hover")
            await agent._handle_response(msg_heard("what do you see"))
            hold_after = agent.toolkit.state.operator_hold
            await agent.stop()
            return hold_after

        assert asyncio.run(scenario()) is True

    def test_stop_word_wins_over_a_motion_tool_call_in_the_same_turn(self):
        async def scenario():
            agent, _, _ = make_agent([], push_to_talk=False)
            agent.toolkit.takeoff()
            # Transcript never marked finished; the tool call boundary still checks it.
            await agent._handle_response(msg_heard("stop", finished=False))
            await agent._handle_response(msg_tool_call(("m1", "move", {"direction": "forward", "cm": 50})))
            await wait_until(lambda: not agent._tool_calls and not agent._bg_tasks)
            position_y = agent.toolkit.state.position.y
            await agent.stop()
            return agent, position_y

        agent, position_y = asyncio.run(scenario())
        assert agent.toolkit.state.operator_hold is True
        assert position_y == 0          # the move was refused by the operator hold
