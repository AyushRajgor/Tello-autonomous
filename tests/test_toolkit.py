"""
Tests for TelloToolkit + SafetySystem

Run: python -m pytest tests/ -v
"""

import pytest
import asyncio
from core.toolkit import TelloToolkit
from core.safety import SafetySystem
from core.state import DroneState


# ── Fixtures ──

@pytest.fixture
def toolkit():
    """Simulated toolkit for testing."""
    tk = TelloToolkit(tello=None, simulate=True)
    tk.connect()
    return tk


@pytest.fixture
def flying_toolkit(toolkit):
    """Toolkit with drone already airborne."""
    toolkit.takeoff()
    return toolkit


# ── Toolkit Tests ──

class TestToolkit:
    def test_connect(self, toolkit):
        assert toolkit.state.battery_pct == 100
        assert not toolkit.state.is_flying

    def test_takeoff(self, toolkit):
        result = toolkit.takeoff()
        assert result["success"]
        assert toolkit.state.is_flying
        assert toolkit.state.position.z == 80  # Tello hovers at ~80cm

    def test_double_takeoff_rejected(self, flying_toolkit):
        result = flying_toolkit.takeoff()
        assert not result["success"]
        assert "Already flying" in result["error"]

    def test_land(self, flying_toolkit):
        result = flying_toolkit.land()
        assert result["success"]
        assert not flying_toolkit.state.is_flying

    def test_move_forward(self, flying_toolkit):
        result = flying_toolkit.move("forward", 100)
        assert result["success"]
        assert flying_toolkit.state.position.y == pytest.approx(100, abs=1)

    def test_move_invalid_direction(self, flying_toolkit):
        result = flying_toolkit.move("diagonal", 100)
        assert not result["success"]

    def test_rotate(self, flying_toolkit):
        result = flying_toolkit.rotate(90)
        assert result["success"]
        assert flying_toolkit.state.heading_deg == 90

    def test_rotate_negative(self, flying_toolkit):
        result = flying_toolkit.rotate(-90)
        assert result["success"]
        assert flying_toolkit.state.heading_deg == 270  # 0 - 90 = 270

    def test_get_frame_simulated(self, flying_toolkit):
        result = flying_toolkit.get_frame()
        assert result["success"]
        assert "image_base64" in result

    def test_get_battery(self, toolkit):
        result = toolkit.get_battery()
        assert result["success"]
        assert result["battery_pct"] == 100

    def test_set_speed(self, toolkit):
        result = toolkit.set_speed(50)
        assert result["success"]
        assert toolkit.state.speed_cms == 50

    def test_execute_dispatch(self, toolkit):
        result = toolkit.execute("get_battery", {})
        assert result["success"]

    def test_execute_unknown(self, toolkit):
        result = toolkit.execute("fly_to_moon", {})
        assert not result["success"]

    def test_emergency_stop(self, flying_toolkit):
        result = flying_toolkit.emergency_stop()
        assert result["success"]
        assert not flying_toolkit.state.is_flying


# ── Safety Tests ──

class TestSafety:
    def test_geofence_blocks(self, flying_toolkit):
        # Try to move beyond geofence (800cm)
        result = flying_toolkit.move("forward", 500)
        assert result["success"]  # 500 is within bounds
        result = flying_toolkit.move("forward", 500)
        assert not result["success"]  # 1000 total would exceed 800

    def test_altitude_cap(self, flying_toolkit):
        # Already at 80cm from takeoff, max is 200
        result = flying_toolkit.move("up", 200)
        # Should be clamped — only allowed to go up 120 more
        assert result["success"]
        assert flying_toolkit.state.position.z <= 200

    def test_move_distance_clamped(self, flying_toolkit):
        # Below minimum
        result = flying_toolkit.move("forward", 5)
        assert not result["success"] or flying_toolkit.state.position.y >= 20

    def test_battery_critical_blocks(self, flying_toolkit):
        flying_toolkit.state.battery_pct = 8
        result = flying_toolkit.move("forward", 50)
        assert not result["success"]
        assert "battery" in result["error"].lower() or "CRITICAL" in result["error"]

    def test_flip_needs_battery(self, flying_toolkit):
        flying_toolkit.state.battery_pct = 30
        result = flying_toolkit.flip("forward")
        assert not result["success"]
        assert "battery" in result["error"].lower()

    def test_param_validation(self, flying_toolkit):
        # Rotation beyond 360
        result = flying_toolkit.rotate(400)
        assert result["success"]  # Should be clamped to 360


# ── State Tests ──

class TestState:
    def test_position_tracking(self):
        state = DroneState()
        state.update_position_after_move("forward", 100)
        assert state.position.y == pytest.approx(100, abs=1)
        assert state.position.x == pytest.approx(0, abs=1)

    def test_heading_tracking(self):
        state = DroneState()
        state.update_heading_after_rotate(90)
        assert state.heading_deg == 90
        state.update_heading_after_rotate(300)
        assert state.heading_deg == 30  # (90+300) % 360

    def test_distance_from_origin(self):
        state = DroneState()
        state.position.x = 300
        state.position.y = 400
        assert state.position.distance_from_origin() == pytest.approx(500, abs=1)

    def test_move_with_heading(self):
        state = DroneState()
        state.heading_deg = 90  # Facing right
        state.update_position_after_move("forward", 100)
        assert state.position.x == pytest.approx(100, abs=1)
        assert state.position.y == pytest.approx(0, abs=1)

    def test_action_log(self):
        state = DroneState()
        state.log_action("takeoff", {}, True)
        state.log_action("move", {"direction": "forward", "cm": 100}, True)
        assert len(state.action_log) == 2

    def test_reset(self):
        state = DroneState()
        state.is_flying = True
        state.position.x = 500
        state.reset()
        assert not state.is_flying
        assert state.position.x == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
