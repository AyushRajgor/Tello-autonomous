"""
TelloAI Agent — Central Configuration

All constants, model strings, thresholds, and tool schemas in one place.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── API Keys ──
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ── Model Strings ──
# Each is the newest model of its line, verified 2026-10-13 against the
# Gemini API models list for this key and ai.google.dev/gemini-api/docs/models.
# (gemini-omni-* are video-generation models — not applicable here.)
MODELS = {
    # Real-time voice + vision via Live API WebSocket. GA since 2026-09-15;
    # replaces gemini-3.1-flash-live-preview (shutdown 2026-11-17).
    # Do NOT use -extended-thinking: it rejects BLOCKING tools, and drone
    # actions must complete before the model continues.
    "live": "gemini-3.8-live",
    # Main reasoning model for the non-Live text agent in core/agent.py.
    # GA since 2026-09-02 (gemini-3.5-flash was deprecated 2026-10-08).
    # Thinking levels: low/medium/high — "minimal" is rejected.
    "vision_reasoning": "gemini-3.8-flash",
    # Fast yes/no checks, "is path clear?". GA since 2026-07-21; newest and
    # fastest Flash-Lite (supports thinking "minimal"). Replaces
    # gemini-3.1-flash-lite (cheaper, GA until 2027-05-07).
    "vision_quick": "gemini-3.5-flash-lite",
    # Spatial precision: 2D object pointing ([y, x] normalized 0-1000).
    # gemini-robotics-er-1.6-preview was shut down 2026-08-31.
    "vision_spatial": "gemini-robotics-er-2-preview",
    # OpenAI fallback (unused for now). gpt-realtime-2.1: newest Realtime-API
    # model with tool use (gpt-live-1 lacks image/video input and the
    # Realtime endpoint, so it can't drive the drone).
    "voice_fallback": "gpt-realtime-2.1",
}

# ── HTTP retries for generate_content (agent + vision) ──
# Gemini returns 503 "high demand" / 429 spikes that clear within seconds;
# retrying beats failing a whole drone instruction. (The Live WebSocket has
# its own reconnect logic in voice/gemini_live.py.)
GEMINI_HTTP_RETRY = {
    "attempts": 4,                 # total tries, including the first
    "initial_delay": 1.0,          # seconds; grows exponentially with jitter
    "max_delay": 8.0,
    "http_status_codes": [408, 429, 500, 502, 503, 504],
}

# ── Thinking Levels ──
# Sampling params (temperature/top_p/top_k) are deprecated for Gemini 3.8 and
# ignored by the backend — don't send them.
THINKING = {
    "reasoning": "medium",   # gemini-3.8-flash: scene analysis, planning (low|medium|high)
    "quick": "minimal",      # gemini-3.5-flash-lite: path-clear checks, simple yes/no
    "spatial": "low",        # robotics-er: coordinate extraction
}

# ── Drone Safety Thresholds ──
# Every value here is enforced in code (core/safety.py, core/toolkit.py,
# core/watchdog.py) — not just described in the prompt.
SAFETY = {
    "geofence_radius_cm": 800,       # Max 8m from takeoff
    "max_altitude_cm": 200,          # 2m ceiling for indoor
    "min_safe_altitude_cm": 20,      # Never descend below this while flying
    "battery_warn_pct": 20,          # Warn user (watchdog + LLM status line); no takeoff at or below
    "battery_critical_pct": 10,      # Watchdog force-lands
    "max_single_move_cm": 100,       # Longer moves are clamped: look, then move again
    "min_move_cm": 20,               # Tello SDK minimum
    "max_rotate_deg": 360,           # Max rotation per command
    "flip_min_battery_pct": 50,      # Flips are operator-only and need a healthy battery
    "default_speed_cms": 30,         # Sent to the drone on connect
    "watchdog_interval_s": 1.0,      # Safety watchdog tick
    "keepalive_interval_s": 8,       # Tello auto-lands after 15s without a command
    "connection_timeout_s": 5,       # No state packet for this long => link lost
    # Operator land waits this long for an in-flight command to return. After a
    # `stop` that is usually ~1s, but it must cover the longest motion timeout
    # (100cm at 10cm/s, or a 360° turn) in case the stop reply is lost. The
    # Tello typically rejects `land` mid-move, so waiting beats interrupting.
    "operator_land_lock_timeout_s": 25.0,
    # After a raw stop/land/emergency, the next command waits this long past the
    # end of any interrupted motion, then discards stray replies — djitellopy
    # doesn't match replies to commands, so a late "ok"/"error" could otherwise
    # be read as the next command's answer.
    "abort_settle_s": 0.7,
    # ...but end that wait early once the state packet shows the drone stationary
    # (zero velocity, steady yaw) for this long — `stop` may already have halted it.
    "stationary_confirm_s": 0.4,
    "sensor_settle_s": 1.5,          # Height re-read window after an interrupted takeoff
    "land_confirm_s": 6.0,           # A land "ok" must be confirmed by the height sensor within this
    "priority_land_attempts": 3,     # Operator/watchdog land retries while the drone stays airborne
    "force_land_retry_s": 5.0,       # Watchdog retries a failed force-land this often
    "height_airborne_cm": 10,        # Height-sensor reading above this means airborne
}

# ── Simulation ──
SIMULATION = {
    "takeoff_height_cm": 80,         # Tello hovers at ~80cm after takeoff
    "battery_drain_pct_per_min": 7.5,  # ~13 min flight time, matches a real Tello
}

# ── Drone Movement Defaults ──
MOVEMENT = {
    "valid_directions": ["forward", "back", "left", "right", "up", "down"],
    "valid_flip_directions": ["forward", "back", "left", "right"],
    "scan_rotation_step_deg": 60,    # For 360° room scanning
    "explore_move_step_cm": 80,      # Move increment during exploration
}

# ── Vision Pipeline ──
VISION = {
    "frame_width": 720,              # Downscale from 960 for speed
    "frame_height": 480,
    "jpeg_quality": 70,              # Balance quality vs token cost
    "media_resolution": "medium",    # Gemini media_resolution param
}

# ── Live API (voice + video streaming) ──
LIVE = {
    "voice_name": "Aoede",           # Prebuilt voice: Aoede (warm/friendly). Others: Puck, Charon, Kore, Fenrir
    "input_sample_rate": 16000,      # Mic capture (Live API requires 16 kHz PCM)
    "output_sample_rate": 24000,     # Speaker playback (Live API returns 24 kHz PCM)
    "video_fps": 1.0,                # Max frame rate (Live API accepts <= 1 FPS)
    "video_jpeg_quality": 60,        # Keep video cheap — Live video counts toward tokens
    "video_max_side": 640,           # Downscale before sending
    "media_resolution": "MEDIA_RESOLUTION_LOW",  # ~70 tokens/frame on Gemini 3 models
    # Open mic streams video continuously, so only count input during speech.
    "turn_coverage": "TURN_INCLUDES_ONLY_ACTIVITY",
    # Push-to-talk already rations frames client-side (during a held turn and
    # right after each move), so include all of them — otherwise the frame
    # sent after a move, outside user speech, could be ignored.
    "turn_coverage_ptt": "TURN_INCLUDES_AUDIO_ACTIVITY_AND_ALL_VIDEO",
    # Open-mic (CLI --voice) automatic activity detection. Push-to-talk
    # (dashboard) disables it and sends explicit activity_start/activity_end.
    "vad_start_sensitivity": "START_SENSITIVITY_LOW",
    "vad_silence_duration_ms": 500,
    "vad_prefix_padding_ms": 200,
    "ptt_tail_ms": 300,              # Audio kept flowing after PTT release before activity_end
    # Without compression, audio+video sessions die after 2 minutes.
    "compression_trigger_tokens": 25600,
    "compression_target_tokens": 12800,
    # Connections last ~10 min; resume the same session via resumption handles.
    "reconnect_max_attempts": 5,
    "reconnect_backoff_s": 1.0,      # Doubles per failed attempt
    "input_transcription": True,     # Server-side transcription of user audio
    "output_transcription": True,    # Server-side transcription of model audio
    # Tools NOT exposed to the Live model — video streaming replaces these.
    "disabled_tools": ["get_frame", "stream_on", "stream_off"],
}

# ── Dashboard ──
DASHBOARD = {
    # Loopback by default: the dashboard can fly the drone, so it must not be
    # reachable from the LAN unless you opt in (and set DASHBOARD_TOKEN).
    "host": os.getenv("DASHBOARD_HOST", "127.0.0.1"),
    "port": int(os.getenv("DASHBOARD_PORT", "8080")),
    # Optional shared secret required on the WebSocket when exposed beyond loopback.
    "token": os.getenv("DASHBOARD_TOKEN", ""),
    # Extra allowed browser origins (comma-separated), e.g. a Vite dev server.
    "extra_origins": [o.strip() for o in os.getenv("DASHBOARD_ALLOWED_ORIGINS", "").split(",") if o.strip()],
}

# ── Tracing ──
TRACING = {
    "enabled": True,
    "log_dir": "logs",
    "log_file": "traces.jsonl",
    "max_recent_traces": 100,      # keep in memory for dashboard
    "thumbnail_width": 160,
    "thumbnail_height": 120,
    "thumbnail_quality": 40,
    "terminal_output": True,        # print trace summaries to terminal
}

# ── Tool Schemas for LLM Function Calling ──
TOOL_SCHEMAS = [
    {
        "name": "takeoff",
        "description": "Launch the drone into the air. Must be called before any movement.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "land",
        "description": "Land the drone safely at current position.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "move",
        "description": "Move the drone in a direction by a distance. Minimum 20cm, maximum 100cm per call (longer requests are clamped to 100cm). Look at the camera before each move — this is your only obstacle avoidance.",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["forward", "back", "left", "right", "up", "down"],
                    "description": "Direction to move",
                },
                "cm": {
                    "type": "integer",
                    "description": "Distance in centimeters. Must be between 20 and 100.",
                },
            },
            "required": ["direction", "cm"],
        },
    },
    {
        "name": "rotate",
        "description": "Rotate the drone yaw. Positive degrees = clockwise, negative = counter-clockwise. Range: -360 to 360.",
        "parameters": {
            "type": "object",
            "properties": {
                "degrees": {
                    "type": "integer",
                    "description": "Degrees to rotate. Positive = clockwise, negative = counter-clockwise. Between -360 and 360.",
                },
            },
            "required": ["degrees"],
        },
    },
    {
        "name": "get_frame",
        "description": "Capture a photo from the drone's camera. Returns a base64 JPEG image. Use this to see what the drone sees — for obstacle checking, object search, scene description.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_battery",
        "description": "Get a fresh battery reading. NOTE: Battery is already included in the [Drone Status] at the start of every user message — use that value directly. Only call this if you need a real-time update mid-flight after extended operations.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_height",
        "description": "Get current altitude in centimeters.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_temperature",
        "description": "Get drone's internal temperature.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_speed",
        "description": "Get current speed setting in cm/s.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "set_speed",
        "description": "Set flight speed in cm/s. Valid range: 10 to 100. Default is 30 (conservative indoor speed).",
        "parameters": {
            "type": "object",
            "properties": {
                "speed": {
                    "type": "integer",
                    "description": "Speed in cm/s. Must be between 10 and 100.",
                },
            },
            "required": ["speed"],
        },
    },
    {
        "name": "stream_on",
        "description": "Start the video stream from drone's camera.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "stream_off",
        "description": "Stop the video stream.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]

# Actions only a human operator can trigger (dashboard buttons, CLI, watchdog).
# They are deliberately absent from TOOL_SCHEMAS so no LLM can call them: a
# misheard word must never cut the motors or flip the drone indoors.
OPERATOR_ONLY_ACTIONS = ("emergency_stop", "flip", "hover")
