import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DASHBOARD_VIDEO_REQUEST_INTERVAL_MS,
  DASHBOARD_VIDEO_TARGET_FPS,
  createFrameRequestLoop
} from "./video-stream";

describe("video frame request loop", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("targets a 30 fps dashboard feed", () => {
    expect(DASHBOARD_VIDEO_TARGET_FPS).toBe(30);
    expect(DASHBOARD_VIDEO_REQUEST_INTERVAL_MS).toBe(33);
  });

  it("does not queue another request while a frame is in flight", () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);

    const sendFrameRequest = vi.fn(() => true);
    const loop = createFrameRequestLoop({ sendFrameRequest, isVisible: () => true });

    vi.advanceTimersByTime(DASHBOARD_VIDEO_REQUEST_INTERVAL_MS);
    expect(sendFrameRequest).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(DASHBOARD_VIDEO_REQUEST_INTERVAL_MS * 4);
    expect(sendFrameRequest).toHaveBeenCalledTimes(1);

    loop.markFrameReceived();
    vi.advanceTimersByTime(DASHBOARD_VIDEO_REQUEST_INTERVAL_MS);
    expect(sendFrameRequest).toHaveBeenCalledTimes(2);

    loop.stop();
  });
});
