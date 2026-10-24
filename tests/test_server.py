"""
Tests for the dashboard server (dashboard/server.py)

Run: python -m pytest tests/test_server.py -v
"""

import importlib
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import dashboard.server as server
from core.toolkit import TelloToolkit


# ── Fixtures / helpers ──

class FakeWatchdog:
    """Stands in for core.watchdog.SafetyWatchdog: lets tests fire events from a thread."""

    def __init__(self):
        self.listeners = []

    def add_listener(self, cb):
        self.listeners.append(cb)

    def remove_listener(self, cb):
        if cb in self.listeners:
            self.listeners.remove(cb)

    def emit_from_thread(self, event):
        def run():
            for cb in list(self.listeners):
                cb(event)
        t = threading.Thread(target=run)
        t.start()
        t.join()


class FakeLiveAgent:
    """Minimal live agent: records system notices; no network."""

    def __init__(self):
        self.notices = []

    async def send_system_notice(self, text):
        self.notices.append(text)


@pytest.fixture
def toolkit(monkeypatch):
    tk = TelloToolkit(tello=None, simulate=True)
    tk.connect()
    monkeypatch.setattr(server, "toolkit", tk)
    monkeypatch.setattr(server, "use_live", False)
    monkeypatch.setattr(server, "agent", None)
    monkeypatch.setattr(server, "live_agent", None)
    monkeypatch.setattr(server, "tracer", None)
    monkeypatch.setattr(server, "watchdog", None)
    monkeypatch.setitem(server.DASHBOARD, "token", "")
    monkeypatch.setitem(server.DASHBOARD, "host", "127.0.0.1")
    monkeypatch.setitem(server.DASHBOARD, "port", 8080)
    monkeypatch.setitem(server.DASHBOARD, "extra_origins", [])
    return tk


@pytest.fixture
def client(toolkit):
    with TestClient(server.app) as c:
        yield c
    server.clients.clear()


def receive_until(ws, msg_type, limit=20):
    """Read messages until one of msg_type arrives (telemetry may interleave)."""
    for _ in range(limit):
        msg = ws.receive_json()
        if msg.get("type") == msg_type:
            return msg
    raise AssertionError(f"no {msg_type!r} message within {limit} messages")


def send_operator(ws, action, args=None):
    payload = {"type": "operator", "action": action}
    if args is not None:
        payload["args"] = args
    ws.send_json(payload)
    return receive_until(ws, "operator_result")


# ── Import / static build ──

class TestImportAndStatic:
    def test_module_import_does_not_raise(self):
        importlib.reload(server)

    def test_root_returns_503_with_instructions_when_build_missing(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(server, "STATIC_DIST_DIR", tmp_path / "missing_dist")
        resp = client.get("/")
        assert resp.status_code == 503
        assert "npm run build" in resp.text
        assert "cd dashboard/frontend" in resp.text

    def test_root_serves_index_when_build_present(self, client, monkeypatch, tmp_path):
        (tmp_path / "index.html").write_text("<html>built</html>", encoding="utf-8")
        monkeypatch.setattr(server, "STATIC_DIST_DIR", tmp_path)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "built" in resp.text


# ── Origin / token checks ──

class TestAccessControl:
    def test_disallowed_origin_rejected(self, client):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws", headers={"origin": "http://evil.example"}):
                pass
        assert exc.value.code == 1008
        assert server.clients == []

    def test_allowed_origin_accepted(self, client):
        with client.websocket_connect("/ws", headers={"origin": "http://localhost:8080"}) as ws:
            assert ws.receive_json()["type"] == "state"
            assert ws.receive_json() == {"type": "mode", "mode": "legacy", "simulated": True}

    def test_vite_dev_origin_accepted(self, client):
        with client.websocket_connect("/ws", headers={"origin": "http://127.0.0.1:5173"}) as ws:
            assert ws.receive_json()["type"] == "state"

    def test_missing_origin_accepted(self, client):
        with client.websocket_connect("/ws") as ws:
            state = ws.receive_json()
            assert state["type"] == "state"
            assert "operator_hold" in state["data"]
            assert "link_ok" in state["data"]

    def test_extra_origin_accepted(self, client, monkeypatch):
        monkeypatch.setitem(server.DASHBOARD, "extra_origins", ["http://drone-console.test:3000"])
        with client.websocket_connect("/ws", headers={"origin": "http://drone-console.test:3000"}) as ws:
            assert ws.receive_json()["type"] == "state"

    def test_lan_ip_same_origin_only_when_bound_beyond_loopback(self, client, monkeypatch):
        headers = {"origin": "http://192.168.1.20:8080", "host": "192.168.1.20:8080"}
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws", headers=headers):
                pass
        monkeypatch.setitem(server.DASHBOARD, "host", "0.0.0.0")
        with client.websocket_connect("/ws", headers=headers) as ws:
            assert ws.receive_json()["type"] == "state"

    def test_dns_name_origin_not_trusted_via_host_header(self, monkeypatch, toolkit):
        # DNS-rebinding shape: Origin and Host agree but it's a hostname, not an IP.
        monkeypatch.setitem(server.DASHBOARD, "host", "0.0.0.0")
        assert not server.origin_allowed("http://evil.example:8080", "evil.example:8080")

    def test_token_required_on_ws_when_configured(self, client, monkeypatch):
        monkeypatch.setitem(server.DASHBOARD, "token", "s3cret")
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws"):
                pass
        assert exc.value.code == 1008
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws?token=wrong"):
                pass
        with client.websocket_connect("/ws?token=s3cret") as ws:
            assert ws.receive_json()["type"] == "state"

    def test_token_required_on_api_when_configured(self, client, monkeypatch):
        assert client.get("/api/traces").status_code == 200
        monkeypatch.setitem(server.DASHBOARD, "token", "s3cret")
        assert client.get("/api/traces").status_code == 401
        assert client.get("/api/traces?token=nope").status_code == 401
        resp = client.get("/api/traces?token=s3cret")
        assert resp.status_code == 200
        assert resp.json() == {"traces": []}


# ── Operator controls ──

class TestOperator:
    def test_takeoff_hover_land(self, client, toolkit):
        with client.websocket_connect("/ws") as ws:
            result = send_operator(ws, "takeoff")
            assert result["action"] == "takeoff"
            assert result["result"]["success"]
            assert toolkit.state.is_flying

            result = send_operator(ws, "hover")
            assert result["result"]["success"]
            assert toolkit.state.operator_hold
            state = receive_until(ws, "state")
            assert state["data"]["operator_hold"] is True

            result = send_operator(ws, "land")
            assert result["result"]["success"]
            assert not toolkit.state.is_flying
            assert toolkit.state.operator_hold

    def test_move_and_rotate_args(self, client, toolkit):
        toolkit.takeoff()
        with client.websocket_connect("/ws") as ws:
            result = send_operator(ws, "move", {"direction": "forward", "cm": 50})
            assert result["result"]["success"]
            assert result["args"] == {"direction": "forward", "cm": 50}
            assert toolkit.state.position.y == pytest.approx(50)

            result = send_operator(ws, "rotate", {"degrees": 90})
            assert result["result"]["success"]
            assert toolkit.state.heading_deg == pytest.approx(90)

    def test_emergency_stop_and_resume(self, client, toolkit):
        toolkit.takeoff()
        with client.websocket_connect("/ws") as ws:
            result = send_operator(ws, "emergency_stop")
            assert result["result"]["success"]
            assert not toolkit.state.is_flying
            assert toolkit.state.operator_hold

            result = send_operator(ws, "resume")
            assert result["result"]["success"]
            assert not toolkit.state.operator_hold

    def test_unknown_or_malformed_operator_action(self, client, toolkit):
        with client.websocket_connect("/ws") as ws:
            result = send_operator(ws, "self_destruct")
            assert result["result"]["success"] is False
            ws.send_json({"type": "operator", "action": "move", "args": {"direction": "forward", "cm": "far"}})
            result = receive_until(ws, "operator_result")
            assert result["result"]["success"] is False

    def test_server_never_lifts_hold_itself(self, client, toolkit):
        # The agent lifts the hold when its new turn STARTS; the server lifting
        # it on receipt would let a turn still running from before a STOP move.
        toolkit.state.operator_hold = True
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "command", "text": "hello drone"})
            msg = receive_until(ws, "chat")
            assert msg["text"] == "No agent configured."
            assert toolkit.state.operator_hold is True
            ws.send_json({"type": "audio_start"})
            ws.send_json({"type": "get_frame"})
            receive_until(ws, "frame")  # round-trip: audio_start was processed
            assert toolkit.state.operator_hold is True

    @pytest.mark.parametrize("keep_hold, expected_release", [(False, True), (True, False)])
    def test_chat_command_passes_release_hold_to_agent(self, client, toolkit, monkeypatch, keep_hold, expected_release):
        seen = []

        class FakeAgent:
            async def process_message(self, text, release_hold=True):
                seen.append((text, release_hold))
                return "ok"

        monkeypatch.setattr(server, "agent", FakeAgent())
        with client.websocket_connect("/ws") as ws:
            payload = {"type": "command", "text": "describe the room"}
            if keep_hold:
                payload["keep_hold"] = True  # the Observe button
            ws.send_json(payload)
            assert receive_until(ws, "chat")["role"] == "user"
            assert receive_until(ws, "chat")["text"] == "ok"
        assert seen == [("describe the room", expected_release)]

    def test_typed_stop_is_handled_locally_and_keeps_hold(self, client, toolkit):
        toolkit.takeoff()
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "command", "text": "stop"})
            result = receive_until(ws, "operator_result")
            assert result["action"] == "hover"
            assert toolkit.state.operator_hold is True
            assert toolkit.state.is_flying

    def test_get_frame(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "get_frame"})
            frame = receive_until(ws, "frame")
            assert frame["image_base64"]

    def test_client_removed_on_disconnect(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            assert len(server.clients) == 1
        # TestClient waits for the endpoint to finish, so finally has run.
        assert server.clients == []
        with client.websocket_connect("/ws") as ws2:
            ws2.receive_json()
            assert len(server.clients) == 1
        assert server.clients == []


# ── Live push-to-talk wiring ──

class FakePTTLiveAgent:
    """LiveAgent with explicit push-to-talk (begin/end_user_turn)."""

    def __init__(self):
        self.calls = []
        self.ended = threading.Event()

    async def begin_user_turn(self):
        self.calls.append("begin")

    async def send_audio(self, pcm):
        self.calls.append(("audio", pcm))

    async def end_user_turn(self):
        self.calls.append("end")
        self.ended.set()


class FakeLegacyVADLiveAgent:
    """Older LiveAgent: no begin/end_user_turn, only the sync mark_user_turn_end."""

    def __init__(self):
        self.calls = []
        self.ended = threading.Event()

    async def send_audio(self, pcm):
        self.calls.append(("audio", pcm))

    def mark_user_turn_end(self):
        self.calls.append("mark_end")
        self.ended.set()


class TestLivePushToTalk:
    def _connect_live(self, monkeypatch, live):
        # Lifespan runs with use_live=False (so no host speaker / real session
        # is started); the WS endpoint then sees live mode.
        monkeypatch.setattr(server, "live_agent", live)
        c = TestClient(server.app)
        c.__enter__()
        monkeypatch.setattr(server, "use_live", True)
        return c

    def test_turn_order_begin_audio_end(self, toolkit, monkeypatch):
        live = FakePTTLiveAgent()
        toolkit.state.operator_hold = True
        c = self._connect_live(monkeypatch, live)
        try:
            with c.websocket_connect("/ws") as ws:
                ws.receive_json()
                assert ws.receive_json() == {"type": "mode", "mode": "live", "simulated": True}
                ws.send_json({"type": "audio_start"})
                ws.send_bytes(b"\x01\x02")
                ws.send_bytes(b"\x03\x04")
                ws.send_json({"type": "audio_end"})
                assert live.ended.wait(5)
        finally:
            c.__exit__(None, None, None)
            server.clients.clear()
        # Ordered forwarding; LiveAgent.begin_user_turn() is what lifts the hold.
        assert live.calls == ["begin", ("audio", b"\x01\x02"), ("audio", b"\x03\x04"), "end"]

    def test_falls_back_to_mark_user_turn_end(self, toolkit, monkeypatch):
        live = FakeLegacyVADLiveAgent()
        c = self._connect_live(monkeypatch, live)
        try:
            with c.websocket_connect("/ws") as ws:
                ws.receive_json()
                ws.send_json({"type": "audio_start"})
                ws.send_bytes(b"\x05")
                ws.send_json({"type": "audio_end"})
                assert live.ended.wait(5)
        finally:
            c.__exit__(None, None, None)
            server.clients.clear()
        assert live.calls == [("audio", b"\x05"), "mark_end"]

    def test_disconnect_mid_turn_closes_turn(self, toolkit, monkeypatch):
        live = FakePTTLiveAgent()
        c = self._connect_live(monkeypatch, live)
        try:
            with c.websocket_connect("/ws") as ws:
                ws.receive_json()
                ws.send_json({"type": "audio_start"})
                ws.send_bytes(b"\x06")
            assert live.ended.wait(5)
        finally:
            c.__exit__(None, None, None)
            server.clients.clear()
        assert live.calls[0] == "begin"
        assert live.calls[-1] == "end"


# ── Watchdog → browser ──

class TestWatchdogBridge:
    def test_safety_event_broadcast_and_forwarded_to_live_agent(self, toolkit, monkeypatch):
        dog = FakeWatchdog()
        live = FakeLiveAgent()
        monkeypatch.setattr(server, "watchdog", dog)
        # Live agent present but use_live False: lifespan won't start it,
        # yet safety notices are still forwarded.
        monkeypatch.setattr(server, "live_agent", live)

        with TestClient(server.app) as c:
            assert len(dog.listeners) == 1
            with c.websocket_connect("/ws") as ws:
                ws.receive_json()
                dog.emit_from_thread({"level": "critical", "code": "battery_critical", "message": "Battery critical (9%)"})
                msg = receive_until(ws, "safety")
                assert msg == {
                    "type": "safety",
                    "level": "critical",
                    "code": "battery_critical",
                    "message": "Battery critical (9%)",
                }
                receive_until(ws, "state")
        server.clients.clear()

        assert live.notices and "Battery critical" in live.notices[0]
        # Listener detached on shutdown.
        assert dog.listeners == []

    def test_info_event_not_forwarded_to_live_agent(self, toolkit, monkeypatch):
        dog = FakeWatchdog()
        live = FakeLiveAgent()
        monkeypatch.setattr(server, "watchdog", dog)
        monkeypatch.setattr(server, "live_agent", live)

        with TestClient(server.app) as c:
            with c.websocket_connect("/ws") as ws:
                ws.receive_json()
                dog.emit_from_thread({"level": "info", "code": "link_restored", "message": "Drone telemetry restored."})
                assert receive_until(ws, "safety")["code"] == "link_restored"
        server.clients.clear()
        assert live.notices == []
