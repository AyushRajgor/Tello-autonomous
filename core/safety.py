"""
Safety System

Validates every action before execution. Non-negotiable checks:
- Drone must be airborne for motion commands
- Battery thresholds (warn / force land)
- Parameter bounds (move distance, rotation degrees, speed)
- Max single move (enforces "look before you move" in code, not just the prompt)
- Altitude cap and floor
- Geofence (max distance from takeoff)

Continuous checks (battery drain, link loss, keepalive) live in core/watchdog.py.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from config import SAFETY, MOVEMENT
from core.state import DroneState, project_move

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

    # ── Pre-flight checks ──

    def _require_flying(self) -> Optional[SafetyCheckResult]:
        if not self.state.is_flying:
            return SafetyCheckResult(
                allowed=False,
                reason="Drone is not flying. Call takeoff first.",
            )
        return None

    def check_move(self, direction: str, cm: int) -> SafetyCheckResult:
        """Validate a move command."""
        if direction not in MOVEMENT["valid_directions"]:
            return SafetyCheckResult(
                allowed=False,
                reason=f"Invalid direction '{direction}'. Valid: {MOVEMENT['valid_directions']}",
            )

        not_flying = self._require_flying()
        if not_flying:
            return not_flying

        battery_result = self.check_battery()
        if not battery_result.allowed:
            return battery_result

        # Clamp distance. The upper bound is the single-move cap, not the
        # Tello's 500cm SDK maximum: the drone has no depth sensor, so long
        # blind moves are the main crash risk.
        original_cm = cm
        cm = max(SAFETY["min_move_cm"], min(SAFETY["max_single_move_cm"], int(cm)))

        # Altitude ceiling / floor for up/down
        if direction == "up":
            headroom = SAFETY["max_altitude_cm"] - int(self.state.position.z)
            if cm > headroom:
                cm = headroom
                if cm < SAFETY["min_move_cm"]:
                    return SafetyCheckResult(
                        allowed=False,
                        reason=f"Already at max altitude ({SAFETY['max_altitude_cm']}cm). Cannot go higher.",
                    )

        if direction == "down":
            room = int(self.state.position.z) - SAFETY["min_safe_altitude_cm"]
            if cm > room:
                cm = room
                if cm < SAFETY["min_move_cm"]:
                    return SafetyCheckResult(
                        allowed=False,
                        reason="Already at minimum safe altitude.",
                    )

        # Geofence check — project where we'd end up
        dx, dy, _ = project_move(direction, cm, self.state.heading_deg)
        proj_x = self.state.position.x + dx
        proj_y = self.state.position.y + dy
        projected_dist = (proj_x**2 + proj_y**2) ** 0.5
        if projected_dist > SAFETY["geofence_radius_cm"]:
            return SafetyCheckResult(
                allowed=False,
                reason=f"Move would exceed geofence ({SAFETY['geofence_radius_cm']}cm from home). "
                       f"Current distance: {self.state.position.distance_from_origin():.0f}cm, "
                       f"projected: {projected_dist:.0f}cm.",
            )

        modified = {"direction": direction, "cm": cm} if cm != original_cm else None
        return SafetyCheckResult(allowed=True, reason=battery_result.reason, modified_params=modified)

    def check_rotate(self, degrees: int) -> SafetyCheckResult:
        """Validate a rotation command."""
        not_flying = self._require_flying()
        if not_flying:
            return not_flying

        battery_result = self.check_battery()
        if not battery_result.allowed:
            return battery_result

        limit = SAFETY["max_rotate_deg"]
        if abs(degrees) > limit:
            clamped = max(-limit, min(limit, int(degrees)))
            return SafetyCheckResult(
                allowed=True,
                reason=battery_result.reason,
                modified_params={"degrees": clamped},
            )

        return SafetyCheckResult(allowed=True, reason=battery_result.reason)

    def check_takeoff(self) -> SafetyCheckResult:
        """Validate takeoff."""
        if self.state.is_flying:
            return SafetyCheckResult(allowed=False, reason="Already flying.")

        battery_result = self.check_battery()
        if not battery_result.allowed:
            return battery_result
        if self.state.battery_pct <= SAFETY["battery_warn_pct"]:
            return SafetyCheckResult(
                allowed=False,
                reason=f"Battery too low to take off ({self.state.battery_pct}%). Charge or swap the battery.",
            )

        return SafetyCheckResult(allowed=True)

    def check_flip(self, direction: str) -> SafetyCheckResult:
        """Validate flip — airborne and healthy battery."""
        if direction not in MOVEMENT["valid_flip_directions"]:
            return SafetyCheckResult(
                allowed=False,
                reason=f"Invalid flip direction '{direction}'.",
            )
        not_flying = self._require_flying()
        if not_flying:
            return not_flying
        min_batt = SAFETY["flip_min_battery_pct"]
        if self.state.battery_pct <= min_batt:
            return SafetyCheckResult(
                allowed=False,
                reason=f"Flip requires >{min_batt}% battery. Current: {self.state.battery_pct}%.",
            )
        return SafetyCheckResult(allowed=True)

    def check_battery(self) -> SafetyCheckResult:
        """Check battery thresholds."""
        if self.state.battery_pct <= SAFETY["battery_critical_pct"]:
            return SafetyCheckResult(
                allowed=False,
                reason=f"CRITICAL: Battery at {self.state.battery_pct}%. Force landing NOW.",
            )
        if self.state.battery_pct <= SAFETY["battery_warn_pct"]:
            logger.warning(f"Battery low: {self.state.battery_pct}%")
            # Allow action but surface a warning — agent should decide to land
            return SafetyCheckResult(allowed=True, reason=f"WARNING: Battery low at {self.state.battery_pct}%.")
        return SafetyCheckResult(allowed=True)

    def check_set_speed(self, speed: int) -> SafetyCheckResult:
        """Validate speed setting."""
        if speed < 10 or speed > 100:
            speed = max(10, min(100, int(speed)))
            return SafetyCheckResult(allowed=True, modified_params={"speed": speed})
        return SafetyCheckResult(allowed=True)

    @property
    def should_force_land(self) -> bool:
        """True while airborne with a critical battery."""
        return self.state.is_flying and self.state.battery_pct <= SAFETY["battery_critical_pct"]

    def get_status_summary(self) -> str:
        """Human-readable safety status for LLM context."""
        dist = self.state.position.distance_from_origin()
        summary = (
            f"Battery: {self.state.battery_pct}% | "
            f"Distance from home: {dist:.0f}/{SAFETY['geofence_radius_cm']}cm | "
            f"Altitude: {self.state.position.z:.0f}/{SAFETY['max_altitude_cm']}cm | "
            f"Heading: {self.state.heading_deg:.0f}° | "
            f"Flying: {self.state.is_flying}"
        )
        if self.state.operator_hold:
            summary += " | OPERATOR HOLD: a human stopped the drone; do not move until the user asks again"
        if not self.state.link_ok:
            summary += " | LINK LOST"
        return summary
