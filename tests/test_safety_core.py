"""
Safety-core regression tests: operator controls, operator hold, enforced
limits, the background watchdog, and real-drone code paths (with a fake Tello).

Run: python -m pytest tests/test_safety_core.py -v
"""

import threading
import time

import pytest

import config
from config import OPERATOR_ONLY_ACTIONS, SAFETY, TOOL_SCHEMAS
from core.toolkit import LLM_TOOLS, TelloToolkit, parse_operator_text
from core.watchdog import SafetyWatchdog


@pytest.fixture
def toolkit():
    tk = TelloToolkit(tello=None, simulate=True)
    tk.connect()
    return tk


@pytest.fixture
def flying(toolkit):
    toolkit.takeoff()
    return toolkit


class FakeTello:
    """Records commands; mimics the djitellopy 2.5 surface the toolkit uses."""

    def __init__(self, yaw_sign: int = 1):
        self.calls = []
        self.yaw = 30.0       # arbitrary power-on yaw
        self.yaw_sign = yaw_sign  # +1: yaw grows clockwise, -1: the opposite
        self.height = 0
        self.battery = 90
        self.udp = {"responses": [], "state": {"bat": 90}}

    def _rec(self, *call):
        self.calls.append(call)

    # djitellopy internals used for link-loss detection and reply hygiene
    def get_own_udp_object(self): return self.udp
    def get_current_state(self): return self.udp["state"]
    def new_state_packet(self): self.udp["state"] = {"bat": self.battery}

    def send_control_command(self, command, timeout=7):
        self._rec("cmd", command, timeout)
        verb, _, arg = command.partition(" ")
        n = int(arg) if arg else 0
        if verb == "cw":
            self.yaw += self.yaw_sign * n
        elif verb == "ccw":
            self.yaw -= self.yaw_sign * n
        elif verb == "up":
            self.height += n
        elif verb == "down":
            self.height -= n
        return True

    def connect(self): self._rec("connect")
    def get_battery(self): return self.battery
    def get_height(self): return self.height
    def get_yaw(self): return self.yaw
    def get_temperature(self): return 40
    def set_speed(self, s): self._rec("speed", s)
    def streamon(self): self._rec("streamon")
    def streamoff(self): self._rec("streamoff")

    def takeoff(self):
        self._rec("takeoff")
        self.height = 90

    def land(self):
        self._rec("land")
        self.height = 0

    def flip(self, d): self._rec("flip", d)
    def send_command_without_return(self, cmd): self._rec("raw", cmd)
    def send_rc_control(self, *a): self._rec("rc", *a)


@pytest.fixture
def real_toolkit():
    tk = TelloToolkit(tello=FakeTello(), simulate=False)
    assert tk.connect()
    return tk


# ── LLM tool surface ──

class TestToolSurface:
    def test_operator_only_actions_not_exposed_to_llm(self):
        names = {s["name"] for s in TOOL_SCHEMAS}
        assert not names & set(OPERATOR_ONLY_ACTIONS)
        assert LLM_TOOLS == names

    @pytest.mark.parametrize("name", ["emergency_stop", "flip", "hover"])
    def test_execute_refuses_operator_only(self, flying, name):
        result = flying.execute(name, {"direction": "forward"})
        assert not result["success"]
        assert flying.state.is_flying

    def test_every_llm_tool_is_dispatchable(self, flying):
        for name in LLM_TOOLS:
            result = flying.execute(name, {"direction": "up", "cm": 20, "degrees": 10, "speed": 30})
            assert "Unknown function" not in str(result.get("error", "")), name


# ── Enforced limits / bug regressions ──

class TestLimits:
    def test_move_rejected_when_landed(self, toolkit):
        result = toolkit.execute("move", {"direction": "forward", "cm": 100})
        assert not result["success"]
        assert toolkit.state.position.y == 0

    def test_rotate_rejected_when_landed(self, toolkit):
        assert not toolkit.rotate(90)["success"]

    def test_oversized_rotate_still_checks_battery(self, flying):
        flying.state.battery_pct = 5
        result = flying.rotate(720)
        assert not result["success"]
        assert "CRITICAL" in result["error"]

    def test_move_clamped_to_single_move_cap(self, flying):
        result = flying.move("forward", 400)
        assert result["success"]
        assert flying.state.position.y == pytest.approx(SAFETY["max_single_move_cm"])
        assert "note" in result

    def test_descend_stops_at_floor(self, flying):
        flying.move("down", 100)  # from 80cm: only 60cm of room above the 20cm floor
        assert flying.state.position.z == pytest.approx(SAFETY["min_safe_altitude_cm"])
        assert not flying.move("down", 20)["success"]

    def test_takeoff_refused_on_low_battery(self, toolkit):
        toolkit.state.battery_pct = SAFETY["battery_warn_pct"]
        assert not toolkit.takeoff()["success"]

    def test_takeoff_resets_origin(self, flying):
        flying.move("forward", 100)
        flying.rotate(90)
        flying.land()
        flying.takeoff()
        assert flying.state.position.distance_from_origin() == 0
        assert flying.state.heading_deg == 0

    def test_land_when_on_ground_is_noop(self, toolkit):
        result = toolkit.land()
        assert result["success"]
        assert "already" in result["message"].lower()

    def test_landing_clears_flight_timer(self, flying):
        flying.land()
        assert flying.state.flight_duration_s() is None

    def test_execute_force_lands_on_critical_battery(self, flying):
        flying.state.battery_pct = 9
        result = flying.execute("get_battery", {})
        assert not result["success"]
        assert not flying.state.is_flying


# ── Operator controls ──

class TestOperator:
    def test_hover_sets_hold_and_blocks_ai_motion(self, flying):
        assert flying.operator_command("hover")["success"]
        assert flying.operator_hold
        assert not flying.execute("move", {"direction": "forward", "cm": 50})["success"]
        assert not flying.execute("rotate", {"degrees": 90})["success"]
        # Looking and landing stay allowed.
        assert flying.execute("get_frame", {})["success"]
        assert flying.execute("land", {})["success"]

    def test_clear_hold_restores_ai_motion(self, flying):
        flying.operator_command("hover")
        flying.clear_operator_hold()
        assert flying.execute("move", {"direction": "forward", "cm": 50})["success"]

    def test_resume_action(self, flying):
        flying.operator_command("hover")
        flying.operator_command("resume")
        assert not flying.operator_hold

    def test_operator_land(self, flying):
        result = flying.operator_command("land")
        assert result["success"]
        assert not flying.state.is_flying
        assert flying.operator_hold

    def test_operator_manual_move_ignores_hold_but_not_safety(self, flying):
        flying.operator_command("hover")
        assert flying.operator_command("move", {"direction": "forward", "cm": 50})["success"]
        assert flying.operator_command("move", {"direction": "sideways", "cm": 50})["success"] is False

    def test_operator_flip_needs_battery(self, flying):
        flying.state.battery_pct = 40
        assert not flying.operator_command("flip", {"direction": "left"})["success"]
        flying.state.battery_pct = 90
        assert flying.operator_command("flip", {"direction": "left"})["success"]

    def test_operator_emergency_stop(self, flying):
        assert flying.operator_command("emergency_stop")["success"]
        assert not flying.state.is_flying

    def test_parse_operator_text(self):
        assert parse_operator_text(" Land ") == "land"
        assert parse_operator_text("stop") == "hover"
        assert parse_operator_text("!estop") == "emergency_stop"
        assert parse_operator_text("land on the table") is None
        assert parse_operator_text("emergency") is None  # must be the explicit !estop


# ── Watchdog ──

class TestWatchdog:
    def test_sim_battery_drains_while_flying(self, flying):
        wd = SafetyWatchdog(flying)
        wd.tick(now=0.0)
        wd.tick(now=60.0)
        assert flying.state.battery_pct == pytest.approx(100 - config.SIMULATION["battery_drain_pct_per_min"], abs=1)

    def test_sim_battery_does_not_drain_on_ground(self, toolkit):
        wd = SafetyWatchdog(toolkit)
        wd.tick(now=0.0)
        wd.tick(now=600.0)
        assert toolkit.state.battery_pct == 100

    def test_low_battery_warns_once(self, flying):
        events = []
        wd = SafetyWatchdog(flying)
        wd.add_listener(events.append)
        flying._sim_battery = 15
        wd.tick(now=0.0)
        wd.tick(now=0.1)
        assert [e["code"] for e in events] == ["battery_low"]
        assert flying.state.is_flying

    def test_critical_battery_force_lands(self, flying):
        events = []
        wd = SafetyWatchdog(flying)
        wd.add_listener(events.append)
        flying._sim_battery = 9
        wd.tick(now=0.0)
        wd._force_land_thread.join(2)  # force-land runs off the watchdog thread
        assert not flying.state.is_flying
        assert "battery_critical" in [e["code"] for e in events]

    def test_failing_force_land_is_rate_limited_and_alerts_once(self, flying):
        events, attempts = [], []
        wd = SafetyWatchdog(flying)
        wd.add_listener(events.append)
        flying.operator_command = lambda action, args=None, source="operator": (
            attempts.append(action) or {"success": False, "error": "error Not joystick"}
        )
        flying._sim_battery = 9
        for second in range(12):
            wd.tick(now=float(second))
            if wd._force_land_thread is not None:
                wd._force_land_thread.join(2)
        codes = [e["code"] for e in events]
        assert codes.count("battery_critical") == 1
        assert codes.count("force_land_failed") == 1
        # One attempt per force_land_retry_s (5s) over 12 seconds, not 12.
        assert len(attempts) == 3

    def test_watchdog_thread_not_blocked_by_slow_force_land(self, flying):
        release = threading.Event()
        flying.operator_command = lambda action, args=None, source="operator": (
            release.wait(5) and {"success": True}
        )
        wd = SafetyWatchdog(flying)
        flying._sim_battery = 9
        started = time.monotonic()
        wd.tick(now=0.0)
        wd.tick(now=1.0)  # second tick must not wait for (or duplicate) the landing
        assert time.monotonic() - started < 0.5
        release.set()
        wd._force_land_thread.join(2)

    def test_resyncs_when_sensor_says_airborne_but_state_says_landed(self, real_toolkit):
        events = []
        wd = SafetyWatchdog(real_toolkit)
        wd.add_listener(events.append)
        real_toolkit.tello.height = 85  # e.g. a takeoff whose reply was misread
        for second in range(3):
            wd.tick(now=float(second))
        assert real_toolkit.state.is_flying
        assert [e["code"] for e in events] == ["state_resync_airborne"]

    def test_resyncs_when_sensor_says_landed_but_state_says_flying(self, real_toolkit):
        events = []
        real_toolkit.takeoff()
        wd = SafetyWatchdog(real_toolkit)
        wd.add_listener(events.append)
        real_toolkit.tello.height = 0  # e.g. the Tello auto-landed after a link drop
        for second in range(3):
            wd.tick(now=float(second))
        assert not real_toolkit.state.is_flying
        assert [e["code"] for e in events] == ["state_resync_landed"]

    def test_brief_sensor_disagreement_is_ignored(self, real_toolkit):
        wd = SafetyWatchdog(real_toolkit)
        real_toolkit.tello.height = 85
        wd.tick(now=0.0)
        wd.tick(now=1.0)
        real_toolkit.tello.height = 0
        wd.tick(now=2.0)
        assert not real_toolkit.state.is_flying

    def test_keepalive_sent_when_idle(self, real_toolkit):
        real_toolkit.takeoff()
        real_toolkit._last_command_ts = time.time() - SAFETY["keepalive_interval_s"] - 1
        SafetyWatchdog(real_toolkit).tick(now=0.0)
        assert ("rc", 0, 0, 0, 0) in real_toolkit.tello.calls

    def test_no_keepalive_when_recently_active(self, real_toolkit):
        real_toolkit.takeoff()
        SafetyWatchdog(real_toolkit).tick(now=0.0)
        assert ("rc", 0, 0, 0, 0) not in real_toolkit.tello.calls

    def test_link_loss_and_restore(self, real_toolkit):
        events = []
        wd = SafetyWatchdog(real_toolkit)
        wd.add_listener(events.append)
        wd.tick(now=0.0)  # observes the current state packet
        assert real_toolkit.state.link_ok
        # Same state object for longer than the timeout => no packets arriving.
        real_toolkit._last_state_seen -= SAFETY["connection_timeout_s"] + 2
        wd.tick(now=1.0)
        assert not real_toolkit.state.link_ok
        real_toolkit.tello.new_state_packet()
        wd.tick(now=2.0)
        assert real_toolkit.state.link_ok
        assert [e["code"] for e in events] == ["link_lost", "link_restored"]

    def test_thread_lifecycle(self, toolkit):
        wd = SafetyWatchdog(toolkit, interval_s=0.01)
        wd.start()
        time.sleep(0.05)
        wd.stop()
        assert wd._thread is None


# ── Real-drone code paths (fake Tello) ──

class TestRealDronePaths:
    def test_connect_applies_default_speed(self, real_toolkit):
        assert ("speed", SAFETY["default_speed_cms"]) in real_toolkit.tello.calls

    def test_moves_use_motion_sized_timeout_without_retry_risk(self, real_toolkit):
        real_toolkit.takeoff()
        real_toolkit.set_speed(10)
        real_toolkit.move("forward", 100)
        cmd = [c for c in real_toolkit.tello.calls if c[0] == "cmd"][-1]
        assert cmd[1] == "forward 100"
        assert cmd[2] >= 100 / 10  # timeout covers the ~10s the move takes

    def test_stale_replies_drained_before_each_command(self, real_toolkit):
        real_toolkit.takeoff()
        real_toolkit.tello.udp["responses"].append(b"error Not joystick")
        real_toolkit.move("forward", 50)
        assert real_toolkit.tello.udp["responses"] == []

    def test_yaw_sign_calibrates_when_imu_counts_counter_clockwise(self):
        tk = TelloToolkit(tello=FakeTello(yaw_sign=-1), simulate=False)
        tk.connect()
        tk.takeoff()
        tk.rotate(90)
        assert tk._yaw_sign == -1
        assert tk.state.heading_deg == pytest.approx(90)
        tk.rotate(-45)
        assert tk.state.heading_deg == pytest.approx(45)

    def test_black_placeholder_frame_reports_not_ready(self, real_toolkit):
        import numpy as np

        class Reader:
            frame = np.zeros((300, 400, 3), dtype=np.uint8)

        real_toolkit._frame_reader = Reader()
        result = real_toolkit.get_frame()
        assert not result["success"]
        assert "not ready" in result["error"]

    def test_heading_synced_from_imu_yaw(self, real_toolkit):
        real_toolkit.takeoff()
        real_toolkit.rotate(90)
        real_toolkit.tello.yaw += 7  # IMU says we drifted further than commanded
        real_toolkit.move("forward", 50)
        assert real_toolkit.state.heading_deg == pytest.approx(97)

    def test_altitude_synced_from_height_sensor(self, real_toolkit):
        real_toolkit.takeoff()
        assert real_toolkit.state.position.z == 90
        real_toolkit.move("up", 30)
        assert real_toolkit.state.position.z == 120

    def test_land_unconfirmed_when_lock_never_frees(self, real_toolkit, monkeypatch):
        monkeypatch.setitem(SAFETY, "operator_land_lock_timeout_s", 0.05)
        real_toolkit.takeoff()
        release = threading.Event()

        def hold_lock():
            with real_toolkit._cmd_lock:
                release.wait(2)

        t = threading.Thread(target=hold_lock)
        t.start()
        time.sleep(0.01)
        result = real_toolkit.operator_command("land")
        release.set()
        t.join()
        # A raw land mid-command is usually rejected: never claim success.
        assert not result["success"]
        assert ("raw", "land") in real_toolkit.tello.calls
        assert real_toolkit.state.is_flying  # keepalives and exit-land stay active

    def test_land_uses_normal_path_when_idle(self, real_toolkit):
        real_toolkit.takeoff()
        real_toolkit.operator_command("land")
        assert ("land",) in real_toolkit.tello.calls
        assert ("raw", "stop") not in real_toolkit.tello.calls

    def test_hover_when_idle_sends_nothing_but_sets_hold(self, real_toolkit):
        real_toolkit.takeoff()
        result = real_toolkit.operator_command("hover")
        assert result["success"]
        assert real_toolkit.operator_hold
        assert not [c for c in real_toolkit.tello.calls if c[0] == "raw"]

    def test_takeoff_error_but_airborne_is_treated_as_flying(self):
        class FlakyTakeoff(FakeTello):
            def takeoff(self):
                self.height = 90
                raise Exception("Aborting command 'takeoff'. Did not receive a response")

        tk = TelloToolkit(tello=FlakyTakeoff(), simulate=False)
        tk.connect()
        result = tk.takeoff()
        assert not result["success"]
        assert "airborne" in result["error"]
        assert tk.state.is_flying

    def test_land_ok_without_sensor_confirmation_is_not_believed(self, real_toolkit, monkeypatch):
        monkeypatch.setitem(SAFETY, "land_confirm_s", 0.2)
        real_toolkit.takeoff()
        real_toolkit.tello.land = lambda: True  # "ok" — but the drone never descends
        result = real_toolkit.land()
        assert not result["success"]
        assert "still reads airborne" in result["error"]
        assert real_toolkit.state.is_flying

    def test_estop_during_takeoff_means_not_flying(self, real_toolkit):
        def takeoff_then_estop():
            real_toolkit.tello.height = 40           # mid-climb
            real_toolkit.operator_command("emergency_stop")
            raise Exception("Command 'takeoff' was unsuccessful. Latest response: 'ok'")

        real_toolkit.tello.takeoff = takeoff_then_estop
        result = real_toolkit.takeoff()
        assert not result["success"]
        assert "E-STOP" in result["error"]
        assert not real_toolkit.state.is_flying

    @pytest.mark.parametrize("timeline, expect_flying", [
        ([5, 30, 80, 85], True),    # interrupted reply, but the drone kept climbing
        ([40, 20, 5, 0], False),    # interrupted and it came back down
    ])
    def test_interrupted_takeoff_uses_settled_height(self, real_toolkit, monkeypatch, timeline, expect_flying):
        monkeypatch.setitem(SAFETY, "sensor_settle_s", 0.35)
        readings = iter(timeline + [timeline[-1]] * 20)
        real_toolkit.tello.get_height = lambda: next(readings)

        def failing_takeoff():
            raise Exception("Aborting command 'takeoff'. Did not receive a response")

        real_toolkit.tello.takeoff = failing_takeoff
        real_toolkit.takeoff()
        assert real_toolkit.state.is_flying is expect_flying

    def test_land_retries_once_on_lost_reply(self, real_toolkit):
        attempts = []

        def flaky_land():
            attempts.append(1)
            if len(attempts) == 1:
                raise Exception("Aborting command 'land'. Did not receive a response after 7 seconds")
            real_toolkit.tello.height = 0

        real_toolkit.takeoff()
        real_toolkit.tello.land = flaky_land
        assert real_toolkit.land()["success"]
        assert len(attempts) == 2

    def test_land_refused_but_on_ground_by_sensor_counts_as_landed(self, real_toolkit):
        real_toolkit.takeoff()

        def refused_land():
            real_toolkit.tello.height = 0
            raise Exception("Command 'land' was unsuccessful. Latest response: 'error'")

        real_toolkit.tello.land = refused_land
        assert real_toolkit.land()["success"]
        assert not real_toolkit.state.is_flying

    def test_background_operator_command_returns_immediately(self, real_toolkit):
        real_toolkit.takeoff()
        gate = threading.Event()
        done = []
        def slow_land():
            gate.wait(2)
            real_toolkit.tello.height = 0

        real_toolkit.tello.land = slow_land
        thread = real_toolkit.operator_command_background("land", on_done=done.append)
        assert thread.is_alive()  # caller isn't blocked while the drone lands
        gate.set()
        thread.join(2)
        assert done and done[0]["success"]


# ── Concurrency against djitellopy's unmatched reply queue ──

MOTION_VERBS = {"forward", "back", "left", "right", "up", "down", "cw", "ccw"}


class ReplyQueueTello(FakeTello):
    """
    Models the hazard the review found: djitellopy pops replies from one shared
    queue in arrival order without matching them to commands. Each reply is
    tagged with the command that produced it so tests can check who consumed it.
    Moves take `motion_s` of real time; `stop` aborts them only if supported.
    """

    def __init__(self, stop_supported=True, motion_s=0.3):
        super().__init__()
        self.stop_supported = stop_supported
        self.motion_s = motion_s
        self.consumed = []        # (command, source of the reply it consumed)
        self._motion_end = None
        self._motion_cmd = None

    def _post(self, source, text, delay=0.01):
        threading.Timer(delay, lambda: self.udp["responses"].append((source, text))).start()

    def _moving(self):
        return self._motion_end is not None and not self._motion_end.is_set()

    # State-packet velocity (no command sent): non-zero only while moving.
    def get_speed_x(self): return 3 if self._moving() else 0
    def get_speed_y(self): return 0
    def get_speed_z(self): return 0

    def send_control_command(self, command, timeout=7):
        self._rec("cmd", command, timeout)
        verb = command.split()[0]
        if verb in MOTION_VERBS:
            end = threading.Event()
            self._motion_end, self._motion_cmd = end, command

            def finish():
                if not end.is_set():
                    end.set()
                    self.udp["responses"].append((command, "ok"))

            threading.Timer(self.motion_s, finish).start()
        elif verb == "land":
            if self._moving():
                self._post("land", "error Not joystick")
            else:
                self.height = 0
                self._post("land", "ok")
        else:
            self._post(command, "ok")

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.udp["responses"]:
                source, text = self.udp["responses"].pop(0)
                self.consumed.append((command, source))
                if "ok" in text:
                    return True
                raise Exception(f"Command '{command}' was unsuccessful. Latest response: '{text}'")
            time.sleep(0.002)
        raise Exception(f"Aborting command '{command}'. Did not receive a response after {timeout} seconds")

    def takeoff(self):
        self.height = 90
        return self.send_control_command("takeoff")

    def land(self):
        return self.send_control_command("land")

    def send_command_without_return(self, command):
        self._rec("raw", command)
        if command == "stop":
            if self.stop_supported:
                if self._moving():
                    self._motion_end.set()
                    self._post(self._motion_cmd, "ok", delay=0.05)  # the aborted move's own late reply
                self._post("stop", "ok")
            else:
                self._post("stop", "unknown command")
        elif command == "land":
            self._post("raw-land", "error Not joystick" if self._moving() else "ok")
        elif command == "emergency":
            self._post("emergency", "ok")


@pytest.fixture
def queue_toolkit(monkeypatch):
    monkeypatch.setitem(SAFETY, "abort_settle_s", 0.1)

    def make(**kwargs):
        tk = TelloToolkit(tello=ReplyQueueTello(**kwargs), simulate=False)
        assert tk.connect()
        assert tk.takeoff()["success"]
        tk.state.speed_cms = 100  # 50cm moves: 0.5s expected
        return tk

    return make


def _in_thread(fn, *args):
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("result", fn(*args)))
    t.start()
    return t, out


class TestReplyQueueConcurrency:
    @pytest.mark.parametrize("stop_supported", [True, False])
    def test_operator_land_mid_move_only_consumes_its_own_reply(self, queue_toolkit, stop_supported):
        tk = queue_toolkit(stop_supported=stop_supported)
        t, out = _in_thread(tk.execute, "move", {"direction": "forward", "cm": 50})
        time.sleep(0.05)
        result = tk.operator_command("land")
        t.join(5)

        assert result["success"], result
        assert not tk.state.is_flying
        land_replies = [src for cmd, src in tk.tello.consumed if cmd == "land"]
        assert land_replies == ["land"]  # never the stop's or the move's stray reply
        assert out["result"]["success"] is False  # the move is reported as interrupted

    def test_stop_cancels_commands_queued_behind_a_move(self, queue_toolkit):
        tk = queue_toolkit()
        t1, out1 = _in_thread(tk.execute, "move", {"direction": "forward", "cm": 50})
        time.sleep(0.03)
        t2, out2 = _in_thread(tk.execute, "rotate", {"degrees": 90})
        time.sleep(0.03)
        assert tk.operator_command("hover")["success"]
        t1.join(5)
        t2.join(5)

        assert "Cancelled" in out2["result"]["error"] or "Operator hold" in out2["result"]["error"]
        assert not [c for c in tk.tello.calls if c[0] == "cmd" and c[1].startswith("cw")]

    def test_concurrent_moves_cannot_breach_geofence(self, queue_toolkit):
        tk = queue_toolkit(motion_s=0.1)
        tk.state.position.y = 720
        threads = [_in_thread(tk.execute, "move", {"direction": "forward", "cm": 50}) for _ in range(3)]
        for t, _ in threads:
            t.join(5)

        assert sum(out["result"]["success"] for _, out in threads) == 1
        assert tk.state.position.y == pytest.approx(770)

    def test_hover_aborts_a_move_in_flight_without_waiting(self, queue_toolkit):
        tk = queue_toolkit(motion_s=1.0)
        t, _ = _in_thread(tk.execute, "move", {"direction": "forward", "cm": 50})
        time.sleep(0.05)
        started = time.monotonic()
        assert tk.operator_command("hover")["success"]
        assert time.monotonic() - started < 0.2
        assert ("raw", "stop") in tk.tello.calls
        t.join(5)

    def test_stop_during_settle_wait_cancels_the_waiting_command(self, queue_toolkit):
        # Review finding: a command already holding the lock and waiting out a
        # previous stop's stray replies used to fly after a second STOP.
        tk = queue_toolkit(stop_supported=False, motion_s=0.6)  # drone keeps moving: full settle
        t1, _ = _in_thread(tk.execute, "move", {"direction": "forward", "cm": 50})
        time.sleep(0.05)
        tk.operator_command("hover")
        t1.join(5)
        tk.operator_command("resume")
        t2, out2 = _in_thread(tk.execute, "move", {"direction": "forward", "cm": 50})
        time.sleep(0.1)  # t2 now holds the lock, sleeping through the settle
        tk.operator_command("hover")
        t2.join(5)

        assert "Cancelled" in out2["result"]["error"]
        assert len([c for c in tk.tello.calls if c[0] == "cmd" and c[1].startswith("forward")]) == 1

    def test_settle_ends_early_once_the_drone_is_stationary(self, queue_toolkit):
        tk = queue_toolkit(stop_supported=True, motion_s=10)
        t, _ = _in_thread(tk.execute, "rotate", {"degrees": 360})  # expected ~12s
        time.sleep(0.05)
        started = time.monotonic()
        result = tk.operator_command("land")
        t.join(5)
        assert result["success"], result
        # `stop` halted the turn; land must not wait out the 12s estimate.
        assert time.monotonic() - started < 3

    def test_repeated_land_presses_are_single_flight(self, queue_toolkit):
        tk = queue_toolkit(stop_supported=False, motion_s=0.6)
        t, _ = _in_thread(tk.execute, "move", {"direction": "forward", "cm": 50})
        time.sleep(0.05)
        first, out1 = _in_thread(tk.operator_command, "land")
        time.sleep(0.05)
        started = time.monotonic()
        second = tk.operator_command("land")
        assert time.monotonic() - started < 0.2
        assert second.get("in_progress") and not second["success"]
        first.join(10)
        t.join(5)
        assert out1["result"]["success"], out1["result"]
        # E-STOP is never queued behind lands: it doesn't touch the land gate or lock.
        started = time.monotonic()
        assert tk.operator_command("emergency_stop")["success"]
        assert time.monotonic() - started < 0.1

    def test_manual_motion_is_single_flight(self, queue_toolkit):
        tk = queue_toolkit(motion_s=0.3)
        t, out = _in_thread(tk.operator_command, "move", {"direction": "forward", "cm": 50})
        time.sleep(0.05)
        second = tk.operator_command("move", {"direction": "forward", "cm": 50})
        t.join(5)
        assert out["result"]["success"]
        assert not second["success"] and "busy" in second["error"]
        assert len([c for c in tk.tello.calls if c[0] == "cmd" and c[1].startswith("forward")]) == 1
