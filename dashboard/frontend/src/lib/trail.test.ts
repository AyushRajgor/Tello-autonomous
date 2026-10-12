import { describe, expect, it } from "vitest";

import { LIMITS, batteryTone } from "./limits";
import { MAX_TRAIL_POINTS, appendTrail } from "./trail";

describe("appendTrail", () => {
  it("drops repeated samples", () => {
    const trail = appendTrail([{ x: 0, y: 50 }], { x: 0.2, y: 50.1 }, true);
    expect(trail).toHaveLength(1);
  });

  it("restarts at home after landing", () => {
    const trail = appendTrail([{ x: 0, y: 0 }, { x: 0, y: 50 }], { x: 0, y: 0 }, false);
    expect(trail).toEqual([{ x: 0, y: 0 }]);
  });

  it("caps the trail length", () => {
    let trail = [{ x: 0, y: 0 }];
    for (let i = 1; i <= MAX_TRAIL_POINTS + 10; i += 1) {
      trail = appendTrail(trail, { x: i, y: 0 }, true);
    }
    expect(trail).toHaveLength(MAX_TRAIL_POINTS);
    expect(trail[trail.length - 1]).toEqual({ x: MAX_TRAIL_POINTS + 10, y: 0 });
  });

  it("ignores non-finite points", () => {
    expect(appendTrail([], { x: Number.NaN, y: 0 }, true)).toEqual([]);
  });
});

describe("batteryTone", () => {
  it("follows the server thresholds", () => {
    expect(batteryTone(undefined)).toBe("unknown");
    expect(batteryTone(LIMITS.batteryCriticalPct)).toBe("critical");
    expect(batteryTone(LIMITS.batteryWarnPct)).toBe("warn");
    expect(batteryTone(80)).toBe("ok");
  });
});
