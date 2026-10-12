"""
TelloAI Agent — Entry Point

Modes:
  python main.py              → Interactive text mode (Phase 1)
  python main.py --voice      → Voice mode with Gemini Live API (Phase 3)
  python main.py --dashboard  → Web dashboard + text agent
  python main.py --simulate   → Simulate without real drone (testing)
"""

import argparse
import asyncio
import logging
import sys
import threading
from typing import Optional

import uvicorn

from config import DASHBOARD, GEMINI_API_KEY
from core.toolkit import TelloToolkit
from core.agent import TelloAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("telloai")


def create_toolkit(simulate: bool = False, use_webcam: bool = False) -> TelloToolkit:
    """Create TelloToolkit, optionally with real drone connection."""
    if simulate:
        logger.info("Running in SIMULATION mode — no real drone")
        toolkit = TelloToolkit(tello=None, simulate=True, use_webcam=use_webcam)
        toolkit.connect()
        return toolkit

    try:
        from djitellopy import Tello
        tello = Tello()
        toolkit = TelloToolkit(tello=tello, simulate=False)
        toolkit.connect()
        return toolkit
    except ImportError:
        logger.warning("djitellopy not installed. Falling back to simulation mode.")
        logger.warning("Install with: pip install djitellopy")
        toolkit = TelloToolkit(tello=None, simulate=True)
        toolkit.connect()
        return toolkit
    except Exception as e:
        logger.error(f"Failed to connect to drone: {e}")
        logger.info("Falling back to simulation mode.")
        toolkit = TelloToolkit(tello=None, simulate=True)
        toolkit.connect()
        return toolkit


async def run_text_mode(toolkit: TelloToolkit, tracer):
    """Phase 1: Interactive text-based agent."""
    agent = TelloAgent(toolkit, tracer)
    await agent.run_interactive()


async def run_voice_mode(toolkit: TelloToolkit, tracer, text_only: bool = False):
    """
    Phase 3: Voice mode with Gemini 3.1 Flash Live Preview.

    - Real mic capture (16 kHz PCM) -> Live API
    - Aoede voice output (24 kHz PCM) -> local speakers
    - Drone video streamed at 1 FPS into the session
    - If --voice-text is set, skips mic and accepts typed input (useful on
      machines without a microphone or for silent debugging).
    """
    from voice.gemini_live import LiveAgent
    from voice.audio import MicStream, SpeakerPlayer

    agent = LiveAgent(toolkit, tracer=tracer)

    # Speaker playback (runs off a background thread)
    speaker = SpeakerPlayer()

    async def on_audio(pcm_chunk: bytes):
        speaker.play(pcm_chunk)

    def on_interrupt():
        speaker.flush()

    async def on_text(role: str, text: str):
        label = "You" if role == "user" else "TelloAI"
        print(f"\n{label}: {text}")

    async def on_tool(name, args, result):
        ok = "✓" if result.get("success", True) else "✗"
        print(f"  {ok} {name}({args})")

    agent.on_audio(on_audio)
    agent.on_text(on_text)
    agent.on_tool(on_tool)
    agent.on_interrupt(on_interrupt)

    print("\n" + "=" * 60)
    print("  TelloAI Agent — Voice Mode (gemini-3.1-flash-live-preview)")
    if text_only:
        print("  Text-only mode. Type messages; Ctrl+C to exit.")
    else:
        print("  Speak to the drone. Ctrl+C to exit.")
        print("  You can also type a message — just press Enter.")
    print("=" * 60 + "\n")

    speaker.start()
    await agent.start()

    # Start microphone if not text-only
    mic: Optional["MicStream"] = None
    mic_task: Optional[asyncio.Task] = None
    if not text_only:
        try:
            mic = MicStream()
            mic.start()

            async def mic_pump():
                while True:
                    chunk = await mic.queue.get()
                    await agent.send_audio(chunk)

            mic_task = asyncio.create_task(mic_pump(), name="mic_pump")
        except Exception as e:
            logger.error(f"Couldn't start microphone: {e}")
            logger.info("Falling back to text-only input.")
            mic = None

    try:
        while True:
            user_input = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("").strip()
            )
            if user_input.lower() in ("quit", "exit"):
                break
            if user_input:
                await agent.send_text(user_input)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        if mic_task:
            mic_task.cancel()
        if mic:
            mic.stop()
        if toolkit.state.is_flying:
            toolkit.land()
        await agent.stop()
        speaker.stop()


def run_dashboard_mode(toolkit: TelloToolkit, tracer, use_live: bool = True):
    """
    Web dashboard mode.

    By default routes chat through the Live API session for low-latency responses
    and continuous video streaming. Use --legacy-agent to fall back to the older
    Gemini 3.5 Flash text agent.
    """
    import dashboard.server as dash

    dash.toolkit = toolkit
    dash.tracer = tracer
    dash.use_live = use_live

    if use_live:
        from voice.gemini_live import LiveAgent
        dash.live_agent = LiveAgent(toolkit, tracer=tracer)
        dash.agent = None
        logger.info("Dashboard will use Gemini Live API (fast path)")
    else:
        dash.agent = TelloAgent(toolkit, tracer)
        dash.live_agent = None
        logger.info("Dashboard will use legacy text agent (Gemini 3.5 Flash)")

    host = DASHBOARD["host"]
    port = DASHBOARD["port"]

    logger.info(f"Starting dashboard at http://localhost:{port}")
    uvicorn.run(dash.app, host=host, port=port, log_level="warning")


def main():
    parser = argparse.ArgumentParser(description="TelloAI Agent")
    parser.add_argument("--voice", action="store_true", help="Voice mode (Gemini Live API)")
    parser.add_argument("--voice-text", action="store_true", help="Live API session but no microphone (type input)")
    parser.add_argument("--dashboard", action="store_true", help="Web dashboard mode")
    parser.add_argument("--legacy-agent", action="store_true", help="Use the slower text agent for the dashboard instead of Live API")
    parser.add_argument("--simulate", action="store_true", help="Simulate without real drone")
    parser.add_argument("--webcam", action="store_true", help="Use laptop camera in simulation mode")
    args = parser.parse_args()

    # Check API key
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY not set. Copy .env.example to .env and add your key.")
        sys.exit(1)

    # Create toolkit
    toolkit = create_toolkit(simulate=args.simulate, use_webcam=args.webcam)

    # Initialize tracer
    from core.tracing import TraceManager
    tracer = TraceManager()
    
    # Inject tracer into vision module
    import vision.llm_vision as vision
    vision.tracer = tracer

    # Start video stream if connected to real drone
    if not toolkit.simulate:
        toolkit.stream_on()

    try:
        if args.dashboard:
            run_dashboard_mode(toolkit, tracer, use_live=not args.legacy_agent)
        elif args.voice or args.voice_text:
            asyncio.run(run_voice_mode(toolkit, tracer, text_only=args.voice_text))
        else:
            asyncio.run(run_text_mode(toolkit, tracer))
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        # Safety: always land if flying
        if toolkit.state.is_flying:
            logger.info("Auto-landing before exit...")
            toolkit.land()
        if not toolkit.simulate:
            toolkit.stream_off()
        # Release webcam if used
        if toolkit._webcam is not None:
            toolkit._webcam.release()
            logger.info("Webcam released")


if __name__ == "__main__":
    main()
