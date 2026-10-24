import { CircleAlert, CircleCheck, CircleX } from "lucide-react";

import { LIMITS } from "@/lib/limits";
import { cn } from "@/lib/utils";

export type PreflightCheck = { label: string; detail: string; status: "go" | "warn" | "nogo" };

/**
 * The conditions the server checks before a takeoff, shown before you ask
 * for one. Warnings don't block takeoff; a failed check does.
 */
export function preflightChecks({
  connected,
  simulated = false,
  linkLost,
  battery,
  video,
  mode,
  operatorHold
}: {
  connected: boolean;
  simulated?: boolean;
  linkLost: boolean;
  battery: number | undefined;
  video: boolean;
  mode: string;
  operatorHold: boolean;
}): PreflightCheck[] {
  return [
    { label: "Dashboard", detail: connected ? "Connected" : "Reconnecting", status: connected ? "go" : "nogo" },
    simulated
      ? { label: "Drone link", detail: "Simulator. No real drone is connected", status: "warn" }
      : { label: "Drone link", detail: linkLost ? "No telemetry" : "Receiving telemetry", status: linkLost ? "nogo" : "go" },
    {
      label: "Battery",
      detail:
        battery === undefined
          ? "Unknown"
          : battery > LIMITS.batteryWarnPct
            ? `${Math.round(battery)}%`
            : `${Math.round(battery)}% — charge above ${LIMITS.batteryWarnPct}% to take off`,
      status: battery === undefined ? "warn" : battery > LIMITS.batteryWarnPct ? "go" : "nogo"
    },
    { label: "Video", detail: video ? "Streaming" : "No feed, so the AI can't see", status: video ? "go" : "warn" },
    {
      label: "AI",
      detail: mode === "unknown" ? "Starting" : operatorHold ? "Paused by you" : mode === "live" ? "Gemini Live" : "Legacy agent",
      status: mode === "unknown" || operatorHold ? "warn" : "go"
    }
  ];
}

export function preflightVerdict(checks: PreflightCheck[]): "go" | "nogo" {
  return checks.some((check) => check.status === "nogo") ? "nogo" : "go";
}

const statusIcon = { go: CircleCheck, warn: CircleAlert, nogo: CircleX } as const;
const statusColor = { go: "text-success-text", warn: "text-warning-text", nogo: "text-destructive-text" } as const;
const statusName = { go: "OK", warn: "Check", nogo: "Blocked" } as const;

function CheckRows({ checks }: { checks: PreflightCheck[] }) {
  return (
    <ul className="flex flex-col gap-2.5">
      {checks.map(({ label, detail, status }) => {
        const Icon = statusIcon[status];
        return (
          <li key={label} className="flex items-start gap-2.5 text-sm">
            <Icon className={cn("mt-0.5 size-4 shrink-0", statusColor[status])} strokeWidth={1.5} aria-hidden="true" />
            <span className="sr-only">{statusName[status]}: </span>
            <span className="w-24 shrink-0 font-medium">{label}</span>
            <span className="min-w-0 text-pretty text-muted-foreground">{detail}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** Problems first; the passing checks stay one click away. */
export function PreflightList({ checks }: { checks: PreflightCheck[] }) {
  const problems = checks.filter((check) => check.status !== "go");
  const passed = checks.length - problems.length;
  return (
    <div className="flex flex-col gap-3">
      {problems.length > 0 ? (
        <CheckRows checks={problems} />
      ) : (
        <p className="text-sm text-muted-foreground">All {checks.length} checks passed.</p>
      )}
      {passed > 0 ? (
        <details className="group">
          <summary className="w-fit cursor-pointer rounded-md text-[13px] font-medium text-primary-text outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
            <span className="group-open:hidden">Show all checks</span>
            <span className="hidden group-open:inline">Hide all checks</span>
          </summary>
          <div className="mt-3">
            <CheckRows checks={checks} />
          </div>
        </details>
      ) : null}
    </div>
  );
}

export function VerdictBadge({ verdict }: { verdict: "go" | "nogo" }) {
  const Icon = verdict === "go" ? CircleCheck : CircleX;
  return (
    <span
      className={cn(
        "flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[13px] leading-4 font-medium whitespace-nowrap",
        verdict === "go" ? "bg-success-subtle text-success-text" : "bg-destructive-subtle text-destructive-text"
      )}
    >
      <Icon className="size-3.5" aria-hidden="true" />
      {verdict === "go" ? "Ready to fly" : "Not ready"}
    </span>
  );
}
