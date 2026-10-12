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

# ── Model Strings (June 2026) ──
MODELS = {
    # Real-time voice + vision via Live API WebSocket.
    "live": "gemini-3.1-flash-live-preview",
    # Main reasoning model for the non-Live text agent in core/agent.py.
    "vision_reasoning": "gemini-3.5-flash",
    # Fast yes/no checks, "is path clear?", cheap.
    "vision_quick": "gemini-3.1-flash-lite",
    # Spatial precision: 2D object pointing and embodied reasoning.
    "vision_spatial": "gemini-robotics-er-1.6-preview",
    # OpenAI fallback (unused for now)
    "voice_fallback": "gpt-realtime",
}

# ── Thinking Levels (Gemini 3.5 Flash / 3.1 Flash-Lite) ──
THINKING = {
    "reasoning": "medium",   # For scene analysis, planning
    "quick": "minimal",      # For path-clear checks, simple yes/no
    "spatial": "low",        # For coordinate extraction
}

# ── Drone Safety Thresholds ──
SAFETY = {
    "geofence_radius_cm": 800,       # Max 8m from takeoff
    "max_altitude_cm": 200,          # 2m ceiling for indoor
    "battery_warn_pct": 20,          # Hover + warn user
    "battery_critical_pct": 10,      # Force land
    "battery_check_interval_s": 5,   # Check every 5 seconds
    "max_single_move_cm": 100,       # Must check frame after this
    "min_move_cm": 20,               # djitellopy minimum
    "max_move_cm": 500,              # djitellopy maximum
    "max_rotate_deg": 360,           # Max rotation per command
    "connection_timeout_s": 5,       # Auto-land on WiFi dropout
    "default_speed_cms": 30,         # Conservative indoor speed
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
    "video_fps": 1.0,                # Drone frames per second pumped to the Live session
    "video_jpeg_quality": 60,        # Keep video cheap — Live video counts toward tokens
    "video_max_side": 640,           # Downscale for lower token cost
    "turn_coverage": "TURN_INCLUDES_ONLY_ACTIVITY",  # Only count video frames during user activity
    "enable_vad": True,              # Let Gemini auto-detect end-of-turn from audio
    "input_transcription": True,     # Server-side transcription of user audio
    "output_transcription": True,    # Server-side transcription of model audio
    # Tools NOT exposed to the Live model — video streaming replaces these.
    "disabled_tools": ["get_frame", "stream_on", "stream_off"],
}

# ── Dashboard ──
DASHBOARD = {
    "host": os.getenv("DASHBOARD_HOST", "0.0.0.0"),
    "port": int(os.getenv("DASHBOARD_PORT", "8080")),
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
        "description": "Move the drone in a direction by a distance. Always call get_frame() after moving 100cm or more — this is your only obstacle avoidance. Minimum 20cm, maximum 500cm per call.",
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
                    "description": "Distance in centimeters. Must be between 20 and 500.",
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
        "name": "flip",
        "description": "Perform an aerial flip. Requires >50% battery.",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["forward", "back", "left", "right"],
                    "description": "Flip direction",
                },
            },
            "required": ["direction"],
        },
    },
    {
        "name": "emergency_stop",
        "description": "EMERGENCY: Kill all motors immediately. Drone will fall. Only use in emergencies.",
        "parameters": {"type": "object", "properties": {}, "required": []},
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
