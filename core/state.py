"""
Drone State Tracker

Maintains estimated position (dead reckoning), battery, flight status.
Position is tracked relative to takeoff point (0, 0, 0).
"""

import time
import math
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Position:
    """3D position in cm relative to takeoff point."""
    x: float = 0.0  # left(-) / right(+)
    y: float = 0.0  # back(-) / forward(+)
    z: float = 0.0  # altitude

    def distance_from_origin(self) -> float:
        """Horizontal distance from takeoff point."""
        return math.sqrt(self.x**2 + self.y**2)

    def to_dict(self) -> dict:
        return {"x": round(self.x, 1), "y": round(self.y, 1), "z": round(self.z, 1)}


@dataclass
class DroneState:
    """Full drone state — updated after every action."""

    # Position & orientation
    position: Position = field(default_factory=Position)
    heading_deg: float = 0.0  # 0 = initial facing, clockwise positive

    # Flight status
    is_flying: bool = False
    is_streaming: bool = False

    # Telemetry
    battery_pct: int = 100
    height_cm: int = 0
    speed_cms: int = 30
    temperature: str = "unknown"

    # Timestamps
    takeoff_time: Optional[float] = None
    last_update: float = field(default_factory=time.time)

    # History for debugging
    action_log: list = field(default_factory=list)

    def update_position_after_move(self, direction: str, cm: int):
        """Update estimated position based on movement + current heading."""
        # Convert heading to radians (0° = forward/+y, clockwise positive)
        heading_rad = math.radians(self.heading_deg)

        dx, dy, dz = 0.0, 0.0, 0.0

        if direction == "forward":
            dx = cm * math.sin(heading_rad)
            dy = cm * math.cos(heading_rad)
        elif direction == "back":
            dx = -cm * math.sin(heading_rad)
            dy = -cm * math.cos(heading_rad)
        elif direction == "left":
            dx = -cm * math.cos(heading_rad)
            dy = cm * math.sin(heading_rad)
        elif direction == "right":
            dx = cm * math.cos(heading_rad)
            dy = -cm * math.sin(heading_rad)
        elif direction == "up":
            dz = cm
        elif direction == "down":
            dz = -cm

        self.position.x += dx
        self.position.y += dy
        self.position.z += dz
        self.last_update = time.time()

    def update_heading_after_rotate(self, degrees: int):
        """Update heading after rotation."""
        self.heading_deg = (self.heading_deg + degrees) % 360
        self.last_update = time.time()

    def reset(self):
        """Reset to initial state (on takeoff or reconnect)."""
        self.position = Position()
        self.heading_deg = 0.0
        self.is_flying = False
        self.is_streaming = False
        self.takeoff_time = None
        self.action_log = []

    def log_action(self, action: str, params: dict, result: bool):
        """Log an action for debugging."""
        self.action_log.append({
            "time": time.time(),
            "action": action,
            "params": params,
            "result": result,
            "position": self.position.to_dict(),
            "heading": self.heading_deg,
            "battery": self.battery_pct,
        })

    def flight_duration_s(self) -> Optional[float]:
        """Seconds since takeoff, or None if not flying."""
        if self.takeoff_time is None:
            return None
        return time.time() - self.takeoff_time

    def to_dict(self) -> dict:
        """Full state as dict for dashboard / LLM context."""
        return {
            "position": self.position.to_dict(),
            "heading_deg": round(self.heading_deg, 1),
            "distance_from_home_cm": round(self.position.distance_from_origin(), 1),
            "is_flying": self.is_flying,
            "is_streaming": self.is_streaming,
            "battery_pct": self.battery_pct,
            "height_cm": self.height_cm,
            "speed_cms": self.speed_cms,
            "flight_duration_s": round(self.flight_duration_s() or 0, 1),
        }
