import asyncio
from types import SimpleNamespace

from config import LIVE
from core.toolkit import TelloToolkit
from voice.gemini_live import LiveAgent


def _live_response(
    audio: bytes | None = None,
    input_text: str | None = None,
    input_finished: bool | None = None,
    output_text: str | None = None,
    voice_activity_type: str | None = None,
    vad_signal_type: str | None = None,
    turn_complete: bool = False,
):
    parts = []
    if audio is not None:
        parts.append(SimpleNamespace(inline_data=SimpleNamespace(data=audio)))

    return SimpleNamespace(
        tool_call=None,
        server_content=SimpleNamespace(
            model_turn=SimpleNamespace(parts=parts) if parts else None,
            input_transcription=SimpleNamespace(text=input_text, finished=input_finished) if input_text else None,
            output_transcription=SimpleNamespace(text=output_text) if output_text else None,
            interrupted=False,
            turn_complete=turn_complete,
        ),
        voice_activity=SimpleNamespace(voice_activity_type=voice_activity_type) if voice_activity_type else None,
        voice_activity_detection_signal=SimpleNamespace(vad_signal_type=vad_signal_type) if vad_signal_type else None,
    )


class _RecordingSession:
    """Minimal Live session stand-in: records what was sent and when the latency timer was set."""

    def __init__(self, agent):
        self.agent = agent
        self.sent = []
        self.timer_at_send = {}

    async def send_realtime_input(self, **kwargs):
        (kind, _value), = kwargs.items()
        self.sent.append(kind)
        self.timer_at_send.setdefault(kind, self.agent._turn_user_ts)

    async def send_client_content(self, *, turns=None, turn_complete=True):
        self.sent.append("client_content")
        self.timer_at_send.setdefault("client_content", self.agent._turn_user_ts)


def test_latency_metric_emits_once_when_audio_and_transcript_share_packet():
    async def scenario():
        agent = LiveAgent(TelloToolkit(simulate=True))
        metrics = []

        agent.on_metric(lambda name, value: metrics.append((name, value)))
        agent.mark_user_turn_end()

        await agent._handle_response(_live_response(audio=b"first", input_text="hello"))
        await agent._handle_response(_live_response(audio=b"second"))

        return metrics

    metrics = asyncio.run(scenario())

    assert [name for name, _ in metrics] == ["voice_latency_ms"]


def test_send_text_starts_latency_timer_before_network_send_completes():
    async def scenario():
        agent = LiveAgent(TelloToolkit(simulate=True))
        session = _RecordingSession(agent)
        agent._session = session

        await agent.send_text("hello")
        await agent.stop()
        return session

    session = asyncio.run(scenario())

    assert "client_content" in session.sent
    assert session.timer_at_send["client_content"] > 0


def test_mark_user_turn_end_starts_latency_without_transcription():
    async def scenario():
        agent = LiveAgent(TelloToolkit(simulate=True))
        metrics = []

        agent.on_metric(lambda name, value: metrics.append((name, value)))
        agent.mark_user_turn_end()

        await agent._handle_response(_live_response(audio=b"first"))
        return metrics

    metrics = asyncio.run(scenario())

    assert [name for name, _ in metrics] == ["voice_latency_ms"]


def test_mark_user_turn_end_does_not_restart_latency_during_model_speech():
    async def scenario():
        agent = LiveAgent(TelloToolkit(simulate=True))
        metrics = []

        agent.on_metric(lambda name, value: metrics.append((name, value)))
        agent._model_speaking = True
        agent.mark_user_turn_end()

        await agent._handle_response(_live_response(audio=b"already-speaking"))
        return metrics

    metrics = asyncio.run(scenario())

    assert metrics == []


def test_input_transcription_does_not_start_latency_timer():
    async def scenario():
        agent = LiveAgent(TelloToolkit(simulate=True))
        metrics = []

        agent.on_metric(lambda name, value: metrics.append((name, value)))

        await agent._handle_response(_live_response(input_text="hello"))
        await agent._handle_response(_live_response(audio=b"first"))
        return metrics

    metrics = asyncio.run(scenario())

    assert metrics == []


def test_final_input_transcription_starts_latency_for_open_mic_vad_turn():
    async def scenario():
        agent = LiveAgent(TelloToolkit(simulate=True))
        metrics = []

        agent.on_metric(lambda name, value: metrics.append((name, value)))

        await agent._handle_response(_live_response(input_text="hello", input_finished=True))
        await agent._handle_response(_live_response(output_text="Hi"))
        return metrics

    metrics = asyncio.run(scenario())

    assert [name for name, _ in metrics] == ["voice_latency_ms"]


def test_voice_activity_end_starts_latency_for_open_mic_turn():
    async def scenario():
        agent = LiveAgent(TelloToolkit(simulate=True))
        metrics = []

        agent.on_metric(lambda name, value: metrics.append((name, value)))

        await agent._handle_response(_live_response(voice_activity_type="ACTIVITY_END"))
        await agent._handle_response(_live_response(output_text="Hi"))
        return metrics

    metrics = asyncio.run(scenario())

    assert [name for name, _ in metrics] == ["voice_latency_ms"]


def test_vertex_only_vad_signal_is_ignored():
    # voice_activity_detection_signal is allowlisted/Vertex-only; the Developer
    # API path relies on voice_activity, so the old EOS branch was removed.
    async def scenario():
        agent = LiveAgent(TelloToolkit(simulate=True))
        metrics = []

        agent.on_metric(lambda name, value: metrics.append((name, value)))

        await agent._handle_response(_live_response(vad_signal_type="VAD_SIGNAL_TYPE_EOS"))
        await agent._handle_response(_live_response(output_text="Hi"))
        return metrics

    metrics = asyncio.run(scenario())

    assert metrics == []


def test_sent_audio_chunk_provides_latency_fallback_when_vad_is_absent():
    async def scenario():
        agent = LiveAgent(TelloToolkit(simulate=True))
        metrics = []

        class Session:
            async def send_realtime_input(self, audio):
                return None

        agent._session = Session()
        agent.on_metric(lambda name, value: metrics.append((name, value)))

        await agent.send_audio(b"pcm")
        await agent._handle_response(_live_response(output_text="Hi"))
        return metrics

    metrics = asyncio.run(scenario())

    assert [name for name, _ in metrics] == ["voice_latency_ms"]


def test_output_transcription_emits_latency_when_audio_is_not_seen():
    async def scenario():
        agent = LiveAgent(TelloToolkit(simulate=True))
        metrics = []

        agent.on_metric(lambda name, value: metrics.append((name, value)))
        agent.mark_user_turn_end()

        await agent._handle_response(_live_response(output_text="Hi"))
        return metrics

    metrics = asyncio.run(scenario())

    assert [name for name, _ in metrics] == ["voice_latency_ms"]


def test_output_transcription_then_audio_emits_latency_once():
    async def scenario():
        agent = LiveAgent(TelloToolkit(simulate=True))
        metrics = []

        agent.on_metric(lambda name, value: metrics.append((name, value)))
        agent.mark_user_turn_end()

        await agent._handle_response(_live_response(output_text="Hi"))
        await agent._handle_response(_live_response(audio=b"first"))
        return metrics

    metrics = asyncio.run(scenario())

    assert [name for name, _ in metrics] == ["voice_latency_ms"]


def test_latency_emitted_once_per_turn_across_turns():
    async def scenario():
        agent = LiveAgent(TelloToolkit(simulate=True))
        metrics = []

        agent.on_metric(lambda name, value: metrics.append((name, value)))
        for _turn in range(2):
            agent.mark_user_turn_end()
            await agent._handle_response(_live_response(audio=b"a"))
            await agent._handle_response(_live_response(audio=b"b", output_text="Hi"))
            await agent._handle_response(_live_response(turn_complete=True))
        return metrics

    metrics = asyncio.run(scenario())

    assert [name for name, _ in metrics] == ["voice_latency_ms", "voice_latency_ms"]


def test_ptt_end_user_turn_starts_latency_timer_when_activity_end_is_sent(monkeypatch):
    monkeypatch.setitem(LIVE, "ptt_tail_ms", 0)

    async def scenario():
        agent = LiveAgent(TelloToolkit(simulate=True), push_to_talk=True)
        session = _RecordingSession(agent)
        agent._session = session
        metrics = []
        agent.on_metric(lambda name, value: metrics.append((name, value)))

        await agent.begin_user_turn()
        timer_during_turn = agent._turn_user_ts
        await agent.end_user_turn(wait=True)
        await agent._handle_response(_live_response(audio=b"reply"))
        await agent._handle_response(_live_response(audio=b"more"))
        await agent.stop()
        return session, timer_during_turn, metrics

    session, timer_during_turn, metrics = asyncio.run(scenario())

    assert timer_during_turn == 0  # holding the button is not "waiting for the model"
    assert session.sent.index("activity_start") < session.sent.index("activity_end")
    assert session.timer_at_send["activity_end"] > 0
    assert [name for name, _ in metrics] == ["voice_latency_ms"]


def test_begin_user_turn_discards_stale_latency_timer():
    async def scenario():
        agent = LiveAgent(TelloToolkit(simulate=True), push_to_talk=True)
        metrics = []
        agent.on_metric(lambda name, value: metrics.append((name, value)))

        agent.mark_user_turn_end()      # previous turn's timer, never answered
        await agent.begin_user_turn()   # new press: that timer is meaningless now
        await agent._handle_response(_live_response(audio=b"late audio from the old turn"))
        await agent.stop()
        return metrics

    metrics = asyncio.run(scenario())

    assert metrics == []
