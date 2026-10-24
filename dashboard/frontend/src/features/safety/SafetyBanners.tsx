import { Hand, Play } from "lucide-react";

import { Button } from "@/components/ui/button";

/** Shown while an operator Stop, Land or Emergency stop holds the AI. */
export function SafetyBanners({ operatorHold, onResume }: { operatorHold: boolean; onResume: () => void }) {
  if (!operatorHold) {
    return null;
  }

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl bg-warning-subtle p-3 ps-4">
      <Hand className="size-5 shrink-0 text-warning-text" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold">AI paused</p>
        <p className="text-[13px] text-pretty">You stopped the drone. The AI can't move it until you resume or send a new instruction.</p>
      </div>
      <Button variant="outline" size="sm" onClick={onResume}>
        <Play aria-hidden="true" />
        Resume AI
      </Button>
    </div>
  );
}
