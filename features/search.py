"""
Object Search — "Find X"  (scaffold: not wired into the agent yet)

Autonomous exploration to find a target object:
1. Scan current room (rotate 360° in steps, analyze each frame)
2. If target found → report to user
3. If not found → find a door/opening, turn toward it, and fly through it in
   short steps, re-checking the path before every step
4. Repeat until found, out of rooms, or the search has to stop

Every drone action goes through TelloToolkit.execute() in a worker thread, so
safety clamps, the critical-battery force-land and the operator hold all
apply, and the event loop never blocks on drone I/O.

The search stops (search() returns {"found": False, "aborted": True,
"reason": ...}) as soon as the operator stops the drone, the drone is not
flying, the battery is at or below SAFETY["battery_warn_pct"], or any tool
returns an error.
"""

import asyncio
import logging
from typing import Optional

from config import MOVEMENT, SAFETY
from vision.llm_vision import check_path_clear, find_doors_and_openings, search_for_object

logger = logging.getLogger(__name__)

# Turn toward an opening seen at the side of the frame. The Tello camera has
# a ~82° horizontal field of view, so "left"/"right" is roughly 30° off-axis.
OPENING_ALIGN_DEG = 30
# Forward steps when flying through an opening (path re-checked before each).
NAV_MAX_STEPS = 4


class SearchAborted(Exception):
    """Raised by a search step when the search must stop now. str(e) is the reason."""


async def _notify(on_status, message: str):
    if on_status:
        await on_status(message)


def _pick_opening(openings: list) -> Optional[dict]:
    """Best navigable opening: open (or partly open), not narrow; centered first."""
    usable = [
        o for o in openings
        if isinstance(o, dict)
        and o.get("state") in ("open", "partially_open")
        and o.get("estimated_width") in ("medium", "wide")
    ]
    usable.sort(key=lambda o: o.get("position") != "center")
    return usable[0] if usable else None


class ObjectSearcher:
    """Autonomous object search across rooms."""

    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.scanned_angles: list[float] = []
        self.rooms_visited: int = 0
        self.search_log: list[dict] = []

    # ── Drone access (always via execute, always off the event loop) ──

    async def _execute(self, name: str, args: Optional[dict] = None) -> dict:
        """Run an LLM-tool action through the toolkit. Raises SearchAborted on error."""
        result = await asyncio.to_thread(self.toolkit.execute, name, args or {})
        if not isinstance(result, dict) or not result.get("success", False):
            error = result.get("error") if isinstance(result, dict) else result
            raise SearchAborted(f"{name} failed: {error or 'unknown error'}")
        return result

    async def _check_can_continue(self):
        """Raise SearchAborted if the operator stopped us, we landed, or battery is low."""
        state = self.toolkit.state
        if state.operator_hold:
            raise SearchAborted("Operator hold: a human stopped the drone.")
        if not state.is_flying:
            raise SearchAborted("Drone is not flying.")
        battery = await self._execute("get_battery")
        pct = battery.get("battery_pct", state.battery_pct)
        if pct <= SAFETY["battery_warn_pct"]:
            raise SearchAborted(f"Battery low ({pct}%).")
        if state.operator_hold:  # may have been set while we read the battery
            raise SearchAborted("Operator hold: a human stopped the drone.")

    async def _capture_frame(self) -> str:
        frame = await self._execute("get_frame")
        image_b64 = frame.get("image_base64")
        if not image_b64:
            raise SearchAborted("get_frame failed: no image")
        return image_b64

    async def _rotate(self, degrees: int):
        await self._check_can_continue()
        await self._execute("rotate", {"degrees": degrees})

    # ── Search steps ──

    async def scan_room(self, target: str, on_status=None) -> Optional[dict]:
        """
        Scan current room by rotating 360° in steps.

        Args:
            target: What to find (e.g., "TV", "red cup")
            on_status: Async callback for status updates (for voice/dashboard)

        Returns:
            Search result dict if found, None if not found in this room.

        Raises:
            SearchAborted: the search must stop (see module docstring).
        """
        step = MOVEMENT["scan_rotation_step_deg"]
        steps = max(1, round(360 / step))

        for i in range(steps):
            await self._check_can_continue()
            image_b64 = await self._capture_frame()
            heading = self.toolkit.state.heading_deg

            search_result = await search_for_object(
                image_b64,
                target,
                drone_context=f"Scanning room. Heading: {heading:.0f}°, scanned {i * step}° of 360°",
            )
            self.scanned_angles.append(heading)
            self.search_log.append({"angle": heading, "result": search_result})

            if search_result.get("found") and search_result.get("confidence") in ("high", "medium"):
                location = search_result.get("location_in_frame", "ahead")
                await _notify(on_status, f"Found {target}! It's to the {location}.")
                return search_result

            desc = search_result.get("description", "")
            await _notify(on_status, f"Scanned {(i + 1) * step}°... {desc[:80]}")

            await self._rotate(step)

        return None  # Not found in this room

    async def find_exit(self) -> Optional[dict]:
        """
        Rotate in steps looking for a door/opening to the next room.

        Returns {"opening": {...}, "heading": deg} for the first usable opening,
        or None after a full turn. Raises SearchAborted if the search must stop.
        """
        step = MOVEMENT["scan_rotation_step_deg"]
        steps = max(1, round(360 / step))

        for _ in range(steps):
            await self._check_can_continue()
            image_b64 = await self._capture_frame()
            doors = await find_doors_and_openings(image_b64)
            best = _pick_opening(doors.get("openings", []))
            if best:
                return {"opening": best, "heading": self.toolkit.state.heading_deg}
            await self._rotate(step)

        return None

    async def align_to_opening(self, opening: dict) -> int:
        """Turn toward an opening seen left/right of center. Returns degrees turned."""
        degrees = {"left": -OPENING_ALIGN_DEG, "right": OPENING_ALIGN_DEG}.get(opening.get("position"), 0)
        if degrees:
            await self._rotate(degrees)
        return degrees

    async def navigate_through_opening(self, on_status=None) -> bool:
        """
        Fly forward through the opening ahead in short steps, checking the
        camera before every step. Returns False if the path is blocked.
        Raises SearchAborted if the search must stop.
        """
        await _notify(on_status, "Navigating through opening...")
        step_cm = min(MOVEMENT["explore_move_step_cm"], SAFETY["max_single_move_cm"])

        for _ in range(NAV_MAX_STEPS):
            await self._check_can_continue()
            image_b64 = await self._capture_frame()
            path = await check_path_clear(image_b64)
            if not path.get("clear", False):
                await _notify(on_status, f"Path blocked: {path.get('obstacle') or 'unknown'}. Stopping.")
                return False

            await self._check_can_continue()  # vision took a while; re-check before moving
            await self._execute("move", {"direction": "forward", "cm": step_cm})

        self.rooms_visited += 1
        await _notify(on_status, f"Entered new area (room #{self.rooms_visited + 1})")
        return True

    async def search(self, target: str, max_rooms: int = 3, on_status=None) -> dict:
        """
        Full search: scan rooms, navigate through doors, find target.

        Args:
            target: What to find
            max_rooms: Max rooms to explore before giving up
            on_status: Async callback for status updates

        Returns:
            {"found": bool, "aborted": bool, "rooms_searched": int, "details": ...}
            plus "reason" when aborted and "search_log" when not found.
        """
        rooms_searched = 0
        await _notify(on_status, f"Starting search for '{target}'...")

        try:
            await self._check_can_continue()

            for room_num in range(max_rooms):
                rooms_searched = room_num + 1
                await _notify(on_status, f"Scanning room {rooms_searched}...")

                result = await self.scan_room(target, on_status=on_status)
                if result and result.get("found"):
                    return {
                        "found": True,
                        "aborted": False,
                        "rooms_searched": rooms_searched,
                        "details": result,
                    }

                if room_num == max_rooms - 1:
                    break

                await _notify(on_status, "Not found here. Looking for a way to the next room...")
                exit_info = await self.find_exit()
                if exit_info is None:
                    await _notify(on_status, "No accessible exits found. Search complete.")
                    break

                opening = exit_info["opening"]
                await _notify(
                    on_status,
                    f"Found {opening.get('type', 'opening')} to the {opening.get('position', 'center')}. Navigating...",
                )
                await self.align_to_opening(opening)

                if not await self.navigate_through_opening(on_status=on_status):
                    await _notify(on_status, "Couldn't navigate through. Search complete.")
                    break

        except SearchAborted as e:
            reason = str(e)
            logger.warning(f"Search for '{target}' stopped: {reason}")
            await _notify(on_status, f"Search stopped: {reason}")
            return {
                "found": False,
                "aborted": True,
                "reason": reason,
                "rooms_searched": rooms_searched,
                "details": f"Search stopped: {reason}",
                "search_log": self.search_log[-5:],
            }

        return {
            "found": False,
            "aborted": False,
            "rooms_searched": rooms_searched,
            "details": "Target not found in searched areas.",
            "search_log": self.search_log[-5:],  # Last 5 entries
        }
