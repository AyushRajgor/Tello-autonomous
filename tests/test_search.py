"""
Tests for features/search.py (object-search scaffold). Vision calls are
faked; drone actions run against a simulated TelloToolkit.
"""

import asyncio
import threading

import pytest

import features.search as search_mod
from config import MOVEMENT, SAFETY
from core.toolkit import LLM_TOOLS, TelloToolkit
from features.search import OPENING_ALIGN_DEG, ObjectSearcher

MOTION = ("takeoff", "move", "rotate", "set_speed")


class ExecOnlyToolkit:
    """
    Exposes only execute() and state, so any direct toolkit.move()/rotate()/
    get_frame() call from the searcher would raise AttributeError.
    """

    def __init__(self, flying=True):
        self.real = TelloToolkit(simulate=True)
        if flying:
            self.real.takeoff()
        self.state = self.real.state
        self.calls = []
        self.loop_thread_calls = 0
        self.fail = {}

    def execute(self, name, args):
        self.calls.append((name, dict(args)))
        if threading.current_thread() is threading.main_thread():
            self.loop_thread_calls += 1
        if name in self.fail:
            return {"success": False, "error": self.fail[name]}
        return self.real.execute(name, args)

    def names(self):
        return [name for name, _ in self.calls]

    def motion(self):
        return [(name, args) for name, args in self.calls if name in MOTION]


def patch_vision(monkeypatch, found_on=None, opening=None, clear=True, on_search=None):
    counters = {"search": 0, "doors": 0, "path": 0}

    async def fake_search(image_b64, target, drone_context="", trace_id=None):
        assert image_b64
        counters["search"] += 1
        if on_search:
            on_search(counters["search"])
        found = found_on is not None and counters["search"] == found_on
        return {
            "found": found,
            "location_in_frame": "center" if found else "none",
            "confidence": "high" if found else "low",
            "description": "a room",
            "suggestion": "",
        }

    async def fake_doors(image_b64, trace_id=None):
        counters["doors"] += 1
        return {"openings": [opening] if opening else [], "room_type": "office", "navigable": bool(opening)}

    async def fake_path(image_b64, trace_id=None):
        counters["path"] += 1
        return {"clear": clear, "obstacle": "" if clear else "chair", "distance_estimate": "far"}

    monkeypatch.setattr(search_mod, "search_for_object", fake_search)
    monkeypatch.setattr(search_mod, "find_doors_and_openings", fake_doors)
    monkeypatch.setattr(search_mod, "check_path_clear", fake_path)
    return counters


def test_search_uses_execute_off_the_event_loop(monkeypatch):
    patch_vision(monkeypatch, found_on=2)
    toolkit = ExecOnlyToolkit()

    result = asyncio.run(ObjectSearcher(toolkit).search("red cup"))

    assert result["found"] is True
    assert result["aborted"] is False
    assert toolkit.names() == ["get_battery", "get_battery", "get_frame", "get_battery", "rotate",
                               "get_battery", "get_frame"]
    assert toolkit.motion() == [("rotate", {"degrees": MOVEMENT["scan_rotation_step_deg"]})]
    assert set(toolkit.names()) <= LLM_TOOLS
    assert toolkit.loop_thread_calls == 0  # every call ran in a worker thread


def test_operator_hold_stops_search(monkeypatch):
    toolkit = ExecOnlyToolkit()

    def operator_presses_stop(n):
        toolkit.state.operator_hold = True

    patch_vision(monkeypatch, on_search=operator_presses_stop)

    result = asyncio.run(ObjectSearcher(toolkit).search("cup"))

    assert result["found"] is False
    assert result["aborted"] is True
    assert "Operator hold" in result["reason"]
    assert toolkit.motion() == []


def test_low_battery_stops_search(monkeypatch):
    counters = patch_vision(monkeypatch)
    toolkit = ExecOnlyToolkit()
    toolkit.state.battery_pct = SAFETY["battery_warn_pct"]

    result = asyncio.run(ObjectSearcher(toolkit).search("cup"))

    assert result["aborted"] is True
    assert "Battery low" in result["reason"]
    assert counters["search"] == 0
    assert toolkit.motion() == []


def test_not_flying_stops_search(monkeypatch):
    patch_vision(monkeypatch)
    toolkit = ExecOnlyToolkit(flying=False)

    result = asyncio.run(ObjectSearcher(toolkit).search("cup"))

    assert result["aborted"] is True
    assert "not flying" in result["reason"]
    assert toolkit.calls == []


def test_tool_error_stops_search(monkeypatch):
    patch_vision(monkeypatch)
    toolkit = ExecOnlyToolkit()
    toolkit.fail["get_frame"] = "No frame available"
    statuses = []

    async def on_status(msg):
        statuses.append(msg)

    result = asyncio.run(ObjectSearcher(toolkit).search("cup", on_status=on_status))

    assert result["aborted"] is True
    assert "get_frame failed: No frame available" in result["reason"]
    assert toolkit.motion() == []
    assert statuses[-1].startswith("Search stopped:")


def test_aligns_to_opening_and_rechecks_path_before_each_step(monkeypatch):
    counters = patch_vision(
        monkeypatch,
        opening={"type": "door", "position": "left", "state": "open", "estimated_width": "wide"},
    )
    toolkit = ExecOnlyToolkit()

    result = asyncio.run(ObjectSearcher(toolkit).search("cup", max_rooms=2))

    assert result["found"] is False
    assert result["aborted"] is False
    assert result["rooms_searched"] == 2

    motion = toolkit.motion()
    first_move = next(i for i, (name, _) in enumerate(motion) if name == "move")
    assert motion[first_move - 1] == ("rotate", {"degrees": -OPENING_ALIGN_DEG})

    moves = [args for name, args in motion if name == "move"]
    assert moves and all(m["direction"] == "forward" for m in moves)
    assert all(m["cm"] <= SAFETY["max_single_move_cm"] for m in moves)
    assert counters["path"] == len(moves)

    # A fresh frame is captured between the alignment turn and every move.
    names = toolkit.names()
    move_idx = [i for i, n in enumerate(names) if n == "move"]
    align_idx = max(i for i, (n, a) in enumerate(toolkit.calls) if n == "rotate" and i < move_idx[0])
    for prev, cur in zip([align_idx] + move_idx[:-1], move_idx):
        assert "get_frame" in names[prev + 1:cur]


def test_blocked_path_stops_without_moving(monkeypatch):
    patch_vision(
        monkeypatch,
        opening={"type": "doorway", "position": "center", "state": "open", "estimated_width": "medium"},
        clear=False,
    )
    toolkit = ExecOnlyToolkit()

    result = asyncio.run(ObjectSearcher(toolkit).search("cup", max_rooms=2))

    assert result["found"] is False
    assert result["aborted"] is False
    assert all(name != "move" for name, _ in toolkit.motion())


@pytest.mark.parametrize("position,expected", [("left", -OPENING_ALIGN_DEG), ("right", OPENING_ALIGN_DEG), ("center", 0)])
def test_align_to_opening(position, expected):
    toolkit = ExecOnlyToolkit()
    turned = asyncio.run(ObjectSearcher(toolkit).align_to_opening({"position": position}))
    assert turned == expected
    rotations = [args["degrees"] for name, args in toolkit.motion() if name == "rotate"]
    assert rotations == ([expected] if expected else [])


def test_narrow_or_closed_openings_are_ignored():
    pick = search_mod._pick_opening
    assert pick([{"state": "closed", "estimated_width": "wide"}]) is None
    assert pick([{"state": "open", "estimated_width": "narrow"}]) is None
    best = pick([
        {"state": "open", "estimated_width": "wide", "position": "left"},
        {"state": "partially_open", "estimated_width": "medium", "position": "center"},
    ])
    assert best["position"] == "center"
