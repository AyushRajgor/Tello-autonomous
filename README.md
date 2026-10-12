# TelloAI Agent

Voice-controlled autonomous drone agent for the DJI Tello, powered by Gemini.

The system runs without drone hardware in simulation mode. It has a text agent,
a web dashboard, and a Gemini Live voice/video path. The safest and most useful
development path is the simulated dashboard.

## Current Status

- Toolkit, safety limits, safety watchdog, operator controls, state tracking, tracing and simulation are implemented and covered by tests (233 Python tests, 38 frontend tests).
- Dashboard mode uses the React/Vite operator console and Gemini Live (`gemini-3.8-live`) by default, with push-to-talk.
- Text mode and `--dashboard --legacy-agent` use the `gemini-3.8-flash` function-calling loop. The planner sees camera frames directly in `get_frame` results.
- `features/search.py` is a working, safety-checked object-search routine, but it is **not exposed as a tool** yet.
- Real-drone code paths are hardened (re-entrant command lock, stop epoch that cancels queued commands, settle window after a raw stop, height-sensor-confirmed land/takeoff, no double moves, sensor-synced heading/altitude), but **not yet validated on real hardware**. See [Known Gaps](#known-gaps).

## Quick Start: Windows PowerShell

Use the repo virtual environment. Plain `python` may point at a different global
environment with incompatible packages.

```powershell
cd D:\projects\Tello-autonomous

# Install or refresh dependencies
.\venv\Scripts\python.exe -m pip install -r requirements.txt

# Create local environment file, then set GEMINI_API_KEY
Copy-Item .env.example .env
notepad .env

# Build the dashboard once (required for --dashboard)
cd dashboard\frontend; npm install; npm run build; cd ..\..

# Best no-drone demo: dashboard + laptop webcam
.\venv\Scripts\python.exe main.py --simulate --webcam --dashboard
```

Open the dashboard at `http://localhost:8080`. If the build is missing, the
page returns HTTP 503 with the build instructions.

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
cp .env.example .env            # then set GEMINI_API_KEY
(cd dashboard/frontend && npm install && npm run build)
./venv/bin/python main.py --simulate --webcam --dashboard
```

## Run Modes

Use `.\venv\Scripts\python.exe` on Windows, `./venv/bin/python` on macOS/Linux,
or `python` only after activating the correct virtual environment.

| Command | Description |
|---|---|
| `python main.py --simulate` | Text agent, no drone hardware. |
| `python main.py --simulate --webcam` | Text agent with the laptop webcam as the camera. |
| `python main.py --simulate --webcam --dashboard` | Recommended local demo: dashboard + simulated drone + webcam. |
| `python main.py` | Text agent with the real drone. Slower, but easiest to trace. |
| `python main.py --dashboard` | Web dashboard with Gemini Live (push-to-talk). |
| `python main.py --dashboard --legacy-agent` | Dashboard using the text/function-calling agent. |
| `python main.py --voice` | Open-mic voice mode via Gemini Live. Use headphones. |
| `python main.py --voice-text` | Gemini Live session without the microphone; type instead. |

Without `--simulate`, `main.py` connects to the Tello and **exits with an error**
if it can't. It never silently falls back to simulation.

## Operator Controls

Operator controls go straight to the drone. The AI is never in the loop.

| Where | Stop and hover | Land | Emergency motor cut | Let the AI move again |
|---|---|---|---|---|
| Text / voice CLI (type) | `stop` or `hover` | `land` | `!estop` | `resume`, or any new instruction |
| Dashboard chat (type) | `stop` or `hover` | `land` | `!estop` | `resume`, or any new instruction |
| Dashboard buttons | STOP or **Space** | Land or **Esc** | E-STOP (needs a second confirm click) | Resume AI banner, a new chat message, or a push-to-talk press |
| Voice (spoken) | "stop", "hover", "freeze", "hold" | "land", "land now" | not available by voice | your next instruction |

STOP, Land and E-STOP set an **operator hold**: the AI's motion tools are
refused until the user gives a new instruction. `emergency_stop`, `flip` and
`hover` are operator-only. No LLM can call them.

- A STOP also cancels every AI or manual command still waiting for the drone, so nothing queued flies afterwards.
- The hold is lifted when the agent starts the new turn. A chat message sent before you pressed STOP doesn't lift it, a Live message that couldn't be delivered doesn't lift it, and the dashboard's **Observe** button never lifts it (camera only).
- Operator commands never wait behind each other: the dashboard runs STOP/E-STOP/Resume on their own thread pool and Land on another; the CLIs run each typed operator command on its own thread, so `!estop` never waits behind `land`.
- Manual moves (the dashboard flight pad: takeoff/move/rotate/flip) are single-flight. A press while the previous one is still running gets a "busy" error instead of queuing.

## Environment Variables

Copy `.env.example` to `.env`.

| Variable | Required | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | Yes | Gemini models, Live API and vision calls. |
| `OPENAI_API_KEY` | No | Reserved fallback; unused by the current runtime. |
| `DASHBOARD_HOST` | No | Defaults to `127.0.0.1` (loopback only). Set `0.0.0.0` only together with `DASHBOARD_TOKEN`. |
| `DASHBOARD_PORT` | No | Defaults to `8080`. |
| `DASHBOARD_TOKEN` | No | Shared secret. When set, `/ws` and `/api/*` require `?token=...`; open the page as `http://host:8080/?token=...`. |
| `DASHBOARD_ALLOWED_ORIGINS` | No | Extra browser origins allowed to open the WebSocket (comma-separated). |

## Network Setup For A Real Tello

The Tello creates its own WiFi network. You still need internet for Gemini API
calls, so real flights need two network paths:

- WiFi 1: connect to `TELLO-XXXXXX`
- WiFi 2, USB adapter, or Ethernet: internet access

Run this before real-drone mode:

```powershell
.\venv\Scripts\python.exe scripts\network_check.py
```

## Models

Model IDs live in `config.py` (`MODELS`). Prices live in `core/cost.py`.

| Role | ID | Notes |
|---|---|---|
| Live voice + video | `gemini-3.8-live` | GA. Replaces `gemini-3.1-flash-live-preview`, which shuts down 2026-11-17. Tools declared `BLOCKING`. |
| Text agent / reasoning vision | `gemini-3.8-flash` | Thinking `medium` (3.8 Flash accepts low/medium/high; `minimal` is rejected). No temperature is sent. |
| Quick vision checks | `gemini-3.1-flash-lite` | Thinking `minimal`. Available until 2027-05-07. |
| Spatial pointing (configured, unused) | `gemini-robotics-er-2-preview` | Replaces `gemini-robotics-er-1.6-preview` (shut down 2026-08-31). No code calls it yet. |

The Live path is the default for `--voice` and `--dashboard`. It keeps one
logical session open (context-window compression plus session resumption across
the ~10-minute connection limit) and sees drone video at up to 1 FPS. The text
agent path is slower but gives richer per-turn traces. Its `generateContent`
calls (and the vision helpers) retry transient 408/429/5xx errors up to 4 times
with backoff (`GEMINI_HTTP_RETRY` in `config.py`, `core/genai_client.py`).

## Dependencies

- Use the project venv, not the global Python installation.
- `google-genai>=2.23.0,<3.0.0`: 2.23 handles the Live `interaction_status` used by `gemini-3.8-live`. 3.x removes Live methods this project uses.
- `fastapi>=0.143,<0.144`. Starlette is not pinned; FastAPI owns that dependency.
- `httpx2` is required for FastAPI/Starlette's `TestClient`.
- `opencv-python>=4.10,<5`: 5.x has not been validated here.
- The dashboard frontend is a local npm package in `dashboard/frontend` (React 19, Vite 8, Vitest).

Verified local versions: Python 3.12.9, `google-genai==2.29.0`, `fastapi==0.143.0`,
`starlette==1.3.1`, `djitellopy==2.5.0`, `pytest==9.1.1`, Node 22.

## Dashboard Frontend

The dashboard source lives in `dashboard/frontend` and builds to
`dashboard/static_dist` (gitignored). FastAPI checks for `static_dist/index.html`
on every request to `/` and returns HTTP 503 with build instructions if it is
missing. There is no other fallback UI.

```powershell
cd dashboard\frontend
npm install
npm test
npm run build
```

For frontend development, run the FastAPI dashboard on port 8080 and `npm run dev`
(Vite on `127.0.0.1:5173`). Vite proxies `/ws`, `/api`, `/video-status` and
`/webcam-status` to FastAPI; the dev origins are already in the WebSocket allowlist.

## Project Structure

```text
Tello-autonomous/
|-- main.py                 # CLI entry point, mode selection, auto-land on exit
|-- config.py               # Model IDs, safety thresholds, tool schemas, env vars
|-- core/
|   |-- toolkit.py          # TelloToolkit: execute() for LLMs, operator_command() for humans
|   |-- safety.py           # Per-command checks: flying, battery, limits, geofence
|   |-- watchdog.py         # 1 s background thread: battery, keepalive, link loss
|   |-- state.py            # DroneState, Position, dead reckoning, action log
|   |-- agent.py            # Text agent: gemini-3.8-flash function-calling loop
|   |-- genai_client.py     # Gemini HTTP client with retry/backoff
|   |-- tracing.py          # Trace and span recording
|   `-- cost.py             # Per-model token pricing
|-- voice/
|   |-- gemini_live.py      # LiveAgent: Gemini Live session, tools, reconnects
|   `-- audio.py            # Microphone/speaker helpers (sounddevice)
|-- vision/
|   `-- llm_vision.py       # One-shot frame analysis with structured JSON output
|-- dashboard/
|   |-- server.py           # FastAPI app, WebSocket, operator controls, access control
|   |-- frontend/           # React/Vite dashboard source
|   `-- static_dist/        # Generated build (gitignored)
|-- features/
|   `-- search.py           # Multi-room object search; not exposed as a tool yet
|-- scripts/
|   `-- network_check.py    # Dual-network sanity check
|-- tests/                  # pytest suite (simulation only, no drone needed)
|-- docs/                   # Architecture, workflow, safety, component docs
`-- legacy/                 # Archived pre-AI Tello code (unused)
```

## Tests

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -q
cd dashboard\frontend; npm test
```

Python: 233 tests in 10 files. `test_safety_core.py` (63), `test_live_agent.py` (46),
`test_toolkit.py` (27), `test_server.py` (27), `test_agent.py` (19),
`test_live_latency.py` (14), `test_vision.py` (13), `test_search.py` (11),
`test_model_config.py` (9), `test_tracing.py` (4). No test calls the real
Gemini API or a real drone.

Frontend: 38 Vitest tests in 6 files (`App`, `audio`, `dashboard-state`,
`format`, `hotkeys`, `video-stream`).

Useful checks:

```powershell
.\venv\Scripts\python.exe -m pip check
.\venv\Scripts\python.exe main.py --help
```

## Safety

Limits are enforced in code (`core/safety.py`, `core/toolkit.py`,
`core/watchdog.py`), not only in the prompt:

- Motion commands are refused unless the drone is flying.
- Moves are clamped to 20–100 cm per command. Longer requests move 100 cm and say so.
- Altitude stays between a 20 cm floor and a 200 cm ceiling. `up`/`down` are clamped, then refused at the limit.
- Geofence: a move is refused if it would end more than 800 cm (horizontal) from the latest takeoff point.
- Rotation is clamped to ±360°. Speed is clamped to 10–100 cm/s and set to 30 cm/s on connect.
- Takeoff is refused at ≤20% battery. The watchdog warns at ≤20% and force-lands at ≤10%.
- Flip is operator-only and needs >50% battery.
- The watchdog sends `rc 0 0 0 0` after 8 s of idle flight (the Tello auto-lands after 15 s without commands) and flags LINK LOST after 5 s without telemetry.
- Operator STOP/Land/E-STOP set an operator hold that blocks AI motion until new user input, and cancel any command still waiting for the drone.
- Every motion's safety check, send and state update run under one re-entrant command lock, so concurrent moves can't breach the geofence or altitude limits.
- After a raw stop/land/E-STOP, the next command waits until the interrupted motion has settled, then discards stray replies (djitellopy doesn't match replies to commands).
- On a real drone, land counts only when the height sensor confirms it. Operator/watchdog land retries while the drone stays airborne; a land sent without the lock is reported as unconfirmed.
- The watchdog force-land runs on its own thread and retries every 5 s. If the height sensor disagrees with the flying/landed state for 3 consecutive ticks while no command runs, the watchdog resyncs the state and alerts.
- On exit, `main.py` lands the drone if it is still flying.

E-STOP cuts the motors and the drone falls. Use it only when a crash is imminent.
See [docs/safety.md](docs/safety.md).

## Known Gaps

- `features/search.py` is not exposed as an agent tool.
- No field validation on a real Tello yet. Everything is tested in simulation with a mocked `djitellopy`.
- STOP sends the SDK `stop` command (only while a motion is in flight), which older or plain-Tello firmware may refuse. **E-STOP is the only guaranteed instant interrupt.** Land waits up to 25 s for an in-flight move to finish; after that it sends a raw `land` and reports it as unconfirmed.
- On firmware without `stop`, the settle wait after a STOP and the land wait can take a few seconds, because the interrupted move runs to completion first. An interrupted move's position estimate may be off by up to the move length.
- Verify on the first real flight: the IMU yaw sign self-calibrates on the first rotation of 20° or more, so dead-reckoned x/y may be mirrored before that.
- Also verify the Live API `turn_coverage` behaviour with real video (how frames sent outside speech are counted).
- `gemini-robotics-er-2-preview` is configured and priced but unused.
- Text-agent turns are slow: "take off, look, land" takes about 30 s with `gemini-3.8-flash`.
- Open-mic voice mode mutes the mic while TelloAI speaks (echo gate), so you can't talk over it. Use headphones, or the dashboard's push-to-talk for real barge-in.
- Live sessions record one trace per session, completed only when the session stops, so Live tool calls don't appear in the Traces tab while the session runs.
- Dead reckoning drifts. The geofence is a soft boundary, not a guarantee.

## More Documentation

- [Architecture](docs/architecture.md)
- [Workflow](docs/workflow.md)
- [Components](docs/components.md)
- [Safety](docs/safety.md)
- [User flows](docs/user-flows.md)
