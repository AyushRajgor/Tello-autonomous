# TelloAI Agent

Voice-controlled autonomous drone powered by LLM agents. Built on DJI Tello.

You talk to it. It flies, sees, thinks, and talks back.

## Quick Start

```bash
# 1. Clone and install
cd tello-ai-agent
pip install -r requirements.txt

# 2. Set up API key
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY

# 3. Run in simulation mode (no drone needed)
python main.py --simulate

# 4. Run with real drone
python scripts/network_check.py       # Verify dual WiFi setup
python main.py                         # Text mode (slow agent, Gemini 3 Flash)
python main.py --dashboard             # Web dashboard — Live API by default
python main.py --voice                 # Voice mode — talk to the drone
python main.py --voice-text            # Live API session, type instead of speak
python main.py --dashboard --legacy-agent   # Fall back to the slower text agent
```

## Modes

| Command | Description |
|---------|-------------|
| `python main.py --simulate` | Text agent, no drone — simulated responses |
| `python main.py --simulate --webcam` | Same, but vision sees your laptop camera |
| `python main.py` | Text-based agent with real drone (Gemini 3 Flash) |
| `python main.py --dashboard` | Web dashboard (Live API by default — fast) |
| `python main.py --dashboard --legacy-agent` | Dashboard on the slower multi-call agent |
| `python main.py --voice` | Real-time voice via Gemini 3.1 Flash Live Preview |
| `python main.py --voice-text` | Live API session without microphone (type input) |

## Network Setup

The Tello creates its own WiFi. You need a second network interface for internet (LLM APIs).

- **WiFi 1** (built-in) → Connect to `TELLO-XXXXX`
- **WiFi 2** (USB adapter) or **Ethernet** → Internet

Run `python scripts/network_check.py` to verify.

## Models Used

| Role | Model | String |
|------|-------|--------|
| Live voice + vision | Gemini 3.1 Flash Live Preview | `gemini-3.1-flash-live-preview` |
| Vision (reasoning, legacy agent) | Gemini 3 Flash | `gemini-3-flash-preview` |
| Vision (quick yes/no checks) | Gemini 3.1 Flash-Lite | `gemini-3.1-flash-lite` |
| Spatial (optional) | Gemini Robotics-ER 1.5 | `gemini-robotics-er-1.5` |

The Live API is the default path for `--voice` and `--dashboard`: one persistent
WebSocket, drone video streamed at 1 FPS into the session, Aoede voice output.
Typical first-token latency is 300–800 ms versus ~8–15 s on the legacy agent.

## Project Structure

```
tello-ai-agent/
├── main.py              # Entry point
├── config.py            # All constants, model strings, tool schemas
├── core/
│   ├── toolkit.py       # TelloToolkit — drone functions + safety
│   ├── safety.py        # Geofence, battery, param validation
│   ├── agent.py         # LLM agent loop with function calling
│   └── state.py         # Position tracking, telemetry
├── vision/
│   └── llm_vision.py    # Frame → Gemini analysis
├── voice/
│   └── gemini_live.py   # Gemini Live API voice client
├── features/
│   └── search.py        # "Find X" exploration
├── dashboard/
│   └── server.py        # FastAPI + WebSocket dashboard
├── tests/
│   └── test_toolkit.py  # Unit tests
└── scripts/
    └── network_check.py # Verify dual WiFi
```

## Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

## Safety

- **ESC key** → Immediate land (always active)
- **Geofence** → 8m max from takeoff
- **Battery** → Warn at 20%, force land at 10%
- **Conservative movement** → Max 100cm per move without camera check
- **Max altitude** → 2m indoor cap
