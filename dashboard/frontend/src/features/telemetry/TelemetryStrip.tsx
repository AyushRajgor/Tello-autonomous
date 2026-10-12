import * as React from "react";

import { cn } from "@/lib/utils";
import {
  formatCentimeters,
  formatDegrees,
  formatSeconds,
  getPositionParts,
  getStateMetric,
  type DashboardState
} from "@/lib/format";

type Tone = "default" | "success" | "warning" | "danger";

const toneClass: Record<Tone, string> = {
  default: "text-foreground",
  success: "text-success",
  warning: "text-warning",
  danger: "text-destructive"
};

function Tile({ label, value, tone = "default" }: { label: string; value: React.ReactNode; tone?: Tone }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 px-4 py-3 not-first:border-l">
      <span className="text-[11px] tracking-wide text-muted-foreground">{label}</span>
      <span className={cn("truncate font-mono text-[15px] font-medium tabular-nums", toneClass[tone])}>{value}</span>
    </div>
  );
}

export function TelemetryStrip({ state }: { state?: DashboardState }) {
  const position = getPositionParts(state);
  const battery = getStateMetric<number | undefined>(state, "battery_pct", undefined);
  const isFlying = getStateMetric<boolean>(state, "is_flying", false);
  const distance = getStateMetric<number | undefined>(state, "distance_from_home_cm", undefined);
  const heading = getStateMetric<number | undefined>(state, "heading_deg", undefined);
  const height = getStateMetric<number | undefined>(state, "height_cm", position.z);
  const speed = getStateMetric<number | undefined>(state, "speed_cms", undefined);
  const duration = getStateMetric<number | undefined>(state, "flight_duration_s", undefined);
  const linkLost = state?.link_ok === false;

  const batteryTone: Tone =
    battery === undefined ? "default" : battery <= 10 ? "danger" : battery <= 20 ? "warning" : "success";

  return (
    <div className="grid shrink-0 grid-cols-8 overflow-hidden rounded-xl border bg-card max-[1100px]:grid-cols-4 max-[560px]:grid-cols-2">
      <Tile
        label="Position"
        value={`${Math.round(position.x)}, ${Math.round(position.y)}, ${Math.round(position.z)}`}
      />
      <Tile label="Heading" value={formatDegrees(heading)} />
      <Tile label="Distance" value={formatCentimeters(distance)} />
      <Tile label="Altitude" value={formatCentimeters(height)} />
      <Tile label="Battery" value={battery === undefined ? "—" : `${Math.round(battery)}%`} tone={batteryTone} />
      <Tile label="Speed" value={speed === undefined ? "—" : `${Math.round(speed)} cm/s`} />
      <Tile
        label="Status"
        value={linkLost ? "Link lost" : isFlying ? "Airborne" : "Grounded"}
        tone={linkLost ? "danger" : isFlying ? "success" : "default"}
      />
      <Tile label="Time" value={formatSeconds(duration)} />
    </div>
  );
}
