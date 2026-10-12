import { describe, expect, it } from "vitest";

import type { ChatMessage, ToolCall } from "./dashboard-state";
import { buildTimeline, formatToolSignature } from "./timeline";

const msg = (id: string, at: number): ChatMessage => ({ id, role: "user", text: id, receivedAt: at });
const tool = (id: string, at: number): ToolCall => ({ id, name: "move", status: "ok", args: {}, result: null, receivedAt: at });

describe("buildTimeline", () => {
  it("interleaves messages and tool calls chronologically", () => {
    const entries = buildTimeline([msg("m1", 1), msg("m2", 5)], [tool("t2", 4), tool("t1", 2)]);
    expect(entries.map((e) => e.id)).toEqual(["m1", "t1", "t2", "m2"]);
  });

  it("keeps messages ahead of tools on identical timestamps", () => {
    const entries = buildTimeline([msg("m1", 3)], [tool("t1", 3)]);
    expect(entries.map((e) => e.kind)).toEqual(["message", "tool"]);
  });
});

describe("formatToolSignature", () => {
  it("renders arguments inline", () => {
    expect(formatToolSignature({ name: "move", args: { direction: "forward", cm: 50 } })).toBe(
      "move(direction: forward, cm: 50)"
    );
  });

  it("truncates long values and tolerates non-object args", () => {
    expect(formatToolSignature({ name: "look", args: null })).toBe("look()");
    const long = formatToolSignature({ name: "say", args: { text: "x".repeat(40) } });
    expect(long).toBe(`say(text: ${"x".repeat(23)}…)`);
  });
});
