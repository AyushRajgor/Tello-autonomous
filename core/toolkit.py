"""
TelloToolkit

Wraps djitellopy with safety validation. Every function:
1. Validates via SafetySystem
2. Executes via djitellopy (serialized through one command lock)
3. Updates DroneState (re-synced from the drone's sensors on real hardware)
4. Returns a {"success": bool, ...} result dict

Two entry points:
- execute(name, args)            — what LLMs call. Only TOOL_SCHEMAS actions.
- operator_command(action, args) — what humans call (dashboard buttons, CLI,
                                   watchdog). Includes stop/hover, land,
                                   emergency stop and flip. Never exposed to LLMs.
"""

import base64
import logging
import threading
import time
from typing import Optional

import cv2
import numpy as np

from config import OPERATOR_ONLY_ACTIONS, SAFETY, SIMULATION, TOOL_SCHEMAS, VISION
from core.state import DroneState
from core.safety import SafetySystem

logger = logging.getLogger(__name__)

# Tools an LLM may call. Derived from the schemas so the two can't drift.
LLM_TOOLS = frozenset(schema["name"] for schema in TOOL_SCHEMAS)
# LLM tools that move the drone — refused while an operator hold is active.
MOTION_TOOLS = frozenset({"takeoff", "move", "rotate", "set_speed"})
# Operator actions that stop the drone (set the hold, cancel queued commands).
STOP_ACTIONS = frozenset({"hover", "land", "emergency_stop"})
# Operator actions that fly the drone (single-flight, still safety-checked).
MANUAL_ACTIONS = frozenset({"takeoff", "move", "rotate", "flip"})

assert not LLM_TOOLS & set(OPERATOR_ONLY_ACTIONS), "operator-only action leaked into TOOL_SCHEMAS"

# Typed commands handled locally (no LLM round-trip) in the text/voice CLIs.
OPERATOR_TEXT_COMMANDS = {
    "stop": "hover", "hover": "hover", "!stop": "hover",
    "land": "land", "!land": "land",
    "!estop": "emergency_stop",
    "resume": "resume", "!resume": "resume",
}


class OperatorStopped(Exception):
    """A queued command was cancelled because an operator stopped the drone."""


def parse_operator_text(text: str) -> Optional[str]:
    """Map an exact typed operator command (e.g. 'land', '!estop') to an action."""
    return OPERATOR_TEXT_COMMANDS.get(text.strip().lower())


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
    """All drone actions exposed as callable functions."""

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
        self._webcam_lock = threading.Lock()

        # One drone command at a time. The Tello answers commands over a single
        # UDP socket and djitellopy pops replies in arrival order without
        # matching them to commands, so concurrent senders (agent thread,
        # keepalive, dashboard) would steal each other's replies. Re-entrant so
        # a priority land can hold it across stop -> land with nothing slipping in.
        self._cmd_lock = threading.RLock()
        self._last_command_ts = time.time()

        # Bumped by every operator stop/land/E-STOP: commands that were already
        # waiting for the lock when it changed are cancelled instead of flown.
        self._stop_epoch = 0
        self._epoch_lock = threading.Lock()
        # Monotonic deadline of the motion command in flight (0 = none).
        self._motion_deadline = 0.0
        # The next command waits until this monotonic time (see _send_immediate).
        self._settle_until = 0.0
        self._last_abort_ts = 0.0
        self._abort_count = 0
        self._emergency_count = 0
        self._last_yaw_sample: Optional[float] = None
        # Only one priority land at a time (repeated Land presses / watchdog).
        self._land_gate = threading.Lock()
        # Manual (dashboard pad / CLI) motion is single-flight: rapid clicks are
        # refused instead of queueing up moves that would fly after a STOP.
        self._manual_gate = threading.Lock()
        self._yaw_offset: Optional[float] = None  # IMU yaw at takeoff (real drone)
        # Whether IMU yaw grows clockwise (+1) or counter-clockwise (-1). The
        # Tello docs don't say; it's calibrated from the first real rotation.
        self._yaw_sign = 1
        self._yaw_sign_calibrated = False
        self._sim_battery = 100.0

        # Link-loss detection: djitellopy replaces the state dict object on
        # every state packet, so a new identity means a fresh packet arrived.
        self._last_state_obj = None
        self._last_state_seen = time.monotonic()

    # ── Low-level drone I/O ──

    def _drain_responses(self):
        """Drop stale replies (e.g. from a raw stop/land) so they can't be
        mistaken for the answer to the next command."""
        try:
            self.tello.get_own_udp_object()["responses"].clear()
        except Exception:
            pass

    def _check_epoch(self, epoch: Optional[int]):
        if epoch is not None and epoch != self._stop_epoch:
            raise OperatorStopped("Cancelled: the operator stopped the drone while this command was waiting.")

    def _bump_stop_epoch(self):
        with self._epoch_lock:
            self._stop_epoch += 1

    @property
    def stop_epoch(self) -> int:
        """Increments on every stop/land/E-STOP; compare to detect a stop since some moment."""
        return self._stop_epoch

    def _prepare_command(self, epoch: Optional[int]):
        """
        Call with _cmd_lock held, right before sending. Cancels the command if
        an operator stop happened since it was requested, waits out the replies
        of any interrupted command, then discards stale replies.

        The wait stays cancellable (a STOP during it cancels this command too),
        keeps the Tello's 15s no-command timer fed, and ends early once the
        drone is visibly stationary — e.g. `stop` already halted a long turn.
        """
        self._check_epoch(epoch)
        if self.simulate:
            return
        calm_since: Optional[float] = None
        self._last_yaw_sample = None
        while True:
            now = time.monotonic()
            if now >= self._settle_until:
                break
            self._check_epoch(epoch)
            if self.seconds_since_last_command() >= SAFETY["keepalive_interval_s"]:
                self._send_rc_keepalive()
            if now - self._last_abort_ts >= SAFETY["abort_settle_s"] and self._drone_stationary():
                calm_since = calm_since or now
                if now - calm_since >= SAFETY["stationary_confirm_s"]:
                    break
            else:
                calm_since = None
            time.sleep(0.05)
        self._check_epoch(epoch)
        self._drain_responses()

    def _send_rc_keepalive(self):
        try:
            # `rc 0 0 0 0` gets no reply, so it can't pollute the reply queue.
            self.tello.send_rc_control(0, 0, 0, 0)
        except Exception as e:
            logger.debug(f"keepalive failed: {e}")
        self._last_command_ts = time.time()

    def _drone_stationary(self) -> bool:
        """True if the state packet shows no translation and a steady yaw."""
        if not self.state.link_ok:
            return False
        try:
            speeds = (self.tello.get_speed_x(), self.tello.get_speed_y(), self.tello.get_speed_z())
            yaw = float(self.tello.get_yaw())
        except Exception:
            return False
        previous, self._last_yaw_sample = self._last_yaw_sample, yaw
        yaw_steady = previous is not None and abs((yaw - previous + 180) % 360 - 180) < 1.0
        return yaw_steady and all(abs(int(s)) <= 1 for s in speeds)

    def _drone_call(self, fn, *args, epoch: Optional[int] = None):
        """Run a djitellopy command under the command lock."""
        with self._cmd_lock:
            self._prepare_command(epoch)
            try:
                return fn(*args)
            finally:
                self._last_command_ts = time.time()

    def _drone_call_retry(self, fn, *args, attempts: int = 3):
        """_drone_call with retries — ONLY for idempotent commands (SDK mode, stream, speed)."""
        last_error: Optional[Exception] = None
        for _ in range(attempts):
            try:
                return self._drone_call(fn, *args)
            except Exception as e:
                last_error = e
        raise last_error

    def _run_motion(self, fn, *args, expected_s: float, epoch: Optional[int] = None) -> tuple[Optional[str], bool]:
        """
        Run a motion command (takeoff/move/rotate/flip) and track it as in flight.

        Returns (error, interrupted): error is None on an "ok" reply; interrupted
        is True if an operator stop/land/E-STOP was sent while it ran — its reply
        may then belong to the stop, so the outcome is uncertain.
        Raises OperatorStopped if cancelled before sending.
        """
        with self._cmd_lock:
            self._prepare_command(epoch)
            aborts_before = self._abort_count
            self._motion_deadline = time.monotonic() + expected_s + 1.0
            try:
                fn(*args)
                error = None
            except Exception as e:
                error = str(e)
            finally:
                self._motion_deadline = 0.0
                self._last_command_ts = time.time()
            return error, self._abort_count != aborts_before

    def _motion_timeout(self, expected_s: float) -> int:
        """
        Reply timeout for a motion. The Tello only replies once a move
        finishes, and djitellopy's default 7s timeout + retry would re-send a
        slow move (flying it twice). The Tello is created with retry_count=1
        and the timeout covers the whole motion.
        """
        return int(expected_s * 1.5 + 3)

    def _send_immediate(self, command: str):
        """
        Fire a raw SDK command WITHOUT waiting for the command lock or a reply.
        Reserved for operator stop/land/emergency, which must work while a
        long move is still in flight.

        Its reply — and the reply of any motion it interrupts — still arrives on
        the shared reply queue later, so the next locked command is made to wait
        until then (and drain the queue) instead of mistaking them for its own.
        """
        now = time.monotonic()
        settle = SAFETY["abort_settle_s"]
        self._settle_until = max(self._settle_until, now + settle, self._motion_deadline + settle)
        self._last_abort_ts = now
        self._abort_count += 1
        if command == "emergency":
            self._emergency_count += 1
        self.tello.send_command_without_return(command)
        self._last_command_ts = time.time()

    def motion_in_flight(self) -> bool:
        return self._motion_deadline > time.monotonic()

    def command_in_progress(self) -> bool:
        """True if another thread currently holds the command lock."""
        if not self._cmd_lock.acquire(blocking=False):
            return True
        self._cmd_lock.release()
        return False

    def airborne_by_sensor(self) -> Optional[bool]:
        """Height sensor verdict (real drone): True/False, or None if unknown."""
        if self.simulate or self.tello is None or not self.state.link_ok:
            return None
        try:
            return int(self.tello.get_height()) > SAFETY["height_airborne_cm"]
        except Exception:
            return None

    _airborne_by_sensor = airborne_by_sensor

    def _settled_airborne(self) -> Optional[bool]:
        """Sensor verdict after a short settling window (a falling or climbing
        drone reads misleadingly at the instant a command returns)."""
        deadline = time.monotonic() + SAFETY["sensor_settle_s"]
        verdict = self.airborne_by_sensor()
        while time.monotonic() < deadline:
            time.sleep(0.1)
            verdict = self.airborne_by_sensor()
        return verdict

    def _confirm_landed(self) -> Optional[bool]:
        """After a land reply: True once the sensor shows ground, False if it
        still reads airborne after SAFETY["land_confirm_s"], None if unknown."""
        deadline = time.monotonic() + SAFETY["land_confirm_s"]
        while True:
            airborne = self.airborne_by_sensor()
            if airborne is None:
                return None
            if not airborne:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.2)

    def seconds_since_last_command(self) -> float:
        return time.time() - self._last_command_ts

    def seconds_since_state_packet(self) -> Optional[float]:
        """
        Age of the newest Tello state packet, or None if simulated.
        Must be polled regularly (the watchdog does, every second).
        """
        if self.simulate or self.tello is None:
            return None
        try:
            current = self.tello.get_current_state()
        except Exception:
            return None
        now = time.monotonic()
        if current is not self._last_state_obj:
            self._last_state_obj = current
            self._last_state_seen = now
        return now - self._last_state_seen

    def send_keepalive(self) -> bool:
        """
        Reset the Tello's 15s no-command auto-land timer.
        Skips (returns False) if another command currently holds the lock —
        that command already counts as activity.
        """
        if self.simulate:
            self._last_command_ts = time.time()
            return True
        if not self._cmd_lock.acquire(blocking=False):
            return False
        try:
            # `rc 0 0 0 0` is a no-reply hover command supported by every Tello
            # firmware; it counts as command activity for the auto-land timer.
            self.tello.send_rc_control(0, 0, 0, 0)
            return True
        except Exception as e:
            logger.debug(f"keepalive failed: {e}")
            return False
        finally:
            self._last_command_ts = time.time()
            self._cmd_lock.release()

    def refresh_telemetry(self):
        """Pull battery/height/heading from the Tello state packet (no command sent)."""
        if self.simulate or self.tello is None:
            self.state.height_cm = int(self.state.position.z)
            return
        try:
            self.state.battery_pct = int(self.tello.get_battery())
        except Exception:
            pass
        self._sync_from_sensors()

    def _sync_from_sensors(self):
        """Replace dead-reckoned heading/altitude with the drone's own readings."""
        if self.simulate or self.tello is None:
            self.state.height_cm = int(self.state.position.z)
            return
        try:
            height = int(self.tello.get_height())
            self.state.height_cm = height
            if self.state.is_flying and height > 0:
                self.state.position.z = height
        except Exception:
            pass
        if self._yaw_offset is not None:
            try:
                yaw = float(self.tello.get_yaw())
                self.state.heading_deg = (self._yaw_sign * (yaw - self._yaw_offset)) % 360
            except Exception:
                pass

    def _calibrate_yaw_sign(self, commanded_deg: int, yaw_before: Optional[float]):
        """Learn the IMU yaw direction from the first real rotation."""
        if self._yaw_sign_calibrated or yaw_before is None or abs(commanded_deg) < 20:
            return
        try:
            delta = (float(self.tello.get_yaw()) - yaw_before + 180) % 360 - 180
        except Exception:
            return
        if abs(delta) < 10:
            return  # rotation not reflected yet; try again next time
        self._yaw_sign = 1 if (delta > 0) == (commanded_deg > 0) else -1
        self._yaw_sign_calibrated = True
        logger.info(f"IMU yaw sign calibrated: {'+' if self._yaw_sign > 0 else '-'} (clockwise)")

    def simulate_tick(self, dt_s: float):
        """Advance the simulator clock: drain battery while flying."""
        if not self.simulate or not self.state.is_flying:
            return
        self._sim_battery = max(0.0, self._sim_battery - SIMULATION["battery_drain_pct_per_min"] * dt_s / 60.0)
        self.state.battery_pct = int(round(self._sim_battery))

    # ── Connection ──

    def connect(self) -> bool:
        """Connect to the Tello drone."""
        if self.simulate:
            logger.info("[SIM] Connected to simulated drone")
            self._sim_battery = 100.0
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
            # Entering SDK mode and setting speed are idempotent: retry lost packets.
            self._drone_call_retry(self.tello.connect)
            self._last_state_seen = time.monotonic()
            self.state.battery_pct = int(self.tello.get_battery())
            # Apply the conservative indoor speed instead of the firmware default.
            self._drone_call_retry(self.tello.set_speed, SAFETY["default_speed_cms"])
            self.state.speed_cms = SAFETY["default_speed_cms"]
            logger.info(f"Connected to Tello. Battery: {self.state.battery_pct}%")
            return True
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False

    # ── Flight Commands ──

    def takeoff(self, epoch: Optional[int] = None, source: str = "ai") -> dict:
        """Launch the drone."""
        try:
            with self._cmd_lock:
                self._check_epoch(epoch)
                check = self.safety.check_takeoff()
                if not check.allowed:
                    return {"success": False, "error": check.reason}

                if not self.simulate:
                    emergencies_before = self._emergency_count
                    error, interrupted = self._run_motion(self.tello.takeoff, expected_s=10, epoch=epoch)
                    if self._emergency_count != emergencies_before:
                        self._mark_landed()
                        return {"success": False, "error": "Takeoff aborted by E-STOP."}
                    if error or interrupted:
                        # A failed/foreign reply doesn't prove the drone stayed
                        # down — or went up. Ask the height sensor once it settles.
                        if self._settled_airborne():
                            self._begin_flight(source)
                            return {
                                "success": False,
                                "error": f"Takeoff reply was '{error or 'interrupted'}', but the height sensor "
                                         "says the drone is airborne — treat it as flying.",
                            }
                        return {"success": False, "error": error or "Takeoff interrupted by an operator stop."}

                self._begin_flight(source)
                logger.info("Takeoff successful")
                return {"success": True, "message": f"Drone is airborne at ~{self.state.position.z:.0f}cm"}
        except OperatorStopped as e:
            return {"success": False, "error": str(e)}

    def _begin_flight(self, source: str):
        # Each takeoff is a new home: geofence and dead reckoning start here.
        self.state.reset_origin()
        self.state.is_flying = True
        self.state.takeoff_time = time.time()
        self.state.position.z = SIMULATION["takeoff_height_cm"]
        if not self.simulate:
            try:
                self._yaw_offset = float(self.tello.get_yaw())
            except Exception:
                self._yaw_offset = None
        self._sync_from_sensors()
        self.state.log_action("takeoff", {}, True, source=source)

    def land(self, source: str = "ai") -> dict:
        """Land the drone safely. Never cancelled by an operator stop."""
        if self.simulate:
            if not self.state.is_flying:
                return {"success": True, "message": "Drone is already on the ground"}
        else:
            error = None
            for attempt in range(2):
                try:
                    self._drone_call(self.tello.land)
                    error = None
                    break
                except Exception as e:
                    error = str(e)
                    airborne = self._airborne_by_sensor()
                    if airborne is False:
                        # The sensor says it's down (a previous land worked, or
                        # it never took off) — the error reply doesn't matter.
                        self._mark_landed()
                        return {"success": True, "message": "Drone is on the ground"}
                    # One retry, only for a lost reply (UDP) — not for a refusal.
                    if attempt == 0 and "did not receive a response" in error.lower():
                        continue
                    break
            if error:
                return {"success": False, "error": f"Land failed: {error}"}
            # The reply alone can't be trusted: djitellopy hands out replies in
            # arrival order, so a late "ok" from an interrupted move could be
            # read as the land's. Believe the height sensor.
            if self._confirm_landed() is False:
                return {
                    "success": False,
                    "error": "Land reply received but the height sensor still reads airborne — "
                             "the reply may have belonged to another command.",
                }

        self._mark_landed()
        self.state.log_action("land", {}, True, source=source)
        logger.info("Landing successful")
        return {"success": True, "message": "Drone has landed"}

    def mark_flying_from_sensor(self):
        """Watchdog resync: the height sensor says airborne although we thought it landed."""
        if self.state.is_flying:
            return
        # Keep the current origin — we don't know where it actually took off.
        self.state.is_flying = True
        if self.state.takeoff_time is None:
            self.state.takeoff_time = time.time()
        self._sync_from_sensors()
        self.state.log_action("resync_airborne", {}, True, source="watchdog")

    def mark_landed_from_sensor(self):
        """Watchdog resync: the height sensor says on the ground although we thought it flew."""
        if not self.state.is_flying:
            return
        self._mark_landed()
        self.state.log_action("resync_landed", {}, True, source="watchdog")

    def _mark_landed(self):
        self.state.is_flying = False
        self.state.position.z = 0
        self.state.height_cm = 0
        self.state.takeoff_time = None

    def move(self, direction: str, cm: int, epoch: Optional[int] = None, source: str = "ai") -> dict:
        """Move in a direction by specified distance."""
        try:
            # Check + fly + state update are atomic w.r.t. other motion, so two
            # concurrent moves can't both pass the geofence/altitude checks
            # against a position that doesn't include the other.
            with self._cmd_lock:
                self._check_epoch(epoch)
                check = self.safety.check_move(direction, cm)
                if not check.allowed:
                    return {"success": False, "error": check.reason}

                requested_cm = cm
                if check.modified_params:
                    direction = check.modified_params.get("direction", direction)
                    cm = check.modified_params.get("cm", cm)

                interrupted = False
                if not self.simulate:
                    expected_s = cm / max(self.state.speed_cms, 10)
                    # SDK command names match our direction names ("forward 50").
                    error, interrupted = self._run_motion(
                        self.tello.send_control_command, f"{direction} {cm}", self._motion_timeout(expected_s),
                        expected_s=expected_s, epoch=epoch,
                    )
                    if error and not interrupted:
                        return {"success": False, "error": error}

                # Horizontal displacement is dead-reckoned with the heading at
                # the time of the move; altitude is then corrected from the
                # sensor. An interrupted move is recorded at full length: older
                # firmware ignores `stop` and finishes it, newer stops early.
                self._sync_from_sensors()
                self.state.update_position_after_move(direction, cm)
                self._sync_from_sensors()
                self.state.log_action("move", {"direction": direction, "cm": cm, "interrupted": interrupted}, True, source=source)

                position = {
                    "position": self.state.position.to_dict(),
                    "distance_from_home": round(self.state.position.distance_from_origin(), 1),
                }
                if interrupted:
                    return {
                        "success": False,
                        "error": f"Move {direction} {cm}cm was interrupted by an operator stop. "
                                 f"Position estimate may be off by up to {cm}cm.",
                        **position,
                    }

                result = {"success": True, "message": f"Moved {direction} {cm}cm", **position}
                if cm != requested_cm:
                    result["note"] = (
                        f"Requested {requested_cm}cm, moved {cm}cm "
                        f"(limit {SAFETY['max_single_move_cm']}cm per move, plus altitude ceiling/floor). "
                        "Look at the camera before moving further."
                    )
                if check.reason and "WARNING" in check.reason:
                    result["warning"] = check.reason
                return result
        except OperatorStopped as e:
            return {"success": False, "error": str(e)}

    def rotate(self, degrees: int, epoch: Optional[int] = None, source: str = "ai") -> dict:
        """Rotate the drone. Positive = clockwise."""
        try:
            with self._cmd_lock:
                self._check_epoch(epoch)
                check = self.safety.check_rotate(degrees)
                if not check.allowed:
                    return {"success": False, "error": check.reason}

                if check.modified_params:
                    degrees = check.modified_params.get("degrees", degrees)

                interrupted = False
                if not self.simulate:
                    yaw_before = None
                    try:
                        yaw_before = float(self.tello.get_yaw())
                    except Exception:
                        pass
                    command = f"cw {degrees}" if degrees >= 0 else f"ccw {abs(degrees)}"
                    expected_s = abs(degrees) / 30  # allow ~30°/s
                    error, interrupted = self._run_motion(
                        self.tello.send_control_command, command, self._motion_timeout(expected_s),
                        expected_s=expected_s, epoch=epoch,
                    )
                    if error and not interrupted:
                        return {"success": False, "error": error}
                    if not interrupted:
                        self._calibrate_yaw_sign(degrees, yaw_before)

                # On a real drone the IMU sync overrides this with the true
                # heading, which also covers an interrupted rotation.
                self.state.update_heading_after_rotate(degrees)
                self._sync_from_sensors()
                self.state.log_action("rotate", {"degrees": degrees, "interrupted": interrupted}, True, source=source)
                if interrupted:
                    return {
                        "success": False,
                        "error": "Rotation was interrupted by an operator stop.",
                        "heading": round(self.state.heading_deg, 1),
                    }
                result = {
                    "success": True,
                    "message": f"Rotated {'clockwise' if degrees >= 0 else 'counter-clockwise'} {abs(degrees)}°",
                    "heading": round(self.state.heading_deg, 1),
                }
                if check.reason and "WARNING" in check.reason:
                    result["warning"] = check.reason
                return result
        except OperatorStopped as e:
            return {"success": False, "error": str(e)}

    # ── Camera ──

    def get_frame(self) -> dict:
        """Capture a frame from the drone's camera as base64 JPEG."""
        if self.simulate:
            # Use laptop webcam if available (the Live video loop and the
            # dashboard can both read concurrently — serialize device access).
            if self._webcam is not None and self._webcam.isOpened():
                with self._webcam_lock:
                    ret, frame = self._webcam.read()
                if ret:
                    b64 = _encode_frame_as_jpeg_b64(frame)
                    return {"success": True, "image_base64": b64, "source": "webcam"}
                logger.warning("Webcam read failed, returning placeholder")

            # Fallback: gray placeholder
            frame = np.full((VISION["frame_height"], VISION["frame_width"], 3), 100, dtype=np.uint8)
            b64 = _encode_frame_as_jpeg_b64(frame)
            return {"success": True, "image_base64": b64, "note": "Simulated frame"}

        try:
            if self._frame_reader is None:
                self._frame_reader = self.tello.get_frame_read()

            frame = self._frame_reader.frame
            # djitellopy starts with an all-black placeholder until the first
            # decoded video frame arrives.
            if frame is None or not frame.any():
                return {"success": False, "error": "Camera stream not ready yet — try again in a moment"}

            # djitellopy (PyAV backend) returns RGB; OpenCV JPEG encoding expects BGR.
            b64 = _encode_frame_as_jpeg_b64(frame, input_rgb=True)
            return {"success": True, "image_base64": b64}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def release_camera(self):
        """Release the webcam (simulation) if one was opened."""
        if self._webcam is not None:
            with self._webcam_lock:
                self._webcam.release()
            self._webcam = None

    def stream_on(self) -> dict:
        """Start video stream."""
        if not self.simulate:
            try:
                self._drone_call_retry(self.tello.streamon)
            except Exception as e:
                return {"success": False, "error": str(e)}

        self.state.is_streaming = True
        self.state.log_action("stream_on", {}, True)
        return {"success": True, "message": "Video stream started"}

    def stream_off(self) -> dict:
        """Stop video stream."""
        if not self.simulate:
            try:
                self._drone_call_retry(self.tello.streamoff)
            except Exception as e:
                return {"success": False, "error": str(e)}

        self.state.is_streaming = False
        self._frame_reader = None
        self.state.log_action("stream_off", {}, True)
        return {"success": True, "message": "Video stream stopped"}

    # ── Telemetry (state-packet reads: no command sent, no lock needed) ──

    def get_battery(self) -> dict:
        """Get battery percentage."""
        if not self.simulate:
            try:
                self.state.battery_pct = int(self.tello.get_battery())
            except Exception:
                pass
        return {"success": True, "battery_pct": self.state.battery_pct}

    def get_height(self) -> dict:
        """Get current height in cm."""
        self._sync_from_sensors()
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

    def set_speed(self, speed: int, epoch: Optional[int] = None) -> dict:
        """Set flight speed (10-100 cm/s)."""
        check = self.safety.check_set_speed(speed)
        if check.modified_params:
            speed = check.modified_params["speed"]

        try:
            if self.simulate:
                self._check_epoch(epoch)
            else:
                self._drone_call(self.tello.set_speed, speed, epoch=epoch)
        except OperatorStopped as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

        self.state.speed_cms = speed
        self.state.log_action("set_speed", {"speed": speed}, True)
        return {"success": True, "message": f"Speed set to {speed} cm/s"}

    # ── Operator-only actions (never exposed to LLMs) ──

    def flip(self, direction: str, epoch: Optional[int] = None) -> dict:
        """Perform a flip."""
        try:
            with self._cmd_lock:
                self._check_epoch(epoch)
                check = self.safety.check_flip(direction)
                if not check.allowed:
                    return {"success": False, "error": check.reason}

                if not self.simulate:
                    flip_map = {"forward": "f", "back": "b", "left": "l", "right": "r"}
                    error, interrupted = self._run_motion(
                        self.tello.flip, flip_map[direction], expected_s=3, epoch=epoch,
                    )
                    if error or interrupted:
                        return {"success": False, "error": error or "Flip interrupted by an operator stop."}

                self._sync_from_sensors()
                self.state.log_action("flip", {"direction": direction}, True, source="operator")
                return {"success": True, "message": f"Flipped {direction}"}
        except OperatorStopped as e:
            return {"success": False, "error": str(e)}

    def hover(self, source: str = "operator") -> dict:
        """
        Stop in place. If a motion is in flight, send `stop` immediately to
        abort it (SDK 2.0+ firmware; older firmware ignores it and finishes the
        move). When idle the Tello already hovers, so nothing is sent.
        Doesn't set the operator hold — operator_command("hover") does — but
        does cancel every command still waiting for the drone (including one
        mid-way through its settle wait), so nothing queued flies afterwards.
        """
        self._bump_stop_epoch()
        if self.simulate or not self.motion_in_flight():
            self.state.log_action("hover", {}, True, source=source)
            return {"success": True, "message": "Hovering in place"}
        try:
            self._send_immediate("stop")
        except Exception as e:
            return {"success": False, "error": str(e)}
        self.state.log_action("hover", {"aborted_motion": True}, True, source=source)
        return {
            "success": True,
            "message": "Stop sent — aborting the current move. Older Tello firmware may finish "
                       "the move first; E-STOP is the only instant cut.",
        }

    def emergency_stop(self) -> dict:
        """Kill all motors immediately. Drone WILL fall."""
        if not self.simulate:
            try:
                self._send_immediate("emergency")
            except Exception:
                pass  # Best effort

        self._mark_landed()
        self.state.log_action("emergency_stop", {}, True, source="operator")
        logger.critical("EMERGENCY STOP triggered")
        return {"success": True, "message": "Emergency stop — motors killed"}

    def _priority_land(self, source: str = "operator") -> dict:
        """
        Land even if another command is in flight.

        If a motion is running, `stop` aborts it (SDK 2.0+ firmware; older
        firmware finishes the move — capped at 100cm). Then the command lock is
        taken and HELD through the land, so nothing queued can slip in between,
        and _prepare_command waits out the interrupted motion's stray replies.
        A raw `land` mid-move is typically rejected by the Tello, so if the
        lock never frees it's sent only as a last resort, and NOT reported as a
        success. `emergency_stop` is the only guaranteed instant interrupt.
        """
        if self.simulate:
            return self.land(source=source)
        # Single-flight: repeated Land presses / watchdog retries must not each
        # tie up a worker thread waiting on the same lock.
        if not self._land_gate.acquire(blocking=False):
            return {"success": False, "in_progress": True, "error": "A land is already in progress — wait, or use E-STOP."}
        try:
            if self.motion_in_flight():
                try:
                    self._send_immediate("stop")
                except Exception:
                    pass
            if not self._cmd_lock.acquire(timeout=SAFETY["operator_land_lock_timeout_s"]):
                try:
                    self._send_immediate("land")
                except Exception as e:
                    return {"success": False, "error": f"Land failed: {e}"}
                self.state.log_action("land", {"mode": "unconfirmed"}, False, source=source)
                return {
                    "success": False,
                    "error": "Drone busy: land was sent without confirmation and it may still be flying. "
                             "Use E-STOP if it doesn't descend.",
                }
            try:
                # A busy Tello rejects land ("error Not joystick") until an
                # unstoppable move finishes: retry while it's still airborne.
                result = {"success": False, "error": "Land not attempted"}
                attempts = SAFETY["priority_land_attempts"]
                for attempt in range(attempts):
                    result = self.land(source=source)
                    if result.get("success") or self.airborne_by_sensor() is False:
                        return result
                    if attempt + 1 < attempts:
                        time.sleep(1.0)
                return result
            finally:
                self._cmd_lock.release()
        finally:
            self._land_gate.release()

    # ── Operator hold ──

    @property
    def operator_hold(self) -> bool:
        return self.state.operator_hold

    def clear_operator_hold(self):
        """Allow AI motion again. Call when the user gives a new instruction."""
        if self.state.operator_hold:
            logger.info("Operator hold cleared")
        self.state.operator_hold = False

    def operator_command(self, action: str, args: Optional[dict] = None, source: str = "operator") -> dict:
        """
        Execute a human/operator action. Bypasses the LLM entirely. Blocking.

        Stop-type actions (hover, land, emergency_stop) set the operator hold,
        which makes execute() refuse AI motion until clear_operator_hold(), and
        cancel every command still waiting for the drone. Manual motion is
        single-flight: a second click while one is pending is refused.
        """
        args = args or {}
        if action in STOP_ACTIONS:
            self.state.operator_hold = True
            self._bump_stop_epoch()
            if action == "hover":
                return self.hover(source=source)
            if action == "land":
                return self._priority_land(source=source)
            return self.emergency_stop()
        if action == "resume":
            self.clear_operator_hold()
            return {"success": True, "message": "AI control resumed"}
        if action not in MANUAL_ACTIONS:
            return {"success": False, "error": f"Unknown operator action: {action}"}

        epoch = self._stop_epoch
        if not self._manual_gate.acquire(blocking=False):
            return {"success": False, "error": "Drone is busy with the previous manual command — wait for it to finish."}
        try:
            if action == "takeoff":
                return self.takeoff(epoch=epoch, source=source)
            if action == "move":
                return self.move(args.get("direction", "forward"), int(args.get("cm", 50)), epoch=epoch, source=source)
            if action == "rotate":
                return self.rotate(int(args.get("degrees", 90)), epoch=epoch, source=source)
            return self.flip(args.get("direction", "forward"), epoch=epoch)
        finally:
            self._manual_gate.release()

    def operator_command_background(
        self, action: str, args: Optional[dict] = None, on_done=None, source: str = "operator",
    ) -> threading.Thread:
        """
        Run operator_command on its own daemon thread and return immediately.
        CLIs use this so a typed `!estop` is never stuck behind a typed `land`
        that is still waiting for the drone. on_done(result) runs on that thread.
        """
        def run():
            result = self.operator_command(action, args, source=source)
            if on_done:
                try:
                    on_done(result)
                except Exception as e:
                    logger.debug(f"operator on_done callback failed: {e}")

        thread = threading.Thread(target=run, name=f"operator-{action}", daemon=True)
        thread.start()
        return thread

    # ── LLM Tool Dispatch ──

    def execute(self, function_name: str, arguments: dict) -> dict:
        """
        Execute a tool call from an LLM.
        This is the single entry point the agent loops use.
        """
        if function_name not in LLM_TOOLS:
            return {"success": False, "error": f"Unknown function: {function_name}"}

        # Captured before anything can block: if an operator stop lands while
        # this call waits for the drone, the call is cancelled, not flown.
        epoch = self._stop_epoch

        self.refresh_telemetry()

        # Critical battery overrides whatever the LLM wanted.
        if self.safety.should_force_land and function_name != "land":
            result = self.land(source="safety")
            return {
                "success": False,
                "error": f"Critical battery ({self.state.battery_pct}%) — drone was force-landed. "
                         + ("" if result.get("success") else f"Landing failed: {result.get('error')}"),
            }

        if self.state.operator_hold and function_name in MOTION_TOOLS:
            return {
                "success": False,
                "error": "Operator hold: a human stopped the drone. Do not move until the user gives a new instruction.",
            }

        dispatch = {
            "takeoff": lambda: self.takeoff(epoch=epoch),
            "land": lambda: self.land(),
            "move": lambda: self.move(arguments.get("direction", "forward"), int(arguments.get("cm", 50)), epoch=epoch),
            "rotate": lambda: self.rotate(int(arguments.get("degrees", 90)), epoch=epoch),
            "get_frame": lambda: self.get_frame(),
            "get_battery": lambda: self.get_battery(),
            "get_height": lambda: self.get_height(),
            "get_temperature": lambda: self.get_temperature(),
            "get_speed": lambda: self.get_speed(),
            "set_speed": lambda: self.set_speed(int(arguments.get("speed", SAFETY["default_speed_cms"])), epoch=epoch),
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
