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

from config import DASHBOARD, GEMINI_API_KEY, MODELS
from core.toolkit import TelloToolkit, parse_operator_text
from core.agent import TelloAgent
from core.watchdog import SafetyWatchdog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("telloai")


def create_toolkit(simulate: bool = False, use_webcam: bool = False) -> TelloToolkit:
    """
    Create TelloToolkit, optionally with real drone connection.

    Never silently falls back to simulation: if you asked for the real drone
    and it isn't reachable, that's an error you need to see.
    """
    if simulate:
        logger.info("Running in SIMULATION mode — no real drone")
        toolkit = TelloToolkit(tello=None, simulate=True, use_webcam=use_webcam)
        toolkit.connect()
        return toolkit

    try:
        from djitellopy import Tello
    except ImportError:
        logger.error("djitellopy is not installed. Install it with: pip install djitellopy")
        logger.error("Or run without a drone: python main.py --simulate")
        sys.exit(1)

    # retry_count=1: djitellopy re-sends a command whose reply times out, and
    # the Tello only replies once a move finishes — retries can fly a move twice.
    toolkit = TelloToolkit(tello=Tello(retry_count=1), simulate=False)
    if not toolkit.connect():
        logger.error(
            "Could not connect to the Tello. Check that this computer is on the "
            "TELLO-XXXXXX WiFi (run scripts/network_check.py), or run with --simulate."
        )
        sys.exit(1)
    return toolkit


def _print_safety_event(event: dict):
    """Console listener for watchdog alerts in text/voice modes."""
    print(f"\n[SAFETY {event['level'].upper()}] {event['message']}")


async def run_text_mode(toolkit: TelloToolkit, tracer):
    """Phase 1: Interactive text-based agent."""
    agent = TelloAgent(toolkit, tracer)
    await agent.run_interactive()


async def run_voice_mode(toolkit: TelloToolkit, tracer, watchdog: SafetyWatchdog, text_only: bool = False):
    """
    Voice mode with the Gemini Live API (open mic, automatic turn detection).

    - Real mic capture (16 kHz PCM) -> Live API
    - Aoede voice output (24 kHz PCM) -> local speakers
    - Drone video streamed at up to 1 FPS into the session
    - Watchdog warnings are spoken to the user through the model
    - If --voice-text is set, skips mic and accepts typed input (useful on
      machines without a microphone or for silent debugging).
    """
    from voice.gemini_live import LiveAgent
    from voice.audio import MicStream, SpeakerPlayer

    agent = LiveAgent(toolkit, tracer=tracer, push_to_talk=False)
    loop = asyncio.get_running_loop()
    notice_tasks: set[asyncio.Task] = set()  # strong refs: unreferenced tasks can be GC'd

    def _send_notice(message: str):
        task = asyncio.ensure_future(agent.send_system_notice(message))
        notice_tasks.add(task)
        task.add_done_callback(notice_tasks.discard)

    def on_safety_event(event: dict):
        # Called on the watchdog thread — hop onto the event loop.
        if event["level"] in ("warning", "critical"):
            loop.call_soon_threadsafe(_send_notice, event["message"])

    watchdog.add_listener(on_safety_event)

    # Speaker playback (runs off a background thread)
    speaker = SpeakerPlayer()

    async def on_audio(pcm_chunk: bytes):
        speaker.play(pcm_chunk)

    def on_interrupt():
        speaker.flush()

    async def on_text(role: str, text: str):
        label = {"user": "You", "assistant": "TelloAI"}.get(role, "[system]")
        print(f"\n{label}: {text}")

    async def on_tool(name, args, result):
        ok = "✓" if result.get("success", True) else "✗"
        print(f"  {ok} {name}({args})")

    agent.on_audio(on_audio)
    agent.on_text(on_text)
    agent.on_tool(on_tool)
    agent.on_interrupt(on_interrupt)

    print("\n" + "=" * 60)
    print(f"  TelloAI Agent — Voice Mode ({MODELS['live']})")
    if not text_only:
        print("  Tip: use headphones — laptop speakers echo into the mic.")
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

    print("  Typed 'stop' / 'land' / 'resume' act instantly (no AI); '!estop' cuts motors.")

    try:
        while True:
            user_input = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("").strip()
            )
            if user_input.lower() in ("quit", "exit"):
                break
            action = parse_operator_text(user_input)
            if action:
                # Own thread, keep reading: '!estop' must not wait behind 'land'.
                toolkit.operator_command_background(
                    action,
                    on_done=lambda r: print(f"  [operator] {r.get('message') or r.get('error')}", flush=True),
                )
                continue
            if user_input:
                await agent.send_text(user_input)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        if mic_task:
            mic_task.cancel()
        if mic:
            mic.stop()
        watchdog.remove_listener(on_safety_event)
        if toolkit.state.is_flying:
            await asyncio.to_thread(toolkit.operator_command, "land")
        await agent.stop()
        speaker.stop()


def run_dashboard_mode(toolkit: TelloToolkit, tracer, watchdog: SafetyWatchdog, use_live: bool = True):
    """
    Web dashboard mode.

    By default routes chat through the Live API session for low-latency responses
    and continuous video streaming. Use --legacy-agent to fall back to the
    slower function-calling text agent.
    """
    import dashboard.server as dash

    dash.toolkit = toolkit
    dash.tracer = tracer
    dash.watchdog = watchdog
    dash.use_live = use_live

    if use_live:
        from voice.gemini_live import LiveAgent
        # The browser sends audio only while the talk button is held.
        dash.live_agent = LiveAgent(toolkit, tracer=tracer, push_to_talk=True)
        dash.agent = None
        logger.info("Dashboard will use Gemini Live API (fast path)")
    else:
        dash.agent = TelloAgent(toolkit, tracer)
        dash.live_agent = None
        logger.info(f"Dashboard will use legacy text agent ({MODELS['vision_reasoning']})")

    host = DASHBOARD["host"]
    port = DASHBOARD["port"]

    if host not in ("127.0.0.1", "localhost", "::1") and not DASHBOARD["token"]:
        logger.warning(
            f"Dashboard is bound to {host} WITHOUT a DASHBOARD_TOKEN — anyone on "
            "this network can fly the drone. Set DASHBOARD_TOKEN or use 127.0.0.1."
        )

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

    # Continuous safety: battery force-land, keepalive, link-loss alerts.
    watchdog = SafetyWatchdog(toolkit)
    if not args.dashboard:
        watchdog.add_listener(_print_safety_event)
    watchdog.start()

    try:
        if args.dashboard:
            run_dashboard_mode(toolkit, tracer, watchdog, use_live=not args.legacy_agent)
        elif args.voice or args.voice_text:
            asyncio.run(run_voice_mode(toolkit, tracer, watchdog, text_only=args.voice_text))
        else:
            asyncio.run(run_text_mode(toolkit, tracer))
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        # Safety: always land if flying (priority path: aborts any in-flight move)
        if toolkit.state.is_flying:
            logger.info("Auto-landing before exit...")
            toolkit.operator_command("land")
        watchdog.stop()
        if not toolkit.simulate:
            toolkit.stream_off()
        toolkit.release_camera()


if __name__ == "__main__":
    main()
