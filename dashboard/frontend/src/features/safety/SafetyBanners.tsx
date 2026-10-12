import { Hand, Play, WifiOff } from "lucide-react";

import { Button } from "@/components/ui/button";

/** Persistent banners for states the operator must not miss. */
export function SafetyBanners({
  linkLost,
  operatorHold,
  onResume
}: {
  linkLost: boolean;
  operatorHold: boolean;
  onResume: () => void;
}) {
  if (!linkLost && !operatorHold) {
    return null;
  }

  return (
    <div className="flex shrink-0 flex-col gap-2">
      {linkLost ? (
        <div
          role="alert"
          className="flex items-center gap-3 rounded-xl border-2 border-destructive bg-destructive/15 px-4 py-3 text-destructive"
        >
          <WifiOff className="size-6 shrink-0 animate-pulse" />
          <div className="min-w-0">
            <p className="text-base font-bold tracking-widest">LINK LOST</p>
            <p className="text-xs text-pretty">
              No telemetry from the drone. The Tello lands itself after 15 s without commands — keep clear of it.
            </p>
          </div>
        </div>
      ) : null}

      {operatorHold ? (
        <div
          role="status"
          className="flex flex-wrap items-center gap-3 rounded-xl border border-warning/60 bg-warning/10 px-4 py-3"
        >
          <Hand className="size-5 shrink-0 text-warning" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold">AI paused by operator</p>
            <p className="text-xs text-pretty text-muted-foreground">
              The AI can't move the drone until you resume it or give it a new instruction.
            </p>
          </div>
          <Button size="sm" onClick={onResume}>
            <Play />
            Resume AI
          </Button>
        </div>
      ) : null}
    </div>
  );
}
