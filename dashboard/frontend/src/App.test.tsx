import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    localStorage.clear();
  });

  it("renders the flight deck shell", () => {
    render(<App />);

    expect(screen.getByRole("heading", { level: 1, name: "TelloAI" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Camera" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Flight data" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Safety" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Manual flight" })).toBeInTheDocument();
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

    fireEvent.change(screen.getByLabelText("Message TelloAI"), { target: { value: "scan the room" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(screen.getByText(/working on it/i)).toBeInTheDocument();

    serverPush({ type: "chat", role: "assistant", text: "Scanning." });
    expect(screen.queryByText(/working on it/i)).not.toBeInTheDocument();
  });

  it("suggestions fill the command line instead of sending", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "What do you see?" }));
    expect(screen.getByLabelText("Message TelloAI")).toHaveValue("What do you see?");
    expect(sentPayloads()).toEqual([]);
  });

  it("the step selector changes the distance the sticks send", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("radio", { name: "100 cm" }));
    fireEvent.click(screen.getByRole("button", { name: "Move up 100 cm" }));
    expect(sentPayloads()).toEqual([{ type: "operator", action: "move", args: { direction: "up", cm: 100 } }]);
  });

  it("folds Manual flight and the checks away, never Safety, and remembers it", () => {
    const { unmount } = render(<App />);
    const safety = screen.getByRole("region", { name: "Safety" });
    expect(within(safety).queryAllByRole("button").filter((b) => b.hasAttribute("aria-expanded"))).toEqual([]);

    const manual = screen.getByRole("button", { name: "Manual flight" });
    expect(manual).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(manual);
    expect(manual).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: "Take off" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Pre-flight checks" }));
    expect(screen.queryByText(/show all checks/i)).not.toBeVisible();
    // The verdict stays in the header, and Stop stays one click away.
    expect(screen.getByText("Not ready")).toBeVisible();
    expect(screen.getByRole("button", { name: "STOP — hover in place" })).toBeVisible();

    unmount();
    render(<App />);
    expect(screen.getByRole("button", { name: "Manual flight" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: "Pre-flight checks" })).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(screen.getByRole("button", { name: "Manual flight" }));
    expect(screen.getByRole("button", { name: "Take off" })).toBeVisible();
  });

  it("keeps the chosen step while Manual flight is folded", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("radio", { name: "100 cm" }));
    fireEvent.click(screen.getByRole("button", { name: "Manual flight" }));
    fireEvent.click(screen.getByRole("button", { name: "Manual flight" }));
    fireEvent.click(screen.getByRole("button", { name: "Move up 100 cm" }));
    expect(sentPayloads()).toEqual([{ type: "operator", action: "move", args: { direction: "up", cm: 100 } }]);
  });

  it("disables Take off while flying", () => {
    render(<App />);
    serverPush({ type: "state", data: { is_flying: true, link_ok: true } });
    expect(screen.getByRole("button", { name: "Flying" })).toBeDisabled();
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

    fireEvent.change(screen.getByLabelText("Message TelloAI"), { target: { value: "hello" } });
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
    fireEvent.click(screen.getByRole("button", { name: "Take off" }));
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

  it("Emergency stop requires an explicit confirmation", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /emergency stop/i }));
    expect(sentPayloads()).toEqual([]);
    expect(screen.getByText(/the drone will fall/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(sentPayloads()).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: /emergency stop/i }));
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

    const input = screen.getByLabelText("Message TelloAI");
    input.focus();
    fireEvent.keyDown(input, { key: " ", code: "Space" });
    fireEvent.keyDown(input, { key: "Escape", code: "Escape" });
    expect(sentPayloads()).toHaveLength(2);
  });

  it("shows the operator-hold banner with a Resume AI action", () => {
    render(<App />);
    expect(screen.queryByText(/You stopped the drone/)).not.toBeInTheDocument();

    serverPush({ type: "state", data: { operator_hold: true, link_ok: true } });
    expect(screen.getByText(/You stopped the drone/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /resume ai/i }));
    expect(sentPayloads()).toEqual([{ type: "operator", action: "resume" }]);
  });

  it("says Simulator, not Connected, when the server has no real drone", () => {
    render(<App />);
    act(() => mockSockets[0].onopen?.());
    const status = screen.getByRole("list", { name: "Status" });

    serverPush({ type: "mode", mode: "live", simulated: true });
    expect(within(status).getByText("Simulator")).toBeInTheDocument();
    expect(within(status).queryByText(/connected/i)).not.toBeInTheDocument();

    serverPush({ type: "mode", mode: "live", simulated: false });
    expect(within(status).getByText("Drone connected")).toBeInTheDocument();
  });

  it("shows LINK LOST prominently when the drone link drops", () => {
    render(<App />);
    serverPush({ type: "state", data: { link_ok: false } });
    expect(screen.getAllByRole("alert").some((el) => el.textContent?.includes("Link lost"))).toBe(true);
  });

  it("renders safety events as alerts and failed operator actions visibly", () => {
    render(<App />);
    serverPush({ type: "safety", level: "critical", code: "battery_critical", message: "Battery critical (9%)" });
    expect(screen.getAllByRole("alert").some((el) => el.textContent?.includes("Battery critical (9%)"))).toBe(true);

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

  it("applies the saved theme and follows the system setting", () => {
    let systemDark = false;
    const listeners: Array<() => void> = [];
    vi.stubGlobal("matchMedia", (query: string) => ({
      get matches() {
        return query.includes("dark") && systemDark;
      },
      addEventListener: (_: string, cb: () => void) => listeners.push(cb),
      removeEventListener: () => {}
    }));
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => setTimeout(() => cb(0), 0));
    localStorage.setItem("telloai.settings", JSON.stringify({ theme: "dark" }));
    const { unmount } = render(<App />);
    expect(document.documentElement).toHaveClass("dark");
    unmount();

    localStorage.setItem("telloai.settings", JSON.stringify({ theme: "system" }));
    render(<App />);
    expect(document.documentElement).not.toHaveClass("dark");
    systemDark = true;
    act(() => listeners.forEach((cb) => cb()));
    expect(document.documentElement).toHaveClass("dark");
    localStorage.removeItem("telloai.settings");
    document.documentElement.classList.remove("dark");
  });

  it("passes the page token through to the WebSocket URL", () => {
    window.history.replaceState({}, "", "/?token=s3cret");
    render(<App />);
    expect(mockSockets[0].url).toMatch(/\/ws\?token=s3cret$/);
  });
});

