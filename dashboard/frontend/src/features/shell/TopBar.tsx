import { Hand, WifiOff } from "lucide-react";
import * as React from "react";

import { batteryTone, type BatteryTone } from "@/lib/limits";
import { cn } from "@/lib/utils";
import type { ConnectionState } from "@/types";
import type { DashboardMode } from "@/lib/dashboard-state";

type Tone = "ok" | "warn" | "bad" | "idle" | "ai";

const dotTone: Record<Tone, string> = {
  ok: "bg-success shadow-[0_0_8px] shadow-success/60",
  warn: "bg-warning shadow-[0_0_8px] shadow-warning/60",
  bad: "bg-destructive shadow-[0_0_8px] shadow-destructive/60",
  idle: "bg-muted-foreground/50",
  ai: "bg-primary shadow-[0_0_8px] shadow-primary/60"
};

function Segment({ label, value, tone, className }: { label: string; value: string; tone: Tone; className?: string }) {
  return (
    <div className={cn("flex items-center gap-2 px-3", className)}>
      <span className={cn("size-1.5 shrink-0 rounded-full", dotTone[tone])} />
      <span className="flex flex-col leading-none">
        <span className="font-mono text-[9px] tracking-[0.14em] text-muted-foreground uppercase">{label}</span>
        <span className="mt-1 text-xs font-medium capitalize">{value}</span>
      </span>
    </div>
  );
}

const batteryFill: Record<BatteryTone, string> = {
  unknown: "bg-muted-foreground/40",
  ok: "bg-success",
  warn: "bg-warning",
  critical: "bg-destructive animate-pulse"
};

export function BatteryGauge({ pct }: { pct: number | undefined }) {
  const tone = batteryTone(pct);
  const level = pct === undefined ? 0 : Math.max(0, Math.min(100, pct));
  const cells = 5;
  const lit = Math.ceil((level / 100) * cells);
  return (
    <div
      className="flex items-center gap-2"
      role="meter"
      aria-label="Battery"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct === undefined ? undefined : Math.round(level)}
    >
      <span className="flex items-center gap-[3px]">
        <span className="flex h-3.5 items-stretch gap-[2px] rounded-[3px] border border-foreground/30 p-[2px]">
          {Array.from({ length: cells }, (_, i) => (
            <span key={i} className={cn("w-[5px] rounded-[1px]", i < lit ? batteryFill[tone] : "bg-foreground/8")} />
          ))}
        </span>
        <span className="h-1.5 w-[2px] rounded-r-sm bg-foreground/30" />
      </span>
      <span
        className={cn(
          "font-mono text-xs tabular-nums",
          tone === "critical" ? "text-destructive" : tone === "warn" ? "text-warning" : "text-foreground"
        )}
      >
        {pct === undefined ? "—" : `${Math.round(level)}%`}
      </span>
    </div>
  );
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
  const socketTone: Tone = connection === "connected" ? "ok" : connection === "connecting" ? "warn" : "bad";
  const agentValue = mode === "unknown" ? "pending" : mode === "live" ? "Gemini Live" : "Legacy loop";
  const flightValue = linkLost ? "Link lost" : flying ? "Airborne" : "Grounded";
  const flightTone: Tone = linkLost ? "bad" : flying ? "ok" : "idle";

  return (
    <header className="sticky top-0 z-40 flex h-14 shrink-0 items-center gap-2 border-b bg-background/85 px-3 backdrop-blur-md sm:gap-4 sm:px-4">
      <div className="flex items-center gap-2.5">
        <BrandMark />
        <div className="flex flex-col leading-none max-sm:hidden">
          <span className="text-sm font-semibold tracking-[0.18em]">TELLOAI</span>
          <span className="mt-1 font-mono text-[9px] tracking-[0.2em] text-muted-foreground uppercase">Flight deck</span>
        </div>
        <span className="sr-only sm:hidden">TELLOAI</span>
      </div>

      <div className="mx-1 h-7 w-px bg-border max-sm:hidden" />

      <nav aria-label="System status" className="flex min-w-0 items-center divide-x divide-border overflow-hidden">
        <Segment label="Console" value={connection} tone={socketTone} className="pl-0 max-md:hidden" />
        <Segment label="Agent" value={agentValue} tone={mode === "live" ? "ai" : mode === "legacy" ? "warn" : "idle"} className="max-md:hidden" />
        <Segment label="Drone" value={flightValue} tone={flightTone} className="max-md:pl-0" />
      </nav>

      {operatorHold ? (
        <span className="flex items-center gap-1.5 rounded-full border border-warning/50 bg-warning/10 px-2.5 py-1 text-[11px] font-medium text-warning max-sm:hidden">
          <Hand className="size-3.5" />
          AI paused
        </span>
      ) : null}
      {linkLost ? (
        <span className="flex items-center gap-1.5 rounded-full bg-destructive px-2.5 py-1 text-[11px] font-bold tracking-wider text-white max-sm:hidden">
          <WifiOff className="size-3.5" />
          LINK LOST
        </span>
      ) : null}

      <div className="ml-auto flex items-center gap-3 sm:gap-4">
        <div className="flex items-center gap-1.5 font-mono text-xs tabular-nums max-sm:hidden" title="Time airborne">
          <span className="text-[9px] tracking-[0.14em] text-muted-foreground">T+</span>
          <span className={flying ? "text-foreground" : "text-muted-foreground"}>{formatFlightClock(flightSeconds)}</span>
        </div>
        <BatteryGauge pct={battery} />
        {settings}
      </div>
    </header>
  );
}

export function formatFlightClock(seconds: number | undefined): string {
  if (seconds === undefined || !Number.isFinite(seconds) || seconds <= 0) {
    return "00:00";
  }
  const total = Math.floor(seconds);
  const mm = String(Math.floor(total / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

function BrandMark() {
  return (
    <span className="relative grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground shadow-[0_0_18px] shadow-primary/30">
      <svg viewBox="0 0 32 32" className="size-5" aria-hidden="true">
        <g fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
          <path d="M11 11l10 10M21 11L11 21" />
          <circle cx="8.5" cy="8.5" r="3.6" />
          <circle cx="23.5" cy="8.5" r="3.6" />
          <circle cx="8.5" cy="23.5" r="3.6" />
          <circle cx="23.5" cy="23.5" r="3.6" />
        </g>
        <rect x="13" y="13" width="6" height="6" rx="1.5" fill="currentColor" />
      </svg>
    </span>
  );
}
