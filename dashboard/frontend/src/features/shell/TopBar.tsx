import { Battery, BatteryFull, BatteryLow, BatteryMedium, BatteryWarning, Hand } from "lucide-react";
import type * as React from "react";

import type { DashboardMode } from "@/lib/dashboard-state";
import { batteryTone } from "@/lib/limits";
import { cn } from "@/lib/utils";
import type { ConnectionState } from "@/types";

type Tone = "ok" | "bad" | "idle";

const dotTone: Record<Tone, string> = {
  ok: "bg-success",
  bad: "bg-destructive",
  idle: "bg-muted-foreground"
};

function StatusItem({ tone, children, className }: { tone: Tone; children: React.ReactNode; className?: string }) {
  return (
    <li className={cn("flex items-center gap-2 whitespace-nowrap", className)}>
      <span aria-hidden="true" className={cn("size-2 rounded-full", dotTone[tone])} />
      {children}
    </li>
  );
}

export function BatteryReadout({ pct }: { pct: number | undefined }) {
  const tone = batteryTone(pct);
  const Icon =
    pct === undefined ? Battery : tone === "critical" ? BatteryWarning : tone === "warn" ? BatteryLow : pct < 60 ? BatteryMedium : BatteryFull;
  return (
    <span
      className={cn(
        "flex items-center gap-1.5 text-sm font-medium tabular-nums",
        tone === "critical" ? "text-destructive-text" : tone === "warn" ? "text-warning-text" : "text-foreground"
      )}
    >
      <Icon className="size-4" aria-hidden="true" />
      <span>
        <span className="sr-only">Battery </span>
        {pct === undefined ? "—" : `${Math.round(pct)}%`}
      </span>
    </span>
  );
}

export function formatFlightClock(seconds: number | undefined): string {
  if (seconds === undefined || !Number.isFinite(seconds) || seconds <= 0) {
    return "0:00";
  }
  const total = Math.floor(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export function TopBar({
  connection,
  mode,
  flying,
  linkLost,
  operatorHold,
  battery,
  flightSeconds,
  settings
}: {
  connection: ConnectionState;
  mode: DashboardMode;
  flying: boolean;
  linkLost: boolean;
  operatorHold: boolean;
  battery: number | undefined;
  flightSeconds: number | undefined;
  settings: React.ReactNode;
}) {
  const connectionLabel = connection === "connected" ? "Connected" : connection === "connecting" ? "Connecting…" : "Offline";
  const agentLabel = mode === "live" ? "Gemini Live" : mode === "legacy" ? "Legacy agent" : "Agent starting…";

  return (
    <header className="sticky top-0 z-40 border-b bg-background/95 backdrop-blur-sm xl:static">
      <div className="flex h-14 items-center gap-4 ps-4 pe-2 sm:gap-6">
        <h1 className="flex items-center gap-2 text-[15px] font-semibold">
          <span className="grid size-7 place-items-center rounded-lg bg-primary text-primary-foreground">
            <svg viewBox="0 0 32 32" className="size-4" aria-hidden="true">
              <g fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round">
                <path d="M11 11l10 10M21 11L11 21" />
                <circle cx="8.5" cy="8.5" r="3.6" />
                <circle cx="23.5" cy="8.5" r="3.6" />
                <circle cx="8.5" cy="23.5" r="3.6" />
                <circle cx="23.5" cy="23.5" r="3.6" />
              </g>
            </svg>
          </span>
          TelloAI
        </h1>

        <ul aria-label="Status" className="flex min-w-0 items-center gap-5 text-sm text-muted-foreground">
          <StatusItem tone={connection === "connected" ? "ok" : connection === "connecting" ? "idle" : "bad"} className="max-md:hidden">
            {connectionLabel}
          </StatusItem>
          <StatusItem tone={mode === "unknown" ? "idle" : "ok"} className="max-lg:hidden">
            {agentLabel}
          </StatusItem>
          <StatusItem tone={linkLost ? "bad" : flying ? "ok" : "idle"}>
            {linkLost ? "Link lost" : flying ? "Flying" : "Landed"}
          </StatusItem>
          {operatorHold ? (
            <li className="flex items-center gap-1.5 rounded-full bg-warning-subtle px-2.5 py-0.5 text-[13px] font-medium whitespace-nowrap text-warning-text max-sm:hidden">
              <Hand className="size-3.5" aria-hidden="true" />
              AI paused
            </li>
          ) : null}
        </ul>

        <div className="ms-auto flex items-center gap-4">
          <span className="text-sm text-muted-foreground tabular-nums max-sm:hidden">
            Flight time <span className="font-medium text-foreground">{formatFlightClock(flightSeconds)}</span>
          </span>
          <BatteryReadout pct={battery} />
          {settings}
        </div>
      </div>
    </header>
  );
}
