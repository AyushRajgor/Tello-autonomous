import { describe, expect, it } from "vitest";

import { createDashboardModel, reduceDashboardMessage } from "./dashboard-state";

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
    expect(model.state?.battery_pct).toBe(82);
    expect(model.traces[0]?.trace_id).toBe("trace-1");
    expect(model.frameSrc).toBe("data:image/jpeg;base64,abc");
  });
});
