import { formatCentimeters, getStateMetric } from "@/lib/format";
import { LIMITS } from "@/lib/limits";
import type { DashboardSnapshot } from "@/types";

type Row = [label: string, value: string];

export function SystemPanels({ snapshot, micActive }: { snapshot: DashboardSnapshot; micActive: boolean }) {
  const battery = getStateMetric<number | undefined>(snapshot.state, "battery_pct", undefined);
  const distance = getStateMetric<number | undefined>(snapshot.state, "distance_from_home_cm", undefined);
  const altitude = getStateMetric<number | undefined>(snapshot.state, "height_cm", undefined);
  const isFlying = getStateMetric<boolean>(snapshot.state, "is_flying", false);
  const linkLost = snapshot.state?.link_ok === false;
  const operatorHold = snapshot.state?.operator_hold === true;

  const groups: Array<{ title: string; rows: Row[] }> = [
    {
      title: "Connection",
      rows: [
        ["Dashboard", snapshot.connection === "connected" ? "Connected" : snapshot.connection === "connecting" ? "Connecting" : "Offline"],
        ["Video", snapshot.videoStatus.active ? (snapshot.videoStatus.source === "drone" ? "Drone camera" : "Webcam") : "No feed"],
        ["Telemetry", "Every second"]
      ]
    },
    {
      title: "AI",
      rows: [
        ["Agent", snapshot.mode === "live" ? "Gemini Live" : snapshot.mode === "legacy" ? "Legacy agent" : "Starting"],
        ["Recent actions", String(snapshot.tools.length)],
        ["Stored traces", String(snapshot.traces.length)]
      ]
    },
    {
      title: "Voice",
      rows: [
        ["Microphone", micActive ? "Recording" : "Idle"],
        ["Input format", "PCM16, 16 kHz"],
        ["Replies play on", "The computer running the agent"]
      ]
    },
    {
      title: "Safety limits",
      rows: [
        ["Flight", isFlying ? "Flying" : "Landed"],
        ["Drone link", linkLost ? "Lost" : "OK"],
        ["AI control", operatorHold ? "Paused by you" : "Allowed"],
        ["Battery", battery === undefined ? "—" : `${Math.round(battery)}%`],
        ["Distance from home", `${formatCentimeters(distance)} of ${LIMITS.geofenceRadiusCm} cm`],
        ["Altitude", `${formatCentimeters(altitude)} of ${LIMITS.maxAltitudeCm} cm`]
      ]
    }
  ];

  return (
    <div className="flex flex-col gap-6 p-4">
      {groups.map((group) => (
        <section key={group.title} aria-label={group.title}>
          <h3 className="text-[13px] font-semibold">{group.title}</h3>
          <dl className="mt-2 flex flex-col gap-1.5 text-[13px]">
            {group.rows.map(([label, value]) => (
              <div key={label} className="flex justify-between gap-4">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="text-end tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </div>
  );
}
