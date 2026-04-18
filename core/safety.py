"""
Safety System

Validates every action before execution. Non-negotiable checks:
- Geofence (max distance from takeoff)
- Battery thresholds (warn / force land)
- Parameter bounds (move distance, rotation degrees)
- Altitude cap
- Connection watchdog
"""

import logging
from dataclasses import dataclass
from typing import Optional

from config import SAFETY, MOVEMENT
from core.state import DroneState

logger = logging.getLogger(__name__)


@dataclass
class SafetyCheckResult:
    """Result of a safety check."""
    allowed: bool
    reason: Optional[str] = None
    modified_params: Optional[dict] = None  # If we clamped values


class SafetySystem:
    """Validates all drone actions before execution."""

    def __init__(self, state: DroneState):
        self.state = state
        self._force_land_requested = False

    # ── Pre-flight checks ──

    def check_move(self, direction: str, cm: int) -> SafetyCheckResult:
        """Validate a move command."""
        # Direction valid?
        if direction not in MOVEMENT["valid_directions"]:
            return SafetyCheckResult(
                allowed=False,
                reason=f"Invalid direction '{direction}'. Valid: {MOVEMENT['valid_directions']}",
            )

        # Clamp distance
        original_cm = cm
        cm = max(SAFETY["min_move_cm"], min(SAFETY["max_move_cm"], cm))

        # Altitude check for up/down
        if direction == "up":
            projected_alt = self.state.position.z + cm
            if projected_alt > SAFETY["max_altitude_cm"]:
                cm = max(0, SAFETY["max_altitude_cm"] - int(self.state.position.z))
                if cm < SAFETY["min_move_cm"]:
                    return SafetyCheckResult(
                        allowed=False,
                        reason=f"Already at max altitude ({SAFETY['max_altitude_cm']}cm). Cannot go higher.",
                    )

        if direction == "down":
            projected_alt = self.state.position.z - cm
            if projected_alt < 20:  # Don't go below ~20cm
                cm = max(0, int(self.state.position.z) - 20)
                if cm < SAFETY["min_move_cm"]:
                    return SafetyCheckResult(
                        allowed=False,
                        reason="Already at minimum safe altitude.",
                    )

        # Geofence check — estimate where we'd end up
        import math
        heading_rad = math.radians(self.state.heading_deg)
        proj_x, proj_y = self.state.position.x, self.state.position.y

        if direction == "forward":
            proj_x += cm * math.sin(heading_rad)
            proj_y += cm * math.cos(heading_rad)
        elif direction == "back":
            proj_x -= cm * math.sin(heading_rad)
            proj_y -= cm * math.cos(heading_rad)
        elif direction == "left":
            proj_x -= cm * math.cos(heading_rad)
            proj_y += cm * math.sin(heading_rad)
        elif direction == "right":
            proj_x += cm * math.cos(heading_rad)
            proj_y -= cm * math.sin(heading_rad)

        projected_dist = math.sqrt(proj_x**2 + proj_y**2)
        if projected_dist > SAFETY["geofence_radius_cm"]:
            return SafetyCheckResult(
                allowed=False,
                reason=f"Move would exceed geofence ({SAFETY['geofence_radius_cm']}cm from home). "
                       f"Current distance: {self.state.position.distance_from_origin():.0f}cm, "
                       f"projected: {projected_dist:.0f}cm.",
            )

        # Battery check
        battery_result = self.check_battery()
        if not battery_result.allowed:
            return battery_result

        modified = {"direction": direction, "cm": cm} if cm != original_cm else None
        return SafetyCheckResult(allowed=True, modified_params=modified)

    def check_rotate(self, degrees: int) -> SafetyCheckResult:
        """Validate a rotation command."""
        if abs(degrees) > SAFETY["max_rotate_deg"]:
            degrees = max(-SAFETY["max_rotate_deg"], min(SAFETY["max_rotate_deg"], degrees))
            return SafetyCheckResult(
                allowed=True,
                modified_params={"degrees": degrees},
            )

        battery_result = self.check_battery()
        if not battery_result.allowed:
            return battery_result

        return SafetyCheckResult(allowed=True)

    def check_takeoff(self) -> SafetyCheckResult:
        """Validate takeoff."""
        if self.state.is_flying:
            return SafetyCheckResult(allowed=False, reason="Already flying.")

        battery_result = self.check_battery()
        if not battery_result.allowed:
            return battery_result

        return SafetyCheckResult(allowed=True)

    def check_flip(self, direction: str) -> SafetyCheckResult:
        """Validate flip — needs >50% battery."""
        if direction not in MOVEMENT["valid_flip_directions"]:
            return SafetyCheckResult(
                allowed=False,
                reason=f"Invalid flip direction '{direction}'.",
            )
        if self.state.battery_pct < 50:
            return SafetyCheckResult(
                allowed=False,
                reason=f"Flip requires >50% battery. Current: {self.state.battery_pct}%.",
            )
        return SafetyCheckResult(allowed=True)

    def check_battery(self) -> SafetyCheckResult:
        """Check battery thresholds."""
        if self.state.battery_pct <= SAFETY["battery_critical_pct"]:
            self._force_land_requested = True
            return SafetyCheckResult(
                allowed=False,
                reason=f"CRITICAL: Battery at {self.state.battery_pct}%. Force landing NOW.",
            )
        if self.state.battery_pct <= SAFETY["battery_warn_pct"]:
            logger.warning(f"Battery low: {self.state.battery_pct}%")
            # Allow action but log warning — agent should decide to land
            return SafetyCheckResult(allowed=True, reason=f"WARNING: Battery low at {self.state.battery_pct}%.")
        return SafetyCheckResult(allowed=True)

    def check_set_speed(self, speed: int) -> SafetyCheckResult:
        """Validate speed setting."""
        if speed < 10 or speed > 100:
            speed = max(10, min(100, speed))
            return SafetyCheckResult(allowed=True, modified_params={"speed": speed})
        return SafetyCheckResult(allowed=True)

    @property
    def should_force_land(self) -> bool:
        return self._force_land_requested

    def get_status_summary(self) -> str:
        """Human-readable safety status for LLM context."""
        dist = self.state.position.distance_from_origin()
        return (
            f"Battery: {self.state.battery_pct}% | "
            f"Distance from home: {dist:.0f}/{SAFETY['geofence_radius_cm']}cm | "
            f"Altitude: {self.state.position.z:.0f}/{SAFETY['max_altitude_cm']}cm | "
            f"Flying: {self.state.is_flying}"
        )
