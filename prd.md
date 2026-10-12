# TelloAI Agent — PRD

> Voice-controlled autonomous drone powered by LLM agents. Built on DJI Tello.

## What This Is

An AI agent that controls a DJI Tello drone through natural voice conversation. You talk to it, it sees through the drone's camera, reasons about the environment, and executes physical actions autonomously. The LLM is both the planner and the perception engine — no hardcoded commands, no separate SLAM, no local ML models.

**Existing repo:** `github.com/ARajgor/Tello-autonomous` (2022, pre-AI era)
**What we're keeping:** `djitellopy` SDK, basic drone connection logic
**What we're replacing:** Everything else — voice pipeline, detection models, control logic

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│                   USER (Voice)                  │
└──────────────────────┬──────────────────────────┘
                       │ mic audio stream
                       ▼
┌─────────────────────────────────────────────────┐
│          VOICE LAYER (Gemini Live API)          │
│  Model: gemini-3.1-flash-live-preview           │
│  - Real-time audio in/out via WebSocket         │
│  - Function calling for drone actions           │
│  - Maintains conversation context               │
│  - Supports barge-in (user can interrupt)       │
│  Fallback: OpenAI gpt-realtime (WebSocket)      │
└──────────────────────┬──────────────────────────┘
                       │ function calls
                       ▼
┌─────────────────────────────────────────────────┐
│            AGENT CORE (Python)                  │
│  - Orchestrates tool calls from LLM             │
│  - Manages vision pipeline (frame → LLM)        │
│  - Safety layer (geofence, battery, bounds)     │
│  - Exploration state machine                    │
└────────┬────────────────────────┬───────────────┘
         │                        │
         ▼                        ▼
┌─────────────────┐  ┌───────────────────────────┐
│  VISION LAYER   │  │     DRONE CONTROL LAYER   │
│                 │  │                           │
│ Scene analysis: │  │  TelloToolkit class       │
│ Gemini 3.5 Flash│  │  wrapping djitellopy      │
│  (on demand)    │  │                           │
│                 │  │  All actions exposed as   │
│ Quick checks:   │  │  callable functions with  │
│  Gemini 3.1     │  │  typed params + returns   │
│  Flash-Lite     │  │                           │
│                 │  │  UDP over WiFi to Tello   │
│ Spatial:        │  │                           │
│  Robotics-ER    │  │                           │
│  1.6 (optional) │  │                           │
└─────────────────┘  └───────────────────────────┘
```

**No local ML models.** No YOLO, no OpenCV DNN, no local inference. The LLM handles ALL perception. Safety comes from conservative movement (short bursts + check frame), geofencing, and keyboard kill switch — not from real-time local object detection. The Tello has no depth sensor anyway, so local bounding boxes can't give real obstacle avoidance.

---

## Models (updated June 2026)

| Role | Model | Model String | Why |
|------|-------|-------------|-----|
| **Voice (real-time)** | Gemini 3.1 Flash Live Preview | `gemini-3.1-flash-live-preview` | Live API model, function calling, audio+video streaming, lowest latency for voice |
| **Vision (reasoning)** | Gemini 3.5 Flash | `gemini-3.5-flash` | Current Flash reasoning model for multimodal planning and scene understanding |
| **Vision (quick checks)** | Gemini 3.1 Flash-Lite | `gemini-3.1-flash-lite` | Fast, cheaper model for simple yes/no frame checks |
| **Spatial precision** | Gemini Robotics-ER 1.6 Preview | `gemini-robotics-er-1.6-preview` | 2D object pointing, precise coords — only when exact position needed |
| **Voice fallback** | OpenAI gpt-realtime | `gpt-realtime` | Alternative if Gemini Live has issues |

**Model routing strategy:**
- Voice conversation → `gemini-3.1-flash-live-preview` (Live API, always on)
- "What do you see?" / planning / complex reasoning → `gemini-3.5-flash` (thinking_level: medium)
- "Is path clear?" / quick yes/no → `gemini-3.1-flash-lite` (thinking_level: minimal, cheapest + fastest)
- "Point at the TV precisely" → `gemini-robotics-er-1.6-preview` (only when coordinates matter)

**Important Gemini reasoning note:** Gemini reasoning models use thought signatures — encrypted tokens returned with responses that must be passed back in subsequent requests to maintain reasoning context. The agent must handle these in multi-turn function calling flows.

---

## TelloToolkit API

Every drone action is a Python function the LLM can call. Define with JSON schemas for function calling.

```python
class TelloToolkit:
    def takeoff() -> bool
    def land() -> bool
    def move(direction: str, cm: int) -> bool        # "forward"|"back"|"left"|"right"|"up"|"down", 20-500cm
    def rotate(degrees: int) -> bool                  # positive=clockwise, negative=counter
    def get_frame() -> str                            # returns base64 JPEG
    def get_battery() -> int                          # 0-100
    def get_height() -> int                           # cm
    def get_temperature() -> str
    def get_speed() -> int
    def set_speed(speed: int) -> bool                 # 10-100 cm/s
    def flip(direction: str) -> bool                  # "forward"|"back"|"left"|"right"
    def emergency_stop() -> bool                      # kills motors immediately
    def stream_on() -> bool
    def stream_off() -> bool
```

---

## Features (Priority Order)

### P0 — Must Build

**1. Natural Language Control**
User says anything → LLM parses intent → calls toolkit functions → confirms via voice.
No predefined command list. "Take off and go forward a bit" just works.

**2. Object Search ("Find X")**
Core loop: `scan → reason → decide direction → move → scan again`
Use `gemini-3.1-flash-lite` for quick "is this the target?" checks, `gemini-3.5-flash` for planning next move.

**3. Scene Description**
"What do you see?" → capture frame → `gemini-3.5-flash` → speak back.
Supports follow-ups: "is there a laptop on that desk?"

### P1 — Should Build

**4. Change Detection** — baseline scan → patrol → LLM compares → narrates differences
**5. Lost Item Finder** — systematic low-altitude scan → LLM identifies objects in clutter

### P2 — Nice to Have

**6. Follow-Me Mode** — person tracking + LLM-guided framing
**7. Presence Detection** — "is anyone home?" → room-by-room check
**8. Gesture Control** — hand signals via vision model

---

## Tech Stack

| Component | Technology | Notes |
|-----------|-----------|-------|
| Language | Python 3.11+ | |
| Drone SDK | djitellopy | UDP commands, video stream |
| Voice | Gemini Live API | `gemini-3.1-flash-live-preview`, WebSocket |
| Voice fallback | OpenAI gpt-realtime | WebSocket/WebRTC |
| Vision (reasoning) | Gemini 3.5 Flash | `gemini-3.5-flash`, scene understanding + planning |
| Vision (quick) | Gemini 3.1 Flash-Lite | `gemini-3.1-flash-lite`, fast frame checks |
| Vision (spatial) | Gemini Robotics-ER 1.6 Preview | `gemini-robotics-er-1.6-preview`, precise 2D pointing |
| Audio I/O | PyAudio or sounddevice | Mic capture + speaker output |
| Web dashboard | FastAPI + WebSocket | Live video, chat log, telemetry |
| Frontend | HTML/JS or React | Dashboard UI |
| Gemini SDK | google-genai | `pip install google-genai` |

---

## Safety System

Non-negotiable. Implement before any autonomous flight:

- **Keyboard kill switch** — always active, `ESC` → immediate `land()`
- **Geofence** — max 8m radius from takeoff, enforced in TelloToolkit before every move
- **Battery guard** — hover at 20%, force land at 10%, check every 5s
- **Parameter validation** — `move()` clamped 20-500cm, `rotate()` clamped ±360°
- **Connection watchdog** — auto-hover on WiFi dropout, auto-land after 5s timeout
- **LLM action validation** — every function call validated before execution
- **Max altitude** — cap at 2m for indoor use
- **Conservative movement** — never move more than 100cm without stopping and checking camera. This IS the obstacle avoidance — no local ML needed
- **Emergency stop** — hardware motor kill, separate from land()

---

## Network Setup

Tello creates its own WiFi AP. Computer needs internet for LLM APIs simultaneously.

**Solution:** Dual network interface
- WiFi 1 (built-in) → Tello AP
- WiFi 2 (USB adapter) or Ethernet → internet
- Route API calls through internet interface, drone commands through Tello interface

Add `network_check()` that verifies both connections on startup.

---

## Project Structure

```
tello-ai-agent/
├── README.md
├── requirements.txt
├── .env                          # GEMINI_API_KEY, OPENAI_API_KEY
├── config.py                     # Constants, model strings, thinking levels, thresholds
│
├── core/
│   ├── toolkit.py                # TelloToolkit — all drone functions + JSON schemas
│   ├── safety.py                 # Geofence, battery guard, param validation, kill switch
│   ├── agent.py                  # Main agent loop — LLM ↔ toolkit orchestration
│   └── state.py                  # Drone state (position estimate, battery, flight status)
│
├── vision/
│   ├── frame_capture.py          # Camera stream → frame extraction → base64
│   ├── llm_vision.py             # Frame → Gemini 3.5 Flash / 3.1 Flash-Lite analysis
│   └── spatial.py                # Gemini Robotics-ER 1.6 Preview (optional)
│
├── voice/
│   ├── gemini_live.py            # Gemini Live API WebSocket client
│   ├── openai_realtime.py        # OpenAI Realtime API client (fallback)
│   └── audio.py                  # Mic capture + speaker output helpers
│
├── features/
│   ├── search.py                 # "Find X" exploration logic
│   ├── describe.py               # Scene description
│   ├── patrol.py                 # Change detection patrol
│   ├── find_item.py              # Lost item search
│   └── follow.py                 # Follow-me mode (P2)
│
├── dashboard/
│   ├── server.py                 # FastAPI WebSocket server
│   ├── static/
│   └── templates/
│
├── prompts/
│   ├── system_prompt.md          # Main agent system prompt
│   ├── search_prompt.md          # Object search prompt
│   └── describe_prompt.md        # Scene description prompt
│
├── tests/
│   ├── test_toolkit.py
│   ├── test_safety.py
│   ├── test_agent.py
│   └── test_vision.py
│
├── scripts/
│   ├── network_check.py
│   ├── test_connection.py
│   └── calibrate.py
│
└── main.py                       # Entry point
```

---

## Build Phases

### Phase 1: Foundation (Week 1-2)
Build `TelloToolkit` with JSON schemas, `safety.py`, text-based agent loop.
Use `gemini-3.5-flash` with function calling (text mode, not voice yet).
**Test:** Type "take off, go forward 1 meter, rotate 360, land" → drone executes.

### Phase 2: Vision (Week 3-4)
Add frame capture pipeline. Implement `get_frame()` as a callable tool.
Route to `gemini-3.5-flash` (thinking: medium) for descriptions, `gemini-3.1-flash-lite` (thinking: minimal) for quick checks.
Handle Gemini thought signatures in multi-turn flows.
**Test:** "What do you see?" returns accurate description. "Find the red cup" works in one room.

### Phase 3: Voice (Week 5-6)
Replace text with Gemini Live API (`gemini-3.1-flash-live-preview`). Handle dual WiFi.
**Test:** Full voice conversation with drone — commands, questions, status updates.

### Phase 4: Exploration (Week 7-9)
Multi-room search, change detection, lost item finder.
**Test:** "Find the TV" works across 2-3 rooms.

### Phase 5: Polish (Week 10)
Web dashboard, demo scripts, documentation. Prep for Surat AI meetup demo.

---

## System Prompt (Draft)

```markdown
You are TelloAI, an AI agent controlling a DJI Tello drone. You can fly, see, and
talk to the user in real-time.

## Your capabilities
- You control the drone through function calls (takeoff, land, move, rotate, etc.)
- You can see through the drone's camera by calling get_frame()
- You speak responses back to the user via voice

## Rules
- ALWAYS check battery before starting a task. If below 20%, tell the user and land.
- NEVER move more than 100cm without stopping and checking the frame first.
  This is your obstacle avoidance — move in short bursts, look, move again.
- When searching for objects, rotate in 60° increments, analyze each frame, decide.
- If you see a door or opening, describe it before navigating through.
- Always confirm dangerous actions: "I'm going to fly through the doorway. Should I proceed?"
- If unsure, hover in place and ask the user.
- Keep voice responses short during flight. Details when hovering or landed.
- Track approximate position relative to takeoff. Never exceed 8m from start.
- You have NO depth sensor. Judge distance from object size in frame.
  When in doubt, move slowly in small increments.

## Exploration strategy
1. Scan current room (rotate 360° in 60° steps, analyze each frame)
2. Build mental model: what objects are here, where are doors/openings
3. If target not found, pick most promising exit and navigate to it
4. Repeat in new room
5. Report findings throughout
```

---

## Key Constraints

- **Battery:** 13 min max. Every feature must account for this.
- **WiFi range:** ~10m. Geofence at 8m.
- **Camera:** 720p, decent in good light, poor in low light.
- **No GPS:** Indoor only. Dead reckoning from movement commands.
- **No depth sensor:** LLM visual reasoning + conservative movement = obstacle avoidance.
- **LLM latency:** 1-3s per vision call. On-demand frames only.
- **Cost per 10-min session:** ~$0.10-0.50 depending on frame frequency.

---

## Open Questions

1. Gemini Live vs OpenAI Realtime first? (Rec: Gemini)
2. Gujarati voice support? (Gemini Live supports it natively)
3. Frame resolution: 720p or downscale to 360p?
4. Dashboard: plain HTML/JS or React?
5. Record sessions from day 1?
6. Use `media_resolution` param on Gemini 3.5 Flash to control vision token cost?
