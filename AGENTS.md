# AGENTS.md

This file provides guidance to AI coding agents (Codex and similar tools) when working with code in this repository.

## Project Overview

TelloAI Agent is a voice-controlled autonomous drone system for the DJI Tello, powered by Google Gemini. It has three modes: text REPL, web dashboard (React + FastAPI, Gemini Live push-to-talk by default), and open-mic voice via the Gemini Live API. Everything runs in simulation without hardware. Real-drone paths exist but have not been field-tested.

## Commands

```powershell
# Run (use the repo venv)
.\venv\Scripts\python.exe main.py                         # Text mode (real drone)
.\venv\Scripts\python.exe main.py --simulate              # Text mode, no drone
.\venv\Scripts\python.exe main.py --simulate --webcam --dashboard  # Dashboard at http://localhost:8080
.\venv\Scripts\python.exe main.py --dashboard --legacy-agent       # Dashboard with the text agent instead of Live
.\venv\Scripts\python.exe main.py --voice                 # Open-mic Live voice (use headphones)
.\venv\Scripts\python.exe main.py --voice-text            # Live session, typed input

# Tests (233 Python tests, 38 frontend tests)
.\venv\Scripts\python.exe -m pytest tests/ -q
.\venv\Scripts\python.exe -m pytest tests/test_safety_core.py -v
.\venv\Scripts\python.exe -m pytest tests/test_toolkit.py::TestSafety -v
.\venv\Scripts\python.exe -m pytest tests/test_toolkit.py::TestSafety::test_geofence_blocks
cd dashboard\frontend; npm test

# Dashboard frontend: build is required before --dashboard (else / returns 503)
cd dashboard\frontend; npm install; npm run build
cd dashboard\frontend; npm run dev     # Vite on 127.0.0.1:5173, proxies to FastAPI :8080

# Install / network check
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe scripts\network_check.py
```

No linter or formatter is configured. `dashboard/static_dist/` is gitignored; build it after cloning.

## Architecture

### Request Flow

```
Text / dashboard chat / voice
  -> TelloAgent.process_message()  (core/agent.py, gemini-3.8-flash)   or   LiveAgent (voice/gemini_live.py, gemini-3.8-live)
    -> model emits function calls
      -> asyncio.to_thread(toolkit.execute, name, args)   # core/toolkit.py, LLM entry point; captures stop_epoch
        -> refuse if: not in TOOL_SCHEMAS | critical battery (force-land) | operator hold + motion tool
        -> under _cmd_lock (RLock), atomic for motion:
             cancel if stop_epoch changed (OperatorStopped)
             SafetySystem.check_*()                          # core/safety.py: clamp or refuse
             settle wait after a raw stop/land/E-STOP, drain stale replies
             djitellopy send, motion-sized timeouts
             DroneState update, then heading/altitude re-synced from IMU yaw/height
    -> text agent: get_frame JPEG goes back as a multimodal function response
       Live: frames go to the video input; tool responses carry a drone-state summary
  -> reply (text agent loops up to 15 model calls per message)

Human operator (dashboard buttons/hotkeys, typed stop/land/resume/!estop, spoken stop words, watchdog)
  -> toolkit.operator_command(action, args)   # never exposed to LLMs
     (CLIs, stop words, watchdog: operator_command_background, one thread per command)

core/watchdog.py thread (every 1 s): battery warn / force-land, keepalive, link loss,
  height-sensor state reconciliation, sim battery drain
```

### Key Design Decisions

- **Two toolkit entry points.** `execute()` serves LLMs and only accepts `TOOL_SCHEMAS` names (12 tools). `operator_command()` serves humans: hover, land, emergency_stop, resume, takeoff, move, rotate, flip.
- **Operator-only actions** (`config.OPERATOR_ONLY_ACTIONS` = emergency_stop, flip, hover) are absent from `TOOL_SCHEMAS`; `core/toolkit.py` asserts this at import.
- **Operator hold.** hover/land/emergency_stop set `state.operator_hold`. `execute()` then refuses takeoff/move/rotate/set_speed until new user input clears it: a chat command, push-to-talk press, typed text, a new open-mic utterance, or `resume`. The agent clears it when the new turn starts, not the server: the text agent only if no STOP happened after the message was sent (`process_message` captures `stop_epoch` on arrival); Live `send_text(release_hold=...)` only if the message can be delivered. The dashboard's Observe button sends `keep_hold: true` (camera-only, never lifts a STOP).
- **Stop epoch.** Every operator hover/land/E-STOP, and `toolkit.hover()` (e.g. a Live barge-in abort), bumps `toolkit.stop_epoch`. AI and manual commands capture it when requested and are cancelled (`OperatorStopped` -> error result) if a stop happened while they waited, including during the settle wait.
- **Safety in code, not prompts.** `SafetySystem` returns `SafetyCheckResult(allowed, reason, modified_params)`. Distance and rotation are clamped; geofence violations and motion while landed are refused.
- **Real-drone hardening.** `_cmd_lock` is a `threading.RLock`; epoch check + safety check + send + state update for every motion run under it, so concurrent moves can't breach the geofence or altitude limits. Operator stop/land/E-STOP send raw `stop`/`land`/`emergency` without waiting for the lock and set a settle window: the next command waits past the interrupted motion (cancellable, sends `rc 0 0 0 0` keepalives, ends early once velocity/yaw show the drone stationary), then drains the reply queue, because djitellopy hands out replies in arrival order without matching them. `hover` sends `stop` only when a motion is in flight (an idle Tello already hovers); interrupted moves/rotations are reported as interrupted (position may be off by up to the move length). `Tello(retry_count=1)` plus motion-sized timeouts (no double moves). Manual (dashboard pad/CLI) motion is single-flight: a second request gets a "busy" error instead of queuing. No silent fallback to simulation: `main.py` exits if the drone isn't reachable.
- **Land and takeoff outcomes come from the height sensor.** A land "ok" isn't trusted until the height sensor confirms it; one retry on a lost reply. Operator/watchdog land (`_priority_land`) is single-flight, holds the lock through the land and retries while the drone stays airborne (a busy Tello rejects `land` mid-move); if the lock doesn't free within 25 s, a raw `land` is sent and reported as UNCONFIRMED (the drone stays marked flying). E-STOP during takeoff means not flying; an error/interrupted takeoff reply is decided by a settled height reading.
- **Position.** x/y are dead-reckoned from heading (2D geofence from the latest takeoff point, which resets on each takeoff). On a real drone, heading comes from IMU yaw (sign self-calibrates on the first rotation) and altitude from the height sensor.
- **Watchdog.** Battery-critical alert once per episode; force-land runs on its own thread and retries every 5 s. State reconciliation: if the height sensor disagrees with `is_flying` for 3 consecutive ticks while no command is running, it resyncs (airborne: keepalive and battery protection resume; landed: marked landed) and alerts.
- **Operator commands never queue behind each other.** CLIs run typed operator commands via `operator_command_background` (own thread), so `!estop` never waits behind `land`. The dashboard runs STOP/E-STOP/Resume on `_INSTANT_POOL` and Land on `_LAND_POOL`.
- **Live session** (`voice/gemini_live.py`): BLOCKING tools, each run as its own task; `tool_call_cancellation` aborts in-flight motion with `hover()`; context-window compression; session resumption with go_away and backoff reconnects; tool calls of a dropped connection are cancelled if not started, and one summary notice goes to the model after reconnect; push-to-talk (dashboard) or server VAD with an echo gate (CLI); video at most 1 FPS; spoken stop words act without the model, on a dedicated thread.

### Module Responsibilities

| Module | Role |
|---|---|
| `config.py` | Model IDs, HTTP retry, thinking levels, safety thresholds, Live/dashboard settings, `TOOL_SCHEMAS`, `OPERATOR_ONLY_ACTIONS` |
| `main.py` | Arg parsing, toolkit creation (exits if the real drone is unreachable), watchdog start, mode dispatch, land on exit |
| `core/toolkit.py` | `execute()` (LLM) and `operator_command()` / `operator_command_background()` (human); drone I/O under `_cmd_lock`, stop epoch, settle windows, sensor sync, `parse_operator_text()` |
| `core/safety.py` | Per-command checks: flying, battery, move/rotate/speed bounds, altitude floor/ceiling, geofence, flip |
| `core/watchdog.py` | `SafetyWatchdog` thread: battery warn/force-land (own thread, 5 s retry), keepalive, link-loss alerts, height-sensor state reconciliation, sim battery drain |
| `core/state.py` | `DroneState`, `Position`, `project_move()`, operator hold, link status, action log |
| `core/agent.py` | Text agent: function-calling loop, multimodal frames, history trimming and rollback, CLI REPL |
| `core/genai_client.py` | `make_client()`: Gemini HTTP client with retry/backoff (`config.GEMINI_HTTP_RETRY`) for the text agent and vision |
| `core/tracing.py`, `core/cost.py` | Traces/spans (JSONL + dashboard) and per-model pricing |
| `vision/llm_vision.py` | One-shot frame analysis with JSON schemas: `describe_scene`, `check_path_clear`, `search_for_object`, `find_doors_and_openings` |
| `voice/gemini_live.py`, `voice/audio.py` | `LiveAgent` and mic/speaker I/O |
| `features/search.py` | `ObjectSearcher` multi-room search via `execute()`; aborts on hold/low battery. Not exposed as a tool |
| `dashboard/server.py` | FastAPI + WebSocket: chat, operator controls (dedicated `_INSTANT_POOL` / `_LAND_POOL`), safety alerts, telemetry, Origin/token checks |
| `dashboard/frontend/` | React/Vite console (STOP/Land/E-STOP, hotkeys, Resume AI banner, push-to-talk, traces) |
| `tests/` | `test_safety_core`, `test_toolkit`, `test_agent`, `test_live_agent`, `test_live_latency`, `test_server`, `test_vision`, `test_search`, `test_model_config`, `test_tracing` |

### Models (config.py `MODELS`)

- Text agent and reasoning vision: `gemini-3.8-flash`, thinking `medium` (never send `minimal`; 3.8 Flash rejects it). No temperature.
- Quick checks (`check_path_clear`): `gemini-3.5-flash-lite`, thinking `minimal`.
- Live: `gemini-3.8-live` (not `-extended-thinking`, which rejects BLOCKING tools). No thinking config.
- Spatial: `gemini-robotics-er-2-preview`, configured but unused.

### Safety Thresholds (config.py `SAFETY`)

- Max 100 cm per move (clamped), min 20 cm; altitude floor 20 cm, ceiling 200 cm; geofence 800 cm from takeoff
- Rotation clamped to ±360°; speed 10–100 cm/s, 30 cm/s on connect
- Battery: takeoff refused at ≤20%, watchdog warns at ≤20%, force-lands at ≤10%; flip needs >50%
- Keepalive `rc 0 0 0 0` after 8 s idle in flight; link lost after 5 s without state packets
- Command layer: settle 0.7 s past an interrupted motion (`abort_settle_s`, ends early after 0.4 s stationary); land confirmed by height (>10 cm = airborne) within 6 s; operator land waits up to 25 s for the lock, 3 land attempts; watchdog force-land retry every 5 s

## Environment Setup

`.env` (see `.env.example`): `GEMINI_API_KEY` (required), `OPENAI_API_KEY` (unused), `DASHBOARD_HOST` (default `127.0.0.1`), `DASHBOARD_PORT` (default `8080`), optional `DASHBOARD_TOKEN` and `DASHBOARD_ALLOWED_ORIGINS`.

A real drone needs two networks: `TELLO-*` WiFi for the drone and a second link for Gemini.

## Conventions

- Toolkit methods return `{"success": bool, "message" | "error": str, ...}` dicts.
- Never expose operator-only actions to an LLM. New LLM tools go in `TOOL_SCHEMAS` and the `execute()` dispatch; add motion tools to `MOTION_TOOLS` so the operator hold covers them, pass them the `epoch` captured in `execute()`, and do check + send + state update inside one `with self._cmd_lock:` block (send via `_run_motion` / `_drone_call`).
- Toolkit calls block (UDP round-trips, multi-second moves). From async code, always call them via `asyncio.to_thread` (or an executor), never directly on the event loop.
- Watchdog listeners run on the watchdog thread; marshal to the event loop with `loop.call_soon_threadsafe`.
- Tests use simulated toolkits (fixtures such as `toolkit`, `flying_toolkit`), a `FakeTello` for real-drone code paths, and fake Gemini clients. No test needs an API key or a drone.
- `legacy/` is the archived pre-AI codebase (2021–2022) and is not used.
