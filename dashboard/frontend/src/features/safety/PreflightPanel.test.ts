import { describe, expect, it } from "vitest";

import { preflightChecks, preflightVerdict } from "./PreflightPanel";

const ready = { connected: true, linkLost: false, battery: 80, video: true, mode: "live", operatorHold: false };

describe("preflight", () => {
  it("is GO when everything is nominal", () => {
    expect(preflightVerdict(preflightChecks(ready))).toBe("go");
  });

  it("is NO-GO at or below the takeoff battery threshold", () => {
    expect(preflightVerdict(preflightChecks({ ...ready, battery: 20 }))).toBe("nogo");
  });

  it("treats a missing video feed as a warning, not a blocker", () => {
    const checks = preflightChecks({ ...ready, video: false });
    expect(checks.find((c) => c.label === "Video")?.status).toBe("warn");
    expect(preflightVerdict(checks)).toBe("go");
  });

  it("is NO-GO when the console or drone link is down", () => {
    expect(preflightVerdict(preflightChecks({ ...ready, connected: false }))).toBe("nogo");
    expect(preflightVerdict(preflightChecks({ ...ready, linkLost: true }))).toBe("nogo");
  });
});
