import { Cpu, Radio, ShieldCheck, Volume2 } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { DashboardSnapshot } from "@/types";
import { formatCentimeters, getStateMetric } from "@/lib/format";

export function SystemPanels({ snapshot, micActive }: { snapshot: DashboardSnapshot; micActive: boolean }) {
  const battery = getStateMetric<number | undefined>(snapshot.state, "battery_pct", undefined);
  const distance = getStateMetric<number | undefined>(snapshot.state, "distance_from_home_cm", undefined);
  const altitude = getStateMetric<number | undefined>(snapshot.state, "height_cm", undefined);
  const isFlying = getStateMetric<boolean>(snapshot.state, "is_flying", false);

  return (
    <div className="space-y-3 p-3">
      <ReadoutBlock
        icon={Radio}
        title="Transport"
        rows={[
          ["Socket", snapshot.connection],
          ["Frames", snapshot.videoStatus.active ? snapshot.videoStatus.source ?? "active" : "inactive"],
          ["Telemetry", "2s server push"]
        ]}
        good={["connected", "webcam", "drone"]}
      />
      <ReadoutBlock
        icon={Cpu}
        title="Agent"
        rows={[
          ["Mode", snapshot.mode],
          ["Route", snapshot.mode === "live" ? "Gemini Live" : "legacy loop"],
          ["Tools", `${snapshot.tools.length} recent`],
          ["Traces", `${snapshot.traces.length} stored`]
        ]}
        good={["live"]}
      />
      <ReadoutBlock
        icon={Volume2}
        title="Voice"
        rows={[
          ["Mic", micActive ? "recording" : "idle"],
          ["Input", "PCM16 · 16 kHz"],
          ["Reply", "host speaker"]
        ]}
        good={["recording"]}
      />
      <ReadoutBlock
        icon={ShieldCheck}
        title="Safety Envelope"
        rows={[
          ["Flight", isFlying ? "airborne" : "grounded"],
          ["Battery", battery === undefined ? "—" : `${Math.round(battery)}%`],
          ["Geofence", `${formatCentimeters(distance)} / 800 cm`],
          ["Altitude", `${formatCentimeters(altitude)} / 200 cm`]
        ]}
        good={["airborne"]}
      />
    </div>
  );
}

function ReadoutBlock({
  icon: Icon,
  title,
  rows,
  good = []
}: {
  icon: LucideIcon;
  title: string;
  rows: Array<[string, string]>;
  good?: string[];
}) {
  return (
    <section className="overflow-hidden rounded-lg border">
      <header className="flex items-center gap-2 border-b px-3 py-2 text-sm font-medium">
        <Icon className="size-4 text-muted-foreground" />
        {title}
      </header>
      <dl className="grid gap-2 p-3 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-3">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="min-w-0">
              <Badge variant={good.includes(value) ? "default" : "secondary"} className="font-normal">
                {value}
              </Badge>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
