import asyncio
from types import SimpleNamespace

from core.toolkit import TelloToolkit
from voice.gemini_live import LiveAgent


def _live_response(
    audio: bytes | None = None,
    input_text: str | None = None,
    input_finished: bool | None = None,
    output_text: str | None = None,
    voice_activity_type: str | None = None,
    vad_signal_type: str | None = None,
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
            turn_complete=False,
        ),
        voice_activity=SimpleNamespace(voice_activity_type=voice_activity_type) if voice_activity_type else None,
        voice_activity_detection_signal=SimpleNamespace(vad_signal_type=vad_signal_type) if vad_signal_type else None,
    )


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
        observed_start_times = []

        class Session:
            async def send_realtime_input(self, text):
                observed_start_times.append(agent._turn_user_ts)

        agent._session = Session()

        await agent.send_text("hello")
        return observed_start_times

    observed_start_times = asyncio.run(scenario())

    assert observed_start_times[0] > 0


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


def test_vad_eos_starts_latency_for_open_mic_turn():
    async def scenario():
        agent = LiveAgent(TelloToolkit(simulate=True))
        metrics = []

        agent.on_metric(lambda name, value: metrics.append((name, value)))

        await agent._handle_response(_live_response(vad_signal_type="VAD_SIGNAL_TYPE_EOS"))
        await agent._handle_response(_live_response(output_text="Hi"))
        return metrics

    metrics = asyncio.run(scenario())

    assert [name for name, _ in metrics] == ["voice_latency_ms"]


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
