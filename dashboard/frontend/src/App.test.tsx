import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

class MockWebSocket {
  static OPEN = 1;

  readyState = MockWebSocket.OPEN;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor() {
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
  });

  it("renders the mission console shell", () => {
    render(<App />);

    expect(screen.getByText("TELLOAI")).toBeInTheDocument();
    expect(screen.getByText("Mission View")).toBeInTheDocument();
    expect(screen.getByText("Flight Controls")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /chat/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /systems/i })).toBeInTheDocument();
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
});
