"""
Safety Watchdog

A background thread that enforces the continuous safety rules no matter what
the LLM (or the user) is doing:

- Battery: warns at SAFETY["battery_warn_pct"], force-lands at
  SAFETY["battery_critical_pct"] while airborne.
- Keepalive: the Tello auto-lands after 15s without a command. While flying
  and idle (e.g. the user is mid-conversation), a no-op keepalive is sent
  every SAFETY["keepalive_interval_s"].
- Link loss: no state packet for SAFETY["connection_timeout_s"] marks the link
  as lost (the drone's own 15s failsafe will land it) and alerts listeners.
- Simulation: advances the simulated battery drain.

It's a thread rather than an asyncio task so it works identically in the text
REPL (blocking input()), voice mode and the dashboard. Listeners are called on
the watchdog thread — async consumers must marshal back to their event loop
(e.g. loop.call_soon_threadsafe).
"""

import logging
import threading
import time
from typing import Callable, Optional

from config import SAFETY

logger = logging.getLogger(__name__)

# listener(event) where event = {"level": "info"|"warning"|"critical", "code": str, "message": str}
SafetyListener = Callable[[dict], None]

# Ticks (seconds) the height sensor must disagree with is_flying before resyncing.
MISMATCH_TICKS = 3


class SafetyWatchdog:
    def __init__(self, toolkit, interval_s: Optional[float] = None):
        self.toolkit = toolkit
        self.interval_s = interval_s or SAFETY["watchdog_interval_s"]
        self._listeners: list[SafetyListener] = []
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_tick: Optional[float] = None

        # Edge-trigger flags so each alert fires once per crossing.
        self._warned_low_battery = False
        self._link_alerted = False
        self._critical_alerted = False
        self._force_land_failure_alerted = False
        self._last_force_land_attempt: Optional[float] = None
        # Force-land runs on its own thread so link/battery checks keep ticking
        # while a slow landing waits for the drone.
        self._force_land_thread: Optional[threading.Thread] = None
        # Consecutive ticks where the height sensor disagrees with is_flying.
        self._state_mismatch_ticks = 0

    # ── Listeners ──

    def add_listener(self, cb: SafetyListener):
        self._listeners.append(cb)

    def remove_listener(self, cb: SafetyListener):
        if cb in self._listeners:
            self._listeners.remove(cb)

    def _emit(self, level: str, code: str, message: str):
        event = {"level": level, "code": code, "message": message}
        log = logger.critical if level == "critical" else logger.warning if level == "warning" else logger.info
        log(f"[watchdog] {message}")
        for cb in list(self._listeners):
            try:
                cb(event)
            except Exception as e:
                logger.debug(f"watchdog listener error: {e}")

    # ── Lifecycle ──

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._last_tick = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="safety-watchdog", daemon=True)
        self._thread.start()
        logger.info("Safety watchdog started")

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2 * self.interval_s + 1)
        self._thread = None

    def _run(self):
        while not self._stop.wait(self.interval_s):
            try:
                self.tick()
            except Exception as e:  # never let the watchdog die
                logger.error(f"watchdog tick failed: {e}")

    # ── One safety pass (public for tests) ──

    def tick(self, now: Optional[float] = None):
        now = time.monotonic() if now is None else now
        dt = 0.0 if self._last_tick is None else max(0.0, now - self._last_tick)
        self._last_tick = now

        tk = self.toolkit
        state = tk.state

        tk.simulate_tick(dt)
        tk.refresh_telemetry()

        self._check_link()
        self._reconcile_flight_state()
        self._check_battery(now)

        # Keepalive only matters while airborne and idle.
        if state.is_flying and state.link_ok and tk.seconds_since_last_command() >= SAFETY["keepalive_interval_s"]:
            tk.send_keepalive()

    def _check_link(self):
        state = self.toolkit.state
        age = self.toolkit.seconds_since_state_packet()
        if age is None:
            state.link_ok = True
            return
        if age > SAFETY["connection_timeout_s"]:
            state.link_ok = False
            if not self._link_alerted:
                self._link_alerted = True
                self._emit(
                    "critical", "link_lost",
                    f"Lost contact with the drone ({age:.0f}s without telemetry). "
                    "The Tello auto-lands after 15s without commands.",
                )
        else:
            state.link_ok = True
            if self._link_alerted:
                self._link_alerted = False
                self._emit("info", "link_restored", "Drone telemetry restored.")

    def _reconcile_flight_state(self):
        """
        Safety net for every path that can leave is_flying wrong (a misread
        reply, an interrupted takeoff, a drone that auto-landed): if the height
        sensor disagrees for MISMATCH_TICKS in a row while nothing is commanding
        the drone, believe the sensor.
        """
        tk = self.toolkit
        state = tk.state
        airborne = tk.airborne_by_sensor()
        if airborne is None or airborne == state.is_flying or tk.motion_in_flight() or tk.command_in_progress():
            self._state_mismatch_ticks = 0
            return
        self._state_mismatch_ticks += 1
        if self._state_mismatch_ticks < MISMATCH_TICKS:
            return
        self._state_mismatch_ticks = 0
        if airborne:
            tk.mark_flying_from_sensor()
            self._emit(
                "critical", "state_resync_airborne",
                "The drone is airborne but was marked as landed — resynced. "
                "Keepalive and battery protection are active again.",
            )
        else:
            tk.mark_landed_from_sensor()
            self._emit("warning", "state_resync_landed", "The drone is on the ground but was marked as flying — resynced.")

    def _on_force_land_done(self, result: dict):
        if result.get("success") or result.get("in_progress") or self._force_land_failure_alerted:
            return
        self._force_land_failure_alerted = True
        self._emit(
            "critical", "force_land_failed",
            f"Force landing failed: {result.get('error')} Retrying every "
            f"{SAFETY['force_land_retry_s']:.0f}s — use E-STOP if needed.",
        )

    def _check_battery(self, now: float):
        tk = self.toolkit
        state = tk.state
        pct = state.battery_pct

        if not state.is_flying or pct > SAFETY["battery_critical_pct"]:
            # Episode over (landed or battery swapped): re-arm critical alerts.
            self._critical_alerted = False
            self._force_land_failure_alerted = False
            self._last_force_land_attempt = None

        if pct > SAFETY["battery_warn_pct"]:
            self._warned_low_battery = False
            return

        if state.is_flying and pct <= SAFETY["battery_critical_pct"]:
            if not self._critical_alerted:
                self._critical_alerted = True
                self._emit("critical", "battery_critical", f"Battery critical ({pct}%) — force landing.")
            # Retry a failed landing every few seconds, not every tick: each
            # attempt sends stop/land to the drone and each alert interrupts
            # the voice model.
            if self._force_land_thread is not None and self._force_land_thread.is_alive():
                return
            last = self._last_force_land_attempt
            if last is not None and now - last < SAFETY["force_land_retry_s"]:
                return
            self._last_force_land_attempt = now
            self._force_land_thread = tk.operator_command_background(
                "land", on_done=self._on_force_land_done, source="watchdog",
            )
            return

        if not self._warned_low_battery:
            self._warned_low_battery = True
            self._emit("warning", "battery_low", f"Battery low ({pct}%). Land soon.")
