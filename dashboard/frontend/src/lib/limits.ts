/**
 * Mirror of the server's SAFETY thresholds (config.py). The server enforces
 * them; the dashboard only draws them (geofence ring, ceiling, battery bands).
 */
export const LIMITS = {
  geofenceRadiusCm: 800,
  maxAltitudeCm: 200,
  minSafeAltitudeCm: 20,
  batteryWarnPct: 20,
  batteryCriticalPct: 10,
  flipMinBatteryPct: 50,
  minMoveCm: 20,
  maxMoveCm: 100
} as const;

export type BatteryTone = "unknown" | "ok" | "warn" | "critical";

export function batteryTone(pct: number | undefined): BatteryTone {
  if (pct === undefined) return "unknown";
  if (pct <= LIMITS.batteryCriticalPct) return "critical";
  if (pct <= LIMITS.batteryWarnPct) return "warn";
  return "ok";
}
