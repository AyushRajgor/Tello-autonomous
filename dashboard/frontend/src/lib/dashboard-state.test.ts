import { describe, expect, it } from "vitest";

import {
  createDashboardModel,
  dismissAlert,
  isLinkLost,
  isOperatorHold,
  reduceDashboardMessage,
  type DashboardMessage
} from "./dashboard-state";

describe("dashboard message reducer", () => {
  it("records chat messages from the websocket", () => {
    const model = reduceDashboardMessage(createDashboardModel(), {
      type: "chat",
      role: "assistant",
      text: "Ready when you are."
    });

    expect(model.messages).toMatchObject([
      {
        role: "assistant",
        text: "Ready when you are."
      }
    ]);
  });

  it("keeps the latest tool calls at the front", () => {
    const model = reduceDashboardMessage(
      reduceDashboardMessage(createDashboardModel(), {
        type: "tool",
        name: "takeoff",
        status: "ok",
        args: {},
        result: { success: true }
      }),
      {
        type: "tool",
        name: "get_frame",
        status: "ok",
        args: {},
        result: { success: true }
      }
    );

    expect(model.tools.map((tool) => tool.name)).toEqual(["get_frame", "takeoff"]);
  });

  it("updates state, mode, trace, and frame fields", () => {
    const model = [
      { type: "mode", mode: "live" },
      { type: "state", data: { battery_pct: 82 } },
      { type: "trace", data: { trace_id: "trace-1" } },
      { type: "frame", image_base64: "abc" }
    ].reduce(reduceDashboardMessage, createDashboardModel());

    expect(model.mode).toBe("live");
    expect(model.simulated).toBe(false);
    expect(reduceDashboardMessage(model, { type: "mode", mode: "live", simulated: true }).simulated).toBe(true);
    expect(model.state?.battery_pct).toBe(82);
    expect(model.traces[0]?.trace_id).toBe("trace-1");
    expect(model.frameSrc).toBe("data:image/jpeg;base64,abc");
  });

  it("records safety events as alerts and log rows", () => {
    const model = reduceDashboardMessage(createDashboardModel(), {
      type: "safety",
      level: "critical",
      code: "battery_critical",
      message: "Battery critical (9%) — force landing."
    });

    expect(model.alerts).toHaveLength(1);
    expect(model.alerts[0]).toMatchObject({ level: "critical", code: "battery_critical" });
    expect(model.messages.at(-1)).toMatchObject({
      role: "safety",
      level: "critical",
      text: "Battery critical (9%) — force landing."
    });
  });

  it("normalizes unknown safety levels to info and keeps alerts bounded", () => {
    const messages: DashboardMessage[] = Array.from({ length: 6 }, (_, i) => ({
      type: "safety",
      level: i === 0 ? "bogus" : "warning",
      code: `c${i}`,
      message: `m${i}`
    }));
    const model = messages.reduce(reduceDashboardMessage, createDashboardModel());

    expect(model.alerts).toHaveLength(4);
    expect(model.alerts[0]?.code).toBe("c5");
    expect(model.messages[0]?.level).toBe("info");
  });

  it("dismisses an alert by id", () => {
    const model = reduceDashboardMessage(createDashboardModel(), {
      type: "safety",
      level: "warning",
      code: "battery_low",
      message: "Battery low"
    });
    expect(dismissAlert(model, model.alerts[0]!.id).alerts).toEqual([]);
  });

  it("tracks link loss from safety events and state pushes", () => {
    let model = reduceDashboardMessage(createDashboardModel(), { type: "state", data: { link_ok: true } });
    expect(isLinkLost(model.state)).toBe(false);

    model = reduceDashboardMessage(model, { type: "safety", level: "critical", code: "link_lost", message: "Lost" });
    expect(isLinkLost(model.state)).toBe(true);

    model = reduceDashboardMessage(model, { type: "safety", level: "info", code: "link_restored", message: "Back" });
    expect(isLinkLost(model.state)).toBe(false);

    model = reduceDashboardMessage(model, { type: "state", data: { link_ok: false } });
    expect(isLinkLost(model.state)).toBe(true);
  });

  it("does not report link loss when the field is absent", () => {
    expect(isLinkLost(undefined)).toBe(false);
    expect(isLinkLost({ battery_pct: 50 })).toBe(false);
  });

  it("sets operator hold optimistically after STOP/land and clears it on resume", () => {
    let model = reduceDashboardMessage(createDashboardModel(), {
      type: "state",
      data: { operator_hold: false, battery_pct: 80 }
    });

    model = reduceDashboardMessage(model, {
      type: "operator_result",
      action: "hover",
      args: {},
      result: { success: true, message: "Hovering in place" }
    });
    expect(isOperatorHold(model.state)).toBe(true);
    expect(model.state?.battery_pct).toBe(80);
    expect(model.messages.at(-1)).toMatchObject({ role: "operator", level: "info", text: "STOP: Hovering in place" });
    expect(model.alerts).toEqual([]);

    model = reduceDashboardMessage(model, {
      type: "operator_result",
      action: "resume",
      result: { success: true, message: "AI control resumed" }
    });
    expect(isOperatorHold(model.state)).toBe(false);

    model = reduceDashboardMessage(model, { type: "state", data: { operator_hold: true } });
    expect(isOperatorHold(model.state)).toBe(true);
  });

  it("surfaces failed operator actions as visible alerts", () => {
    const model = reduceDashboardMessage(createDashboardModel(), {
      type: "operator_result",
      action: "move",
      args: { direction: "forward", cm: 50 },
      result: { success: false, error: "Drone is not flying" }
    });

    expect(isOperatorHold(model.state)).toBe(false);
    expect(model.alerts[0]).toMatchObject({ level: "warning", code: "operator_move_failed" });
    expect(model.alerts[0]?.message).toContain("Move forward 50 cm failed: Drone is not flying");
    expect(model.messages.at(-1)).toMatchObject({ role: "operator", level: "warning" });
  });

  it("treats a failed land as critical", () => {
    const model = reduceDashboardMessage(createDashboardModel(), {
      type: "operator_result",
      action: "land",
      result: { success: false, error: "timeout" }
    });
    expect(model.alerts[0]?.level).toBe("critical");
  });
});

