import { VideoOff, WifiOff } from "lucide-react";

import type { VideoStatus } from "@/types";

/** The drone's camera. Video bleeds to the panel edge; status sits in small solid pills. */
export function VideoStage({
  frameSrc,
  videoStatus,
  cameraOn = true,
  fps,
  latencyMs,
  linkLost = false
}: {
  frameSrc?: string;
  videoStatus: VideoStatus;
  cameraOn?: boolean;
  fps?: number | null;
  latencyMs?: number | null;
  linkLost?: boolean;
}) {
  const hasPicture = cameraOn && Boolean(frameSrc);
  const source = videoStatus.source === "drone" ? "Drone camera" : videoStatus.source === "webcam" ? "Webcam" : "Camera";

  return (
    <section
      aria-label="Camera"
      className="surface relative isolate aspect-[4/3] w-full overflow-hidden bg-media-bg xl:aspect-auto xl:min-h-[240px] xl:flex-1"
    >
      {hasPicture ? (
        <img src={frameSrc} alt="Live camera view from the drone" className="absolute inset-0 size-full object-contain" />
      ) : (
        <div className="absolute inset-0 grid place-content-center justify-items-center gap-2 px-6 text-center">
          <VideoOff className="mb-1 size-6 text-media-text-muted" aria-hidden="true" />
          <p className="text-sm font-medium text-media-text">
            {!cameraOn ? "Camera feed is hidden" : videoStatus.active ? "Waiting for the first frame…" : "No video"}
          </p>
          <p className="max-w-[34ch] text-[13px] text-pretty text-media-text-muted">
            {!cameraOn
              ? "Turn on Show camera feed in Display settings to see it again."
              : videoStatus.active
                ? "Frames usually arrive within a second."
                : "Start the agent with --webcam, or connect the Tello, to see its camera here."}
          </p>
        </div>
      )}

      {hasPicture ? (
        <span className="absolute start-3 top-3 rounded-md bg-media-chip px-2 py-1 text-xs font-medium text-media-text">
          {source}
        </span>
      ) : null}

      {fps != null || latencyMs != null ? (
        <span className="absolute end-3 top-3 flex gap-3 rounded-md bg-media-chip px-2 py-1 text-xs text-media-text tabular-nums">
          {fps != null ? <span>{fps} fps</span> : null}
          {latencyMs != null ? <span>{latencyMs} ms</span> : null}
        </span>
      ) : null}

      {linkLost ? (
        <div role="alert" className="absolute inset-0 grid place-items-center bg-media-scrim p-4">
          <div className="flex max-w-sm items-start gap-3 rounded-xl bg-card p-4 text-card-foreground shadow-overlay">
            <WifiOff className="mt-0.5 size-5 shrink-0 text-destructive-text" aria-hidden="true" />
            <div>
              <p className="text-sm font-semibold">Link lost</p>
              <p className="mt-1 text-[13px] text-pretty text-muted-foreground">
                The drone stopped sending telemetry. It lands by itself after 15 seconds without commands, so keep clear of it.
              </p>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
