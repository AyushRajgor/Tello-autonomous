import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

class MockWebSocket {
  static OPEN = 1;

  readyState = MockWebSocket.OPEN;
  url: string;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    mockSockets.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 3;
  }
}

let mockSockets: MockWebSocket[] = [];

function sentPayloads(socket: MockWebSocket = mockSockets[0]) {
  return socket.sent.map((raw) => JSON.parse(raw) as Record<string, unknown>);
}

function serverPush(payload: unknown, socket: MockWebSocket = mockSockets[0]) {
  act(() => {
    socket.onmessage?.({ data: JSON.stringify(payload) });
  });
}

describe("dashboard app", () => {
  beforeEach(() => {
    mockSockets = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        const body = url.startsWith("/api/traces") ? { traces: [] } : { active: false };
        return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
      })
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.history.replaceState({}, "", "/");
  });

  it("renders the flight deck shell", () => {
    render(<App />);

    expect(screen.getAllByText("TELLOAI").length).toBeGreaterThan(0);
    expect(screen.getByRole("region", { name: "Mission view" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Flight Controls" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Navigation" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /log/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /traces/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /systems/i })).toBeInTheDocument();
  });

  it("puts AI replies, tool calls and operator actions on one mission log", () => {
    render(<App />);
    serverPush({ type: "chat", role: "user", text: "look left" });
    serverPush({ type: "tool", name: "rotate", status: "ok", args: { degrees: -90 }, result: { success: true } });
    serverPush({ type: "chat", role: "assistant", text: "Turned left, I see a door." });

    expect(screen.getByText("look left")).toBeInTheDocument();
    expect(screen.getByText("rotate(degrees: -90)")).toBeInTheDocument();
    expect(screen.getByText("Turned left, I see a door.")).toBeInTheDocument();
  });

  it("shows the AI as working until it replies", () => {
    render(<App />);
    act(() => mockSockets[0].onopen?.());

    fireEvent.change(screen.getByLabelText("Command input"), { target: { value: "scan the room" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(screen.getByText(/AI working/i)).toBeInTheDocument();

    serverPush({ type: "chat", role: "assistant", text: "Scanning." });
    expect(screen.queryByText(/AI working/i)).not.toBeInTheDocument();
  });

  it("suggestions fill the command line instead of sending", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "What do you see?" }));
    expect(screen.getByLabelText("Command input")).toHaveValue("What do you see?");
    expect(sentPayloads()).toEqual([]);
  });

  it("the step selector changes the distance the sticks send", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("radio", { name: "100" }));
    fireEvent.click(screen.getByRole("button", { name: "Move up 100 cm" }));
    expect(sentPayloads()).toEqual([{ type: "operator", action: "move", args: { direction: "up", cm: 100 } }]);
  });

  it("disables Takeoff while airborne", () => {
    render(<App />);
    serverPush({ type: "state", data: { is_flying: true, link_ok: true } });
    expect(screen.getByRole("button", { name: "Airborne" })).toBeDisabled();
  });

  it("the phone quick bar sends STOP and Land directly", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Quick STOP — hover in place" }));
    fireEvent.click(screen.getByRole("button", { name: "Quick land" }));
    expect(sentPayloads()).toEqual([
      { type: "operator", action: "hover" },
      { type: "operator", action: "land" }
    ]);
  });

  it("clears stale latency when a new text command starts", async () => {
    render(<App />);

    act(() => {
      mockSockets[0].onopen?.();
      mockSockets[0].onmessage?.({
        data: JSON.stringify({ type: "metric", name: "voice_latency_ms", value: 800 })
      });
    });

    expect(screen.getByText(/800 ms/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Command input"), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(screen.queryByText(/800 ms/i)).not.toBeInTheDocument());
  });

  it("STOP sends a direct operator hover, not a chat command", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "STOP — hover in place" }));
    expect(sentPayloads()).toEqual([{ type: "operator", action: "hover" }]);
  });

  it("flight pad buttons send operator messages; Observe stays an AI chat command", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Takeoff" }));
    fireEvent.click(screen.getByRole("button", { name: "Move forward 50 cm" }));
    fireEvent.click(screen.getByRole("button", { name: "Turn right 90°" }));
    fireEvent.click(screen.getByRole("button", { name: /observe/i }));

    expect(sentPayloads()).toEqual([
      { type: "operator", action: "takeoff" },
      { type: "operator", action: "move", args: { direction: "forward", cm: 50 } },
      { type: "operator", action: "rotate", args: { degrees: 90 } },
      // Observe is camera-only: it must not lift an operator STOP.
      { type: "command", text: "Describe what you see. Do not move.", keep_hold: true }
    ]);
  });

  it("E-STOP requires an explicit confirmation", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /e-stop/i }));
    expect(sentPayloads()).toEqual([]);
    expect(screen.getByText(/the drone will fall/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(sentPayloads()).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: /e-stop/i }));
    fireEvent.click(screen.getByRole("button", { name: /cut motors/i }));
    expect(sentPayloads()).toEqual([{ type: "operator", action: "emergency_stop" }]);
  });

  it("Space = STOP and Escape = Land, but not while typing", () => {
    render(<App />);

    fireEvent.keyDown(document.body, { key: " ", code: "Space" });
    fireEvent.keyDown(document.body, { key: "Escape", code: "Escape" });
    expect(sentPayloads()).toEqual([
      { type: "operator", action: "hover" },
      { type: "operator", action: "land" }
    ]);

    const input = screen.getByLabelText("Command input");
    input.focus();
    fireEvent.keyDown(input, { key: " ", code: "Space" });
    fireEvent.keyDown(input, { key: "Escape", code: "Escape" });
    expect(sentPayloads()).toHaveLength(2);
  });

  it("shows the operator-hold banner with a Resume AI action", () => {
    render(<App />);
    expect(screen.queryByText("AI paused by operator")).not.toBeInTheDocument();

    serverPush({ type: "state", data: { operator_hold: true, link_ok: true } });
    expect(screen.getByText("AI paused by operator")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /resume ai/i }));
    expect(sentPayloads()).toEqual([{ type: "operator", action: "resume" }]);
  });

  it("shows LINK LOST prominently when the drone link drops", () => {
    render(<App />);
    serverPush({ type: "state", data: { link_ok: false } });
    expect(screen.getByRole("alert")).toHaveTextContent("LINK LOST");
  });

  it("renders safety events as alerts and failed operator actions visibly", () => {
    render(<App />);
    serverPush({ type: "safety", level: "critical", code: "battery_critical", message: "Battery critical (9%)" });
    expect(screen.getByRole("alert")).toHaveTextContent("Battery critical (9%)");

    serverPush({
      type: "operator_result",
      action: "flip",
      args: { direction: "forward" },
      result: { success: false, error: "Battery too low for flip" }
    });
    expect(screen.getAllByText(/Battery too low for flip/).length).toBeGreaterThan(0);

    fireEvent.click(screen.getAllByRole("button", { name: "Dismiss alert" })[0]!);
    expect(screen.getAllByRole("button", { name: "Dismiss alert" })).toHaveLength(1);
  });

  it("passes the page token through to the WebSocket URL", () => {
    window.history.replaceState({}, "", "/?token=s3cret");
    render(<App />);
    expect(mockSockets[0].url).toMatch(/\/ws\?token=s3cret$/);
  });
});

