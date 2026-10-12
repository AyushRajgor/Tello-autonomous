import { Check, Minus, X } from "lucide-react";

import { LIMITS } from "@/lib/limits";
import { cn } from "@/lib/utils";

type Check = { label: string; detail: string; status: "go" | "warn" | "nogo" };

/**
 * Pre-flight checklist: the same conditions the server checks before a
 * takeoff, shown before you ask for one. Warnings don't block takeoff.
 */
export function preflightChecks({
  connected,
  linkLost,
  battery,
  video,
  mode,
  operatorHold
}: {
  connected: boolean;
  linkLost: boolean;
  battery: number | undefined;
  video: boolean;
  mode: string;
  operatorHold: boolean;
}): Check[] {
  return [
    { label: "Console", detail: connected ? "socket open" : "reconnecting", status: connected ? "go" : "nogo" },
    { label: "Drone link", detail: linkLost ? "no telemetry" : "telemetry ok", status: linkLost ? "nogo" : "go" },
    {
      label: "Battery",
      detail: battery === undefined ? "unknown" : `${Math.round(battery)}% · needs > ${LIMITS.batteryWarnPct}%`,
      status: battery === undefined ? "warn" : battery > LIMITS.batteryWarnPct ? "go" : "nogo"
    },
    { label: "Video", detail: video ? "streaming" : "no feed — AI is blind", status: video ? "go" : "warn" },
    {
      label: "AI agent",
      detail: mode === "unknown" ? "waiting" : operatorHold ? "paused by operator" : mode === "live" ? "Gemini Live" : "legacy loop",
      status: mode === "unknown" || operatorHold ? "warn" : "go"
    }
  ];
}

export function preflightVerdict(checks: Check[]): "go" | "nogo" {
  return checks.some((check) => check.status === "nogo") ? "nogo" : "go";
}

const icon = { go: Check, warn: Minus, nogo: X } as const;
const tone = {
  go: "bg-success/15 text-success",
  warn: "bg-warning/15 text-warning",
  nogo: "bg-destructive/20 text-destructive"
} as const;

export function PreflightList({ checks }: { checks: Check[] }) {
  return (
    <ul className="divide-y divide-border/60">
      {checks.map(({ label, detail, status }) => {
        const Icon = icon[status];
        return (
          <li key={label} className="flex items-center gap-2.5 py-1.5 text-xs">
            <span className={cn("grid size-4 shrink-0 place-items-center rounded", tone[status])}>
              <Icon className="size-3" strokeWidth={3} />
            </span>
            <span className="font-medium">{label}</span>
            <span className="ml-auto truncate font-mono text-[10px] text-muted-foreground">{detail}</span>
          </li>
        );
      })}
    </ul>
  );
}

export function VerdictBadge({ verdict }: { verdict: "go" | "nogo" }) {
  return (
    <span
      className={cn(
        "rounded px-1.5 py-px font-mono text-[10px] font-bold tracking-[0.14em]",
        verdict === "go" ? "bg-success/15 text-success" : "bg-destructive/20 text-destructive"
      )}
    >
      {verdict === "go" ? "GO" : "NO-GO"}
    </span>
  );
}
