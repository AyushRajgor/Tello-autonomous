import { Hand, Play } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * Operator-hold banner. LINK LOST is drawn over the camera view instead
 * (VideoStage), where the operator is already looking.
 */
export function SafetyBanners({ operatorHold, onResume }: { operatorHold: boolean; onResume: () => void }) {
  if (!operatorHold) {
    return null;
  }

  return (
    <div
      role="status"
      className="flex shrink-0 flex-wrap items-center gap-3 rounded-xl border border-warning/50 bg-[linear-gradient(90deg,color-mix(in_oklch,var(--warning)_14%,transparent),transparent)] px-4 py-2.5 animate-in fade-in slide-in-from-top-1"
    >
      <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-warning/15 text-warning">
        <Hand className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold">AI paused by operator</p>
        <p className="text-xs text-pretty text-muted-foreground">
          The AI can't move the drone until you resume it or give it a new instruction.
        </p>
      </div>
      <Button size="sm" onClick={onResume} className="bg-warning text-background hover:bg-warning/90">
        <Play />
        Resume AI
      </Button>
    </div>
  );
}
