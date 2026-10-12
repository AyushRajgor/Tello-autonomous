import { Cpu, Radio, ShieldCheck, Volume2 } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { formatCentimeters, getStateMetric } from "@/lib/format";
import { LIMITS } from "@/lib/limits";
import { cn } from "@/lib/utils";
import type { DashboardSnapshot } from "@/types";

type Row = { label: string; value: string; state?: "good" | "bad" | "warn" };

export function SystemPanels({ snapshot, micActive }: { snapshot: DashboardSnapshot; micActive: boolean }) {
  const battery = getStateMetric<number | undefined>(snapshot.state, "battery_pct", undefined);
  const distance = getStateMetric<number | undefined>(snapshot.state, "distance_from_home_cm", undefined);
  const altitude = getStateMetric<number | undefined>(snapshot.state, "height_cm", undefined);
  const isFlying = getStateMetric<boolean>(snapshot.state, "is_flying", false);
  const linkLost = snapshot.state?.link_ok === false;
  const operatorHold = snapshot.state?.operator_hold === true;
  const connected = snapshot.connection === "connected";

  return (
    <div className="grid gap-3 p-3 sm:grid-cols-2 lg:grid-cols-1">
      <ReadoutBlock
        icon={Radio}
        title="Transport"
        rows={[
          { label: "Socket", value: snapshot.connection, state: connected ? "good" : "bad" },
          {
            label: "Frames",
            value: snapshot.videoStatus.active ? (snapshot.videoStatus.source ?? "active") : "inactive",
            state: snapshot.videoStatus.active ? "good" : undefined
          },
          { label: "Telemetry", value: "1 s server push" }
        ]}
      />
      <ReadoutBlock
        icon={Cpu}
        title="Agent"
        rows={[
          { label: "Mode", value: snapshot.mode, state: snapshot.mode === "live" ? "good" : undefined },
          { label: "Route", value: snapshot.mode === "live" ? "Gemini Live" : "legacy loop" },
          { label: "Tools", value: `${snapshot.tools.length} recent` },
          { label: "Traces", value: `${snapshot.traces.length} stored` }
        ]}
      />
      <ReadoutBlock
        icon={Volume2}
        title="Voice"
        rows={[
          { label: "Mic", value: micActive ? "recording" : "idle", state: micActive ? "good" : undefined },
          { label: "Input", value: "PCM16 · 16 kHz" },
          { label: "Reply", value: "host speaker" }
        ]}
      />
      <ReadoutBlock
        icon={ShieldCheck}
        title="Safety envelope"
        rows={[
          { label: "Flight", value: isFlying ? "airborne" : "grounded", state: isFlying ? "good" : undefined },
          { label: "Drone link", value: linkLost ? "lost" : "ok", state: linkLost ? "bad" : "good" },
          { label: "AI control", value: operatorHold ? "paused by operator" : "enabled", state: operatorHold ? "warn" : "good" },
          { label: "Battery", value: battery === undefined ? "—" : `${Math.round(battery)}%` },
          { label: "Geofence", value: `${formatCentimeters(distance)} / ${LIMITS.geofenceRadiusCm} cm` },
          { label: "Altitude", value: `${formatCentimeters(altitude)} / ${LIMITS.maxAltitudeCm} cm` }
        ]}
      />
    </div>
  );
}

const stateDot = { good: "bg-success", bad: "bg-destructive", warn: "bg-warning" } as const;

function ReadoutBlock({ icon: Icon, title, rows }: { icon: LucideIcon; title: string; rows: Row[] }) {
  return (
    <section className="overflow-hidden rounded-lg border bg-sunken/50">
      <header className="flex items-center gap-2 border-b px-3 py-2">
        <Icon className="size-3.5 text-hud" />
        <h3 className="font-mono text-[10px] tracking-[0.16em] text-muted-foreground uppercase">{title}</h3>
      </header>
      <dl className="divide-y divide-border/60">
        {rows.map(({ label, value, state }) => (
          <div key={label} className="flex items-center justify-between gap-3 px-3 py-1.5 text-xs">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="flex min-w-0 items-center gap-1.5 font-mono text-[11px]">
              {state ? <span className={cn("size-1.5 rounded-full", stateDot[state])} /> : null}
              <span className="truncate">{value}</span>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
