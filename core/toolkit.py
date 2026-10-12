"""
TelloToolkit

Wraps djitellopy with safety validation. Every function:
1. Validates via SafetySystem
2. Executes via djitellopy
3. Updates DroneState
4. Returns result

This class is what the LLM calls via function calling.
"""

import base64
import logging
import time
from typing import Optional

import cv2
import numpy as np

from config import SAFETY, VISION
from core.state import DroneState
from core.safety import SafetySystem

logger = logging.getLogger(__name__)


def _encode_frame_as_jpeg_b64(frame, input_rgb: bool = False) -> str:
    """Resize and JPEG-encode a camera frame as base64."""
    resized = cv2.resize(
        frame,
        (VISION["frame_width"], VISION["frame_height"]),
        interpolation=cv2.INTER_AREA,
    )
    if input_rgb:
        resized = cv2.cvtColor(resized, cv2.COLOR_RGB2BGR)

    ok, encoded = cv2.imencode(
        ".jpg",
        resized,
        [int(cv2.IMWRITE_JPEG_QUALITY), int(VISION["jpeg_quality"])],
    )
    if not ok:
        raise ValueError("Failed to encode frame as JPEG")
    return base64.b64encode(encoded).decode("utf-8")


class TelloToolkit:
    """All drone actions exposed as callable functions for the LLM."""

    def __init__(self, tello=None, simulate: bool = False, use_webcam: bool = False):
        """
        Args:
            tello: djitellopy.Tello instance. None if simulating.
            simulate: If True, don't send real commands (for testing without drone).
            use_webcam: If True and simulating, use laptop camera for vision.
        """
        self.tello = tello
        self.simulate = simulate
        self.use_webcam = use_webcam
        self.state = DroneState()
        self.safety = SafetySystem(self.state)
        self._frame_reader = None
        self._webcam: Optional[cv2.VideoCapture] = None

    # ── Connection ──

    def connect(self) -> bool:
        """Connect to the Tello drone."""
        if self.simulate:
            logger.info("[SIM] Connected to simulated drone")
            self.state.battery_pct = 100
            if self.use_webcam:
                self._webcam = cv2.VideoCapture(0)
                if self._webcam.isOpened():
                    logger.info("[SIM] Laptop webcam connected")
                else:
                    logger.warning("[SIM] Failed to open webcam — falling back to placeholder frames")
                    self._webcam = None
                    self.use_webcam = False
            return True
        try:
            self.tello.connect()
            self.state.battery_pct = self.tello.get_battery()
            logger.info(f"Connected to Tello. Battery: {self.state.battery_pct}%")
            return True
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False

    # ── Flight Commands ──

    def takeoff(self) -> dict:
        """Launch the drone."""
        check = self.safety.check_takeoff()
        if not check.allowed:
            return {"success": False, "error": check.reason}

        if not self.simulate:
            try:
                self.tello.takeoff()
            except Exception as e:
                return {"success": False, "error": str(e)}

        self.state.is_flying = True
        self.state.takeoff_time = time.time()
        self.state.position.z = 80  # Tello hovers at ~80cm after takeoff
        self.state.log_action("takeoff", {}, True)
        logger.info("Takeoff successful")
        return {"success": True, "message": "Drone is airborne at ~80cm"}

    def land(self) -> dict:
        """Land the drone safely."""
        if not self.simulate:
            try:
                self.tello.land()
            except Exception as e:
                return {"success": False, "error": str(e)}

        self.state.is_flying = False
        self.state.position.z = 0
        self.state.log_action("land", {}, True)
        logger.info("Landing successful")
        return {"success": True, "message": "Drone has landed"}

    def move(self, direction: str, cm: int) -> dict:
        """Move in a direction by specified distance."""
        check = self.safety.check_move(direction, cm)
        if not check.allowed:
            return {"success": False, "error": check.reason}

        # Use modified params if safety clamped values
        if check.modified_params:
            direction = check.modified_params.get("direction", direction)
            cm = check.modified_params.get("cm", cm)

        if not self.simulate:
            try:
                move_fn = {
                    "forward": self.tello.move_forward,
                    "back": self.tello.move_back,
                    "left": self.tello.move_left,
                    "right": self.tello.move_right,
                    "up": self.tello.move_up,
                    "down": self.tello.move_down,
                }[direction]
                move_fn(cm)
            except Exception as e:
                return {"success": False, "error": str(e)}

        self.state.update_position_after_move(direction, cm)
        self.state.log_action("move", {"direction": direction, "cm": cm}, True)

        result = {
            "success": True,
            "message": f"Moved {direction} {cm}cm",
            "position": self.state.position.to_dict(),
            "distance_from_home": round(self.state.position.distance_from_origin(), 1),
        }

        # Append battery warning if relevant
        if check.reason and "WARNING" in check.reason:
            result["warning"] = check.reason

        return result

    def rotate(self, degrees: int) -> dict:
        """Rotate the drone. Positive = clockwise."""
        check = self.safety.check_rotate(degrees)
        if not check.allowed:
            return {"success": False, "error": check.reason}

        if check.modified_params:
            degrees = check.modified_params.get("degrees", degrees)

        if not self.simulate:
            try:
                if degrees >= 0:
                    self.tello.rotate_clockwise(degrees)
                else:
                    self.tello.rotate_counter_clockwise(abs(degrees))
            except Exception as e:
                return {"success": False, "error": str(e)}

        self.state.update_heading_after_rotate(degrees)
        self.state.log_action("rotate", {"degrees": degrees}, True)
        return {
            "success": True,
            "message": f"Rotated {'clockwise' if degrees >= 0 else 'counter-clockwise'} {abs(degrees)}°",
            "heading": self.state.heading_deg,
        }

    # ── Camera ──

    def get_frame(self) -> dict:
        """Capture a frame from the drone's camera as base64 JPEG."""
        if self.simulate:
            # Use laptop webcam if available
            if self._webcam is not None and self._webcam.isOpened():
                ret, frame = self._webcam.read()
                if ret:
                    b64 = _encode_frame_as_jpeg_b64(frame)
                    return {"success": True, "image_base64": b64, "source": "webcam"}
                else:
                    logger.warning("Webcam read failed, returning placeholder")

            # Fallback: gray placeholder
            frame = np.full((VISION["frame_height"], VISION["frame_width"], 3), 100, dtype=np.uint8)
            b64 = _encode_frame_as_jpeg_b64(frame)
            return {"success": True, "image_base64": b64, "note": "Simulated frame"}

        try:
            if self._frame_reader is None:
                self._frame_reader = self.tello.get_frame_read()

            frame = self._frame_reader.frame
            if frame is None:
                return {"success": False, "error": "No frame available"}

            # djitellopy (PyAV backend) returns RGB; OpenCV JPEG encoding expects BGR.
            b64 = _encode_frame_as_jpeg_b64(frame, input_rgb=True)

            self.state.log_action("get_frame", {}, True)
            return {"success": True, "image_base64": b64}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def stream_on(self) -> dict:
        """Start video stream."""
        if not self.simulate:
            try:
                self.tello.streamon()
            except Exception as e:
                return {"success": False, "error": str(e)}

        self.state.is_streaming = True
        self.state.log_action("stream_on", {}, True)
        return {"success": True, "message": "Video stream started"}

    def stream_off(self) -> dict:
        """Stop video stream."""
        if not self.simulate:
            try:
                self.tello.streamoff()
            except Exception as e:
                return {"success": False, "error": str(e)}

        self.state.is_streaming = False
        self._frame_reader = None
        self.state.log_action("stream_off", {}, True)
        return {"success": True, "message": "Video stream stopped"}

    # ── Telemetry ──

    def get_battery(self) -> dict:
        """Get battery percentage."""
        if not self.simulate:
            try:
                self.state.battery_pct = self.tello.get_battery()
            except Exception:
                pass
        return {"success": True, "battery_pct": self.state.battery_pct}

    def get_height(self) -> dict:
        """Get current height in cm."""
        if not self.simulate:
            try:
                self.state.height_cm = self.tello.get_height()
            except Exception:
                pass
        return {"success": True, "height_cm": self.state.height_cm}

    def get_temperature(self) -> dict:
        """Get drone temperature."""
        if not self.simulate:
            try:
                self.state.temperature = f"{self.tello.get_temperature()}°C"
            except Exception:
                pass
        return {"success": True, "temperature": self.state.temperature}

    def get_speed(self) -> dict:
        """Get current speed setting."""
        return {"success": True, "speed_cms": self.state.speed_cms}

    def set_speed(self, speed: int) -> dict:
        """Set flight speed (10-100 cm/s)."""
        check = self.safety.check_set_speed(speed)
        if check.modified_params:
            speed = check.modified_params["speed"]

        if not self.simulate:
            try:
                self.tello.set_speed(speed)
            except Exception as e:
                return {"success": False, "error": str(e)}

        self.state.speed_cms = speed
        self.state.log_action("set_speed", {"speed": speed}, True)
        return {"success": True, "message": f"Speed set to {speed} cm/s"}

    def flip(self, direction: str) -> dict:
        """Perform a flip."""
        check = self.safety.check_flip(direction)
        if not check.allowed:
            return {"success": False, "error": check.reason}

        if not self.simulate:
            try:
                flip_map = {"forward": "f", "back": "b", "left": "l", "right": "r"}
                self.tello.flip(flip_map[direction])
            except Exception as e:
                return {"success": False, "error": str(e)}

        self.state.log_action("flip", {"direction": direction}, True)
        return {"success": True, "message": f"Flipped {direction}"}

    def emergency_stop(self) -> dict:
        """Kill all motors immediately. Drone WILL fall."""
        if not self.simulate:
            try:
                self.tello.emergency()
            except Exception:
                pass  # Best effort

        self.state.is_flying = False
        self.state.position.z = 0
        self.state.log_action("emergency_stop", {}, True)
        logger.critical("EMERGENCY STOP triggered")
        return {"success": True, "message": "Emergency stop — motors killed"}

    # ── Tool Dispatch ──

    def execute(self, function_name: str, arguments: dict) -> dict:
        """
        Execute a tool call from the LLM.
        This is the single entry point the agent loop uses.
        """
        # Refresh battery before any action
        if not self.simulate and self.tello:
            try:
                self.state.battery_pct = self.tello.get_battery()
            except Exception:
                pass

        # Check for force land
        if self.safety.should_force_land and function_name not in ("land", "emergency_stop"):
            self.land()
            return {"success": False, "error": "Force landing due to critical battery."}

        dispatch = {
            "takeoff": lambda: self.takeoff(),
            "land": lambda: self.land(),
            "move": lambda: self.move(arguments.get("direction", "forward"), arguments.get("cm", 50)),
            "rotate": lambda: self.rotate(arguments.get("degrees", 90)),
            "get_frame": lambda: self.get_frame(),
            "get_battery": lambda: self.get_battery(),
            "get_height": lambda: self.get_height(),
            "get_temperature": lambda: self.get_temperature(),
            "get_speed": lambda: self.get_speed(),
            "set_speed": lambda: self.set_speed(arguments.get("speed", 30)),
            "flip": lambda: self.flip(arguments.get("direction", "forward")),
            "emergency_stop": lambda: self.emergency_stop(),
            "stream_on": lambda: self.stream_on(),
            "stream_off": lambda: self.stream_off(),
        }

        fn = dispatch.get(function_name)
        if fn is None:
            return {"success": False, "error": f"Unknown function: {function_name}"}

        try:
            return fn()
        except Exception as e:
            logger.error(f"Error executing {function_name}: {e}")
            return {"success": False, "error": str(e)}
