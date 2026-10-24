import type * as React from "react";

import { LIMITS } from "@/lib/limits";
import type { TrailPoint } from "@/lib/trail";
import { cn } from "@/lib/utils";

const R = LIMITS.geofenceRadiusCm;
const MAX_VIEW = R + 100;
const MIN_VIEW = 250;

/** Zoom so the whole flight so far fits, from ±2.5 m out to the full fence. */
export function mapViewRadius(points: Array<{ x: number; y: number }>): number {
  const furthest = points.reduce((max, p) => Math.max(max, Math.hypot(p.x, p.y)), 0);
  return Math.round(Math.min(MAX_VIEW, Math.max(MIN_VIEW, furthest * 1.4 + 100)));
}

/** Bearing from the drone back to home, in the same frame as heading. */
export function bearingHome(x: number, y: number): number | undefined {
  if (Math.hypot(x, y) < 1) return undefined;
  const deg = (Math.atan2(-x, -y) * 180) / Math.PI;
  return (deg + 360) % 360;
}

/**
 * Top-down plan centred on the takeoff point. Up is the takeoff heading
 * (heading 0°, +y), clockwise positive, matching core/state.py. Positions are
 * dead-reckoned by the server, so this is a sketch, not GPS.
 */
export function FlightMap({
  x,
  y,
  heading = 0,
  trail
}: {
  x: number;
  y: number;
  heading?: number;
  trail: TrailPoint[];
}) {
  const view = mapViewRadius([...trail, { x, y }]);
  // The map renders about 180 CSS px wide: one CSS px is roughly view / 90 units.
  const px = view / 90;
  const ringStep = view <= 500 ? 100 : 200;
  const rings = Array.from({ length: Math.floor(R / ringStep) }, (_, i) => (i + 1) * ringStep).filter((r) => r < view && r < R);
  const distance = Math.hypot(x, y);
  const clamp = distance > view - 12 * px ? (view - 12 * px) / distance : 1;

  return (
    <svg
      viewBox={`${-view} ${-view} ${view * 2} ${view * 2}`}
      className="aspect-square w-full max-w-[220px] rounded-lg bg-muted"
      role="img"
      aria-label={`Map: ${Math.round(distance)} cm from home, heading ${Math.round(heading)}°. The fence is ${R / 100} m from home.`}
    >
      {rings.map((r) => (
        <circle key={r} r={r} fill="none" stroke="var(--border)" strokeWidth={px} />
      ))}
      {rings.length > 0 ? (
        <text x={rings[0] + 3 * px} y={-3 * px} fontSize={12 * px} fill="var(--muted-foreground)">
          {rings[0] / 100} m
        </text>
      ) : null}

      {view > R ? (
        <>
          <circle r={R} fill="none" stroke="var(--destructive-text)" strokeWidth={1.5 * px} strokeDasharray={`${4 * px} ${4 * px}`} />
          <text y={-R - 5 * px} textAnchor="middle" fontSize={12 * px} fill="var(--destructive-text)">
            Fence
          </text>
        </>
      ) : null}

      {trail.length > 1 ? (
        <polyline
          points={trail.map((p) => `${p.x},${-p.y}`).join(" ")}
          fill="none"
          stroke="var(--primary)"
          strokeOpacity="0.45"
          strokeWidth={2 * px}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ) : null}

      <circle r={4 * px} fill="var(--card)" stroke="var(--foreground)" strokeWidth={1.5 * px} />

      <g
        className="motion-safe:transition-transform motion-safe:duration-300 motion-safe:ease-out"
        style={{ transform: `translate(${x * clamp}px, ${-y * clamp}px) rotate(${heading}deg)` }}
      >
        <path
          d={`M0 ${-9 * px} L${6.5 * px} ${7 * px} L0 ${3.5 * px} L${-6.5 * px} ${7 * px} Z`}
          fill="var(--primary)"
          stroke="var(--card)"
          strokeWidth={1.5 * px}
          strokeLinejoin="round"
        />
      </g>
    </svg>
  );
}

function Meter({ value, max, tone }: { value: number; max: number; tone: "default" | "warn" | "danger" }) {
  const ratio = Math.max(0, Math.min(1, value / max));
  return (
    <span className="mt-1.5 block h-1 overflow-hidden rounded-full bg-muted" aria-hidden="true">
      <span
        className={cn(
          "block h-full rounded-full motion-safe:transition-[width] motion-safe:duration-300",
          tone === "danger" ? "bg-destructive" : tone === "warn" ? "bg-warning" : "bg-primary"
        )}
        style={{ width: `${ratio * 100}%` }}
      />
    </span>
  );
}

function Datum({ label, children, meter }: { label: string; children: React.ReactNode; meter?: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[13px] text-muted-foreground">{label}</dt>
      <dd className="mt-0.5">
        <span className="block text-lg leading-snug font-semibold tabular-nums">{children}</span>
        {meter}
      </dd>
    </div>
  );
}

const unit = "ms-1 text-[13px] font-normal text-muted-foreground";

/** Where the drone is: the plan view plus the numbers you glance at. */
export function FlightData({
  x,
  y,
  heading,
  height,
  speed,
  trail
}: {
  x: number;
  y: number;
  heading?: number;
  height?: number;
  speed?: number;
  trail: TrailPoint[];
}) {
  const distance = Math.hypot(x, y);
  const home = bearingHome(x, y);
  const fenceRatio = distance / R;
  const altRatio = (height ?? 0) / LIMITS.maxAltitudeCm;
  const deg = (n: number) => `${String(Math.round(((n % 360) + 360) % 360)).padStart(3, "0")}°`;

  return (
    <section
      aria-labelledby="flight-data-title"
      className="surface grid gap-5 p-4 sm:grid-cols-[minmax(120px,180px)_minmax(0,1fr)] sm:items-center"
    >
      <h2 id="flight-data-title" className="sr-only">
        Flight data
      </h2>
      <FlightMap x={x} y={y} heading={heading} trail={trail} />
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 md:grid-cols-3">
        <Datum
          label="Altitude"
          meter={<Meter value={height ?? 0} max={LIMITS.maxAltitudeCm} tone={altRatio >= 0.95 ? "danger" : altRatio >= 0.8 ? "warn" : "default"} />}
        >
          {height === undefined ? "—" : Math.round(height)}
          <span className={unit}>of {LIMITS.maxAltitudeCm} cm</span>
        </Datum>
        <Datum
          label="From home"
          meter={<Meter value={distance} max={R} tone={fenceRatio >= 0.9 ? "danger" : fenceRatio >= 0.7 ? "warn" : "default"} />}
        >
          {Math.round(distance)}
          <span className={unit}>of {R} cm</span>
        </Datum>
        <Datum label="Speed">
          {speed === undefined ? "—" : Math.round(speed)}
          <span className={unit}>cm/s</span>
        </Datum>
        <Datum label="Heading">{heading === undefined ? "—" : deg(heading)}</Datum>
        <Datum label="Way home">{home === undefined ? "You're home" : deg(home)}</Datum>
        <Datum label="Position">
          {Math.round(x)}, {Math.round(y)}
          <span className={unit}>cm</span>
        </Datum>
      </dl>
    </section>
  );
}
