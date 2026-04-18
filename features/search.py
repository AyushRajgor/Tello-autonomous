"""
Object Search — "Find X"

Autonomous exploration to find a target object:
1. Scan current room (rotate 360° in steps, analyze each frame)
2. If target found → report to user
3. If not found → identify doors/openings → navigate to next room
4. Repeat until found or battery low
"""

import asyncio
import logging
from typing import Optional

from core.toolkit import TelloToolkit
from vision.llm_vision import search_for_object, find_doors_and_openings
from config import MOVEMENT, SAFETY

logger = logging.getLogger(__name__)


class ObjectSearcher:
    """Autonomous object search across rooms."""

    def __init__(self, toolkit: TelloToolkit):
        self.toolkit = toolkit
        self.scanned_angles: list[float] = []
        self.rooms_visited: int = 0
        self.search_log: list[dict] = []

    async def scan_room(self, target: str, on_status=None) -> Optional[dict]:
        """
        Scan current room by rotating 360° in steps.

        Args:
            target: What to find (e.g., "TV", "red cup")
            on_status: Callback for status updates (for voice/dashboard)

        Returns:
            Search result dict if found, None if not found in this room.
        """
        step = MOVEMENT["scan_rotation_step_deg"]
        total_rotation = 0

        while total_rotation < 360:
            # Check battery
            battery = self.toolkit.get_battery()
            if battery["battery_pct"] <= SAFETY["battery_warn_pct"]:
                if on_status:
                    await on_status(f"Battery low ({battery['battery_pct']}%), stopping search.")
                return None

            # Capture and analyze frame
            frame_result = self.toolkit.get_frame()
            if not frame_result.get("success"):
                logger.warning("Failed to capture frame during scan")
                total_rotation += step
                self.toolkit.rotate(step)
                continue

            image_b64 = frame_result["image_base64"]

            # Search for target
            search_result = await search_for_object(
                image_b64,
                target,
                drone_context=f"Scanning room. Heading: {self.toolkit.state.heading_deg}°, "
                              f"Scanned: {total_rotation}° of 360°",
            )

            self.search_log.append({
                "angle": self.toolkit.state.heading_deg,
                "result": search_result,
            })

            if search_result.get("found") and search_result.get("confidence") in ("high", "medium"):
                if on_status:
                    location = search_result.get("location_in_frame", "ahead")
                    await on_status(f"Found {target}! It's to the {location}.")
                return search_result

            if on_status:
                desc = search_result.get("description", "")
                await on_status(f"Scanned {total_rotation}°... {desc[:80]}")

            # Rotate to next position
            self.toolkit.rotate(step)
            total_rotation += step

        return None  # Not found in this room

    async def find_exit(self) -> Optional[dict]:
        """Find the best door/opening to navigate to the next room."""
        step = MOVEMENT["scan_rotation_step_deg"]
        best_opening = None
        best_angle = 0
        total_rotation = 0

        while total_rotation < 360:
            frame_result = self.toolkit.get_frame()
            if frame_result.get("success"):
                doors = await find_doors_and_openings(frame_result["image_base64"])
                openings = doors.get("openings", [])

                for opening in openings:
                    if opening.get("state") in ("open", "partially_open"):
                        if opening.get("estimated_width") in ("medium", "wide"):
                            best_opening = opening
                            best_angle = self.toolkit.state.heading_deg
                            break

                if best_opening:
                    break

            self.toolkit.rotate(step)
            total_rotation += step

        if best_opening:
            return {"opening": best_opening, "heading": best_angle}
        return None

    async def navigate_through_opening(self, on_status=None) -> bool:
        """Attempt to fly through a detected opening."""
        if on_status:
            await on_status("Navigating through opening...")

        # Move forward in small increments, checking each time
        for _ in range(4):  # Max 4 * explore_step = ~320cm
            # Check path
            frame_result = self.toolkit.get_frame()
            if frame_result.get("success"):
                from vision.llm_vision import check_path_clear
                path = await check_path_clear(frame_result["image_base64"])
                if not path.get("clear", False):
                    if on_status:
                        await on_status(f"Path blocked: {path.get('obstacle', 'unknown')}. Stopping.")
                    return False

            # Move forward
            step = MOVEMENT["explore_move_step_cm"]
            result = self.toolkit.move("forward", step)
            if not result.get("success"):
                if on_status:
                    await on_status(f"Can't move: {result.get('error', 'unknown')}")
                return False

        self.rooms_visited += 1
        if on_status:
            await on_status(f"Entered new area (room #{self.rooms_visited + 1})")
        return True

    async def search(self, target: str, max_rooms: int = 3, on_status=None) -> dict:
        """
        Full search: scan rooms, navigate through doors, find target.

        Args:
            target: What to find
            max_rooms: Max rooms to explore before giving up
            on_status: Async callback for status updates

        Returns:
            {"found": bool, "rooms_searched": int, "details": ...}
        """
        if on_status:
            await on_status(f"Starting search for '{target}'...")

        for room_num in range(max_rooms):
            if on_status:
                await on_status(f"Scanning room {room_num + 1}...")

            # Scan current room
            result = await self.scan_room(target, on_status=on_status)

            if result and result.get("found"):
                return {
                    "found": True,
                    "rooms_searched": room_num + 1,
                    "details": result,
                }

            # Not found — try to find and navigate through a door
            if room_num < max_rooms - 1:
                if on_status:
                    await on_status("Not found here. Looking for a way to the next room...")

                exit_info = await self.find_exit()
                if exit_info is None:
                    if on_status:
                        await on_status("No accessible exits found. Search complete.")
                    break

                if on_status:
                    opening = exit_info["opening"]
                    await on_status(
                        f"Found {opening.get('type', 'opening')} to the "
                        f"{opening.get('position', 'ahead')}. Navigating..."
                    )

                success = await self.navigate_through_opening(on_status=on_status)
                if not success:
                    if on_status:
                        await on_status("Couldn't navigate through. Search complete.")
                    break

        return {
            "found": False,
            "rooms_searched": self.rooms_visited + 1,
            "details": "Target not found in searched areas.",
            "search_log": self.search_log[-5:],  # Last 5 entries
        }
