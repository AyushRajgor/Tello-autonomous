# TelloAI Agent

Voice-controlled autonomous drone agent for the DJI Tello, powered by Gemini.

The current system can run without drone hardware in simulation mode. It has a
text agent, a web dashboard, and a Gemini Live voice/video path. The safest and
most useful development path is the simulated dashboard.

## Current Status

- Core drone toolkit, safety checks, state tracking, tracing, and simulation tests are working.
- Dashboard mode uses the React/Vite operator console and Gemini Live by default for low-latency voice/video behavior.
- Text mode and `--dashboard --legacy-agent` use the slower Gemini 3.5 Flash function-calling loop.
- `features/search.py` contains object-search scaffolding, but it is not wired into the agent yet.
- Real drone use still needs field testing, dual-network setup, and careful safety checks.

## Quick Start: Windows PowerShell

Use the repo virtual environment. Plain `python` may point at a different global
environment with incompatible packages.

```powershell
cd D:\projects\Tello-autonomous

# Install or refresh dependencies
.\venv\Scripts\python.exe -m pip install -r requirements.txt

# Create local environment file
Copy-Item .env.example .env
# Edit .env and set GEMINI_API_KEY
notepad .env

# Best no-drone demo: dashboard + laptop webcam
.\venv\Scripts\python.exe main.py --simulate --webcam --dashboard
```

Open the dashboard at:

```text
http://localhost:8080
```

If you need to create the virtual environment from scratch:

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Quick Start: macOS/Linux

```bash
cd Tello-autonomous
python3 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
# Edit .env and set GEMINI_API_KEY
./venv/bin/python main.py --simulate --webcam --dashboard
```

## Run Modes

Use `.\venv\Scripts\python.exe` on Windows, `./venv/bin/python` on macOS/Linux,
or `python` only after activating the correct virtual environment.

| Command | Description |
|---|---|
| `python main.py --simulate` | Text agent, no drone hardware. |
| `python main.py --simulate --webcam` | Text agent with laptop webcam as the camera source. |
| `python main.py --simulate --webcam --dashboard` | Recommended local demo: dashboard + simulated drone + webcam. |
| `python main.py` | Text agent with real drone. Slower, but easiest to trace. |
| `python main.py --dashboard` | Web dashboard with Gemini Live by default. |
| `python main.py --dashboard --legacy-agent` | Dashboard using the slower text/function-calling agent. |
| `python main.py --voice` | Real-time voice mode via Gemini Live. |
| `python main.py --voice-text` | Gemini Live session without microphone input; type commands instead. |

## Environment Variables

Copy `.env.example` to `.env`.

| Variable | Required | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | Yes | Gemini models, Live API, and vision calls. |
| `OPENAI_API_KEY` | No | Reserved fallback; currently unused in the main runtime. |
| `DASHBOARD_HOST` | No | Defaults to `0.0.0.0`. |
| `DASHBOARD_PORT` | No | Defaults to `8080`. |

## Network Setup For A Real Tello

The Tello creates its own WiFi network. You still need internet for Gemini API
calls, so real flights need two network paths:

- WiFi 1: connect to `TELLO-XXXXX`
- WiFi 2, USB adapter, or Ethernet: internet access

Run this before real-drone mode:

```powershell
.\venv\Scripts\python.exe scripts\network_check.py
```

## Models

Model IDs live in `config.py`.

| Role | Model | ID |
|---|---|---|
| Live voice + video | Gemini 3.1 Flash Live Preview | `gemini-3.1-flash-live-preview` |
| Reasoning / legacy agent | Gemini 3.5 Flash | `gemini-3.5-flash` |
| Quick vision checks | Gemini 3.1 Flash-Lite | `gemini-3.1-flash-lite` |
| Spatial / robotics, optional | Gemini Robotics-ER 1.6 Preview | `gemini-robotics-er-1.6-preview` |

The Live path is the default for `--voice` and `--dashboard`: it keeps one
persistent WebSocket open, streams drone video at 1 FPS, and uses Aoede voice
output. The legacy path is slower, but it gives richer per-turn traces.

## Dependencies

Important dependency policy:

- Use the project venv, not the global Python installation.
- `google-genai>=2.3.0` is required for the current Gemini API surface.
- FastAPI is pinned to the current compatible line: `fastapi>=0.138,<0.139`.
- Starlette is not pinned directly; FastAPI owns the compatible Starlette dependency.
- The dashboard frontend is a local npm package in `dashboard/frontend`.

Current verified local versions include:

- `fastapi==0.138.1`
- `starlette==1.3.1`
- `google-genai==2.10.0`
- `pytest==9.1.1`

## Dashboard Frontend

The dashboard source lives in `dashboard/frontend` and builds to
`dashboard/static_dist`. FastAPI serves `static_dist` when it exists and falls
back to the legacy `dashboard/static` files when it does not.

```powershell
cd dashboard\frontend
npm install
npm test
npm run build
```

For frontend development, run the FastAPI dashboard on port 8080 and Vite on
port 5173. The Vite dev server proxies `/ws`, `/api`, `/video-status`, and
`/webcam-status` to FastAPI.

## Project Structure

```text
Tello-autonomous/
|-- main.py                 # CLI entry point and mode selection
|-- config.py               # Model IDs, safety thresholds, tool schemas, env vars
|-- core/
|   |-- toolkit.py          # TelloToolkit: drone commands + safety integration
|   |-- safety.py           # Geofence, altitude, battery, parameter validation
|   |-- state.py            # DroneState, Position, dead-reckoning, action log
|   |-- agent.py            # Legacy Gemini function-calling agent loop
|   |-- tracing.py          # Trace and span recording
|   `-- cost.py             # Token cost estimates
|-- voice/
|   |-- gemini_live.py      # Gemini Live API session and tool-call loop
|   `-- audio.py            # Microphone/speaker helpers
|-- vision/
|   `-- llm_vision.py       # Standalone Gemini vision calls for legacy path
|-- dashboard/
|   |-- server.py           # FastAPI app, WebSocket, dashboard state
|   |-- frontend/           # React/Vite dashboard source
|   |-- static/             # Legacy browser UI fallback
|   `-- static_dist/        # Generated React dashboard build
|-- features/
|   `-- search.py           # Multi-room object-search scaffold; not wired yet
|-- scripts/
|   `-- network_check.py    # Dual-network sanity check
|-- tests/
|   |-- test_model_config.py
|   |-- test_toolkit.py
|   `-- test_tracing.py
|-- docs/                   # Architecture, workflow, safety, component docs
`-- legacy/                 # Archived pre-AI Tello code
```

## Tests

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -v
cd dashboard\frontend
npm test
```

Current Python suite: 35 tests. The frontend suite covers dashboard formatting,
audio conversion, socket-message reduction, and shell rendering.

Useful checks:

```powershell
.\venv\Scripts\python.exe -m pip check
.\venv\Scripts\python.exe main.py --help
```

## Safety

The current runtime has these safety layers:

- Geofence: 800 cm from takeoff origin.
- Max altitude: 200 cm.
- Battery warning at 20 percent; critical force-land flag at 10 percent.
- Movement clamp: 20 cm minimum, 500 cm maximum.
- Rotation clamp: -360 to 360 degrees.
- Process shutdown tries to land before exit if the drone is still marked flying.
- `emergency_stop` exists, but it cuts motors and the drone can fall. Use only for imminent crash situations.

Important limitation: the current non-legacy runtime does not implement a global
always-on ESC keyboard land switch. Use dashboard/agent commands carefully and
keep physical access to the drone during tests.

## Known Gaps

- `features/search.py` is not exposed as an agent tool yet.
- `SAFETY["connection_timeout_s"]` exists, but no watchdog polls it yet.
- Gemini Live sessions do not auto-reconnect after WebSocket failure.
- Real-drone behavior needs a field checklist and manual validation.
- Dashboard automated coverage is still light.

## More Documentation

- [Architecture](docs/architecture.md)
- [Workflow](docs/workflow.md)
- [Components](docs/components.md)
- [Safety](docs/safety.md)
- [User flows](docs/user-flows.md)
