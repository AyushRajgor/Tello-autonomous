# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TelloAI Agent is a voice-controlled autonomous drone system for the DJI Tello, powered by Google Gemini LLM agents. It supports three operational modes: text REPL (Phase 1), web dashboard (Phase 2), and real-time voice via Gemini Live API (Phase 3). The system runs in simulation mode without hardware.

## Commands

```bash
# Run
python main.py                # Text mode (interactive REPL)
python main.py --dashboard    # Web dashboard at localhost:8080
python main.py --voice        # Voice mode
python main.py --simulate     # Simulation (no drone hardware)
python main.py --simulate --webcam --dashboard  # Simulation with laptop camera

# Tests
python -m pytest tests/ -v              # All tests (27 tests)
python -m pytest tests/test_toolkit.py -v  # Full suite
python -m pytest tests/test_toolkit.py::TestSafety -v  # Single test class
python -m pytest tests/test_toolkit.py::TestSafety::test_geofence -k "geofence"  # Single test

# Network check (verify Tello WiFi + internet)
python scripts/network_check.py

# Install
pip install -r requirements.txt
```

No linter or formatter is configured. No build step required.

## Architecture

### Request Flow

```
User input (text/voice/dashboard)
  → TelloAgent.process_message()        # core/agent.py
    → Gemini LLM (with tool schemas)
    → LLM emits function_calls
      → SafetySystem.check_*()          # core/safety.py — validates
      → TelloToolkit.execute()          # core/toolkit.py — executes via djitellopy
      → DroneState.update_*()           # core/state.py — dead-reckoning position
      → If get_frame: vision.describe_scene()  # vision/llm_vision.py
    → Results fed back to LLM (loops up to 15 iterations)
  → Final text response to user
```

### Key Design Decisions

- **Single dispatch entry point**: `TelloToolkit.execute(function_name, args)` maps function names to methods via a dict. Both the agent and voice systems use this same interface.
- **Centralized tool schemas**: All 14 LLM-callable function schemas are defined in `config.py` as `TOOL_SCHEMAS` and converted to Gemini format by the agent/voice modules.
- **Safety as a separate validation layer**: `SafetySystem` receives `DroneState` and returns `SafetyCheckResult(allowed, reason, modified_params)`. It clamps parameters (e.g., distance, rotation) rather than rejecting outright when possible.
- **Dead-reckoning position tracking**: `DroneState` tracks x/y/z relative to takeoff origin using heading + trigonometry. No GPS — the Tello has no positioning sensors.
- **Geofence uses 2D Pythagorean distance** from origin (max 800cm). Altitude capped at 200cm separately.
- **Simulation mode**: The toolkit returns placeholder data when no drone is connected. All tests run in simulation.

### Module Responsibilities

| Module | Role |
|---|---|
| `config.py` | All constants, model IDs, safety thresholds, tool schemas, env vars |
| `core/toolkit.py` | Command execution: validate → execute → update state → return result dict |
| `core/agent.py` | Multi-turn LLM loop with function calling (Gemini 3 Flash) |
| `core/safety.py` | Pre-flight validation: geofence, altitude, battery, parameter bounds |
| `core/state.py` | `DroneState` + `Position` dataclasses, position math, action logging |
| `vision/llm_vision.py` | Frame analysis: `describe_scene`, `check_path_clear`, `search_for_object`, `find_doors_and_openings` |
| `voice/gemini_live.py` | Bidirectional audio streaming via Gemini Live API |
| `features/search.py` | Autonomous multi-room object search (not yet integrated into agent) |
| `dashboard/server.py` | FastAPI + WebSocket server with embedded HTML dashboard |

### Vision Model Routing

- **Reasoning tasks** (scene description, object search): `gemini-3-flash-preview` with `thinking=medium`
- **Quick checks** (path clear): `gemini-3.1-flash-lite` with `thinking=minimal`
- Model IDs and parameters are in `config.py`

### Safety Thresholds (from config.py)

- Geofence: 800cm from takeoff
- Max altitude: 200cm
- Battery warn: 20%, critical/force-land: 10%
- Max single move: 100cm (triggers automatic vision check)
- Flip requires >50% battery
- Move distance clamped: 20–500cm, rotation clamped: ±360°

## Environment Setup

Requires a `.env` file (see `.env.example`):
- `GEMINI_API_KEY` — required
- `OPENAI_API_KEY` — optional
- `DASHBOARD_HOST` / `DASHBOARD_PORT` — defaults to `0.0.0.0:8080`

The drone requires dual WiFi: connect to `TELLO-*` for drone control and maintain internet for Gemini API calls.

## Conventions

- All toolkit methods return `{"success": bool, "message": str, ...}` dicts.
- Async throughout: agent, vision, voice, and dashboard are all async/await.
- Tests use pytest with fixtures (`toolkit`, `flying_toolkit`) that create simulated TelloToolkit instances.
- `legacy/` contains the archived pre-AI codebase (2021–2022) and is not used by the current system.
