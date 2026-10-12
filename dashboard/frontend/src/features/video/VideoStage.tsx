import { Info, Video, VideoOff } from "lucide-react";
import type * as React from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { VideoStatus } from "@/types";

function StatusBadge({ active, children }: { active?: boolean; children: React.ReactNode }) {
  return (
    <Badge variant="outline" className="gap-1.5 font-normal text-muted-foreground">
      <span className={cn("size-1.5 rounded-full", active ? "bg-success" : "bg-muted-foreground/60")} />
      {children}
    </Badge>
  );
}

export function VideoStage({
  frameSrc,
  videoStatus,
  cameraOn = true,
  fps,
  latencyMs
}: {
  frameSrc?: string;
  videoStatus: VideoStatus;
  cameraOn?: boolean;
  fps?: number | null;
  latencyMs?: number | null;
}) {
  const showChip = fps != null || latencyMs != null;

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border bg-card">
      <header className="flex items-center justify-between gap-3 border-b px-4 py-2.5">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Video className="size-4 text-muted-foreground" />
          Mission View
        </div>
        <StatusBadge active={cameraOn && videoStatus.active}>
          {!cameraOn ? "camera off" : videoStatus.active ? videoStatus.source ?? "video" : "no video"}
        </StatusBadge>
      </header>

      <div className="feed-surface relative min-h-0 flex-1 bg-background">
        {!cameraOn ? (
          <div className="grid size-full place-content-center justify-items-center gap-3 text-muted-foreground">
            <VideoOff className="size-9 text-muted-foreground/40" />
            <span className="text-sm">Camera off</span>
          </div>
        ) : frameSrc ? (
          <img src={frameSrc} alt="Live camera frame" className="size-full object-contain" />
        ) : (
          <div className="grid size-full place-content-center justify-items-center gap-3 text-muted-foreground">
            <VideoOff className="size-9 text-muted-foreground/40" />
            <span className="text-sm">{videoStatus.active ? "Waiting for camera frame" : "No video signal"}</span>
          </div>
        )}

        {showChip ? (
          <div className="absolute bottom-3 left-3 flex items-center gap-4 rounded-lg border bg-background/70 px-3 py-1.5 font-mono text-xs tabular-nums backdrop-blur">
            {fps != null ? (
              <span>
                <span className="text-muted-foreground">FPS</span> {fps}
              </span>
            ) : null}
            {latencyMs != null ? (
              <span>
                <span className="text-muted-foreground">LAT</span> {latencyMs} ms
              </span>
            ) : null}
          </div>
        ) : null}

        {cameraOn && !videoStatus.active ? (
          <div className="absolute bottom-3 right-3 flex max-w-[55%] items-center gap-2 rounded-lg border bg-background/80 px-3 py-1.5 text-xs text-muted-foreground backdrop-blur">
            <Info className="size-3.5 shrink-0 text-warning" />
            <span>
              Run with <code className="rounded bg-muted px-1 py-0.5 font-mono text-foreground">--webcam</code> for frames.
            </span>
          </div>
        ) : null}
      </div>
    </section>
  );
}
