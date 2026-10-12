export const DASHBOARD_VIDEO_TARGET_FPS = 30;
export const DASHBOARD_VIDEO_REQUEST_INTERVAL_MS = Math.round(1000 / DASHBOARD_VIDEO_TARGET_FPS);
const FRAME_REQUEST_TIMEOUT_MS = 250;

export type FrameRequestLoop = {
  markFrameReceived: () => void;
  stop: () => void;
};

export function createFrameRequestLoop({
  sendFrameRequest,
  isVisible = () => true,
  targetFps = DASHBOARD_VIDEO_TARGET_FPS,
  requestTimeoutMs = FRAME_REQUEST_TIMEOUT_MS
}: {
  sendFrameRequest: () => boolean;
  isVisible?: () => boolean;
  targetFps?: number;
  requestTimeoutMs?: number;
}): FrameRequestLoop {
  let waitingForFrame = false;
  let lastRequestAt = 0;
  const intervalMs = Math.max(16, Math.round(1000 / Math.max(targetFps, 1)));

  const timer = setInterval(() => {
    const now = Date.now();
    if (!isVisible()) {
      return;
    }
    if (waitingForFrame && now - lastRequestAt < requestTimeoutMs) {
      return;
    }

    waitingForFrame = sendFrameRequest();
    lastRequestAt = waitingForFrame ? now : 0;
  }, intervalMs);

  return {
    markFrameReceived() {
      waitingForFrame = false;
    },
    stop() {
      clearInterval(timer);
    }
  };
}
