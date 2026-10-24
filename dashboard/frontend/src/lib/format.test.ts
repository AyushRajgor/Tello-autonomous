import { describe, expect, it } from "vitest";

import {
  buildWebSocketUrl,
  formatCentimeters,
  getPositionParts,
  getStateMetric,
  summarizeTrace
} from "./format";

describe("dashboard format helpers", () => {
  it("normalizes drone position objects from the backend state shape", () => {
    expect(getPositionParts({ position: { x: 12, y: -4, z: 90 } })).toEqual({
      x: 12,
      y: -4,
      z: 90
    });
  });

  it("falls back to centimeter suffixed fields when present", () => {
    expect(getPositionParts({ position: { x_cm: 5, y_cm: 6, z_cm: 7 } })).toEqual({
      x: 5,
      y: 6,
      z: 7
    });
  });

  it("formats missing centimeter values as a dash", () => {
    expect(formatCentimeters(undefined)).toBe("-");
    expect(formatCentimeters(142)).toBe("142 cm");
  });

  it("builds the websocket URL for secure and insecure pages", () => {
    expect(buildWebSocketUrl("http:", "localhost:8080")).toBe("ws://localhost:8080/ws");
    expect(buildWebSocketUrl("https:", "dash.local")).toBe("wss://dash.local/ws");
    expect(buildWebSocketUrl("http:", "localhost:8080", "a b&c")).toBe("ws://localhost:8080/ws?token=a%20b%26c");
    expect(buildWebSocketUrl("http:", "localhost:8080", null)).toBe("ws://localhost:8080/ws");
  });

  it("reads state metrics with a fallback", () => {
    expect(getStateMetric({ battery_pct: 88 }, "battery_pct", 0)).toBe(88);
    expect(getStateMetric({}, "battery_pct", 0)).toBe(0);
  });

  it("summarizes traces without requiring every optional field", () => {
    expect(
      summarizeTrace({
        trace_id: "abc",
        user_message: "look around",
        total_duration_ms: 1200,
        spans: [{ name: "vision" }, { name: "tool" }]
      })
    ).toEqual({
      id: "abc",
      command: "look around",
      duration: "1.20s",
      spans: 2
    });
  });
});
