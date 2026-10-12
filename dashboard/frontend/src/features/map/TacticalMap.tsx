import { LIMITS } from "@/lib/limits";
import type { TrailPoint } from "@/lib/trail";
import { cn } from "@/lib/utils";

const R = LIMITS.geofenceRadiusCm;
const MAX_VIEW = R + 120;
const MIN_VIEW = 300;

/** Zoom so the whole flight so far fits, from ±3 m out to the full fence. */
export function mapViewRadius(points: Array<{ x: number; y: number }>): number {
  const furthest = points.reduce((max, p) => Math.max(max, Math.hypot(p.x, p.y)), 0);
  return Math.round(Math.min(MAX_VIEW, Math.max(MIN_VIEW, furthest * 1.4 + 120)));
}

/**
 * Top-down map centred on the takeoff point. "Up" is the takeoff heading
 * (heading 0°, +y), clockwise positive, matching core/state.py. Positions are
 * dead-reckoned by the server, so this is a plan view, not GPS.
 */
export function TacticalMap({
  x,
  y,
  heading = 0,
  trail,
  flying
}: {
  x: number;
  y: number;
  heading?: number;
  trail: TrailPoint[];
  flying: boolean;
}) {
  const distance = Math.hypot(x, y);
  const fenceRatio = distance / R;
  const fenceTone = fenceRatio >= 0.9 ? "danger" : fenceRatio >= 0.7 ? "warn" : "ok";
  const view = mapViewRadius([...trail, { x, y }]);
  // Sizes below are authored for the full-fence view; scale them with zoom.
  const k = view / MAX_VIEW;
  const ringStep = view <= 520 ? 100 : 200;
  const rings = Array.from({ length: Math.floor((R - 1) / ringStep) }, (_, i) => (i + 1) * ringStep).filter(
    (ring) => ring < view
  );
  // Keep the glyph on the map even if dead reckoning drifts past the view.
  const clamp = distance > view - 40 * k ? (view - 40 * k) / distance : 1;
  const px = x * clamp;
  const py = -y * clamp;
  const trailPath = trail.map((p) => `${p.x},${-p.y}`).join(" ");
  const fenceStroke =
    fenceTone === "danger" ? "var(--destructive)" : fenceTone === "warn" ? "var(--warning)" : "var(--hud)";

  return (
    <div className="relative mx-auto w-full max-w-[260px]">
      <svg
        viewBox={`${-view} ${-view} ${view * 2} ${view * 2}`}
        className="aspect-square w-full rounded-full bg-sunken/70 ring-1 ring-border"
        role="img"
        aria-label={`Top-down map: ${Math.round(distance)} cm from home, geofence ${R} cm`}
      >
        <defs>
          <radialGradient id="map-glow">
            <stop offset="0" stopColor="var(--hud)" stopOpacity="0.1" />
            <stop offset="1" stopColor="var(--hud)" stopOpacity="0" />
          </radialGradient>
          <linearGradient id="sweep" gradientTransform="rotate(90)">
            <stop offset="0" stopColor="var(--hud)" stopOpacity="0.2" />
            <stop offset="1" stopColor="var(--hud)" stopOpacity="0" />
          </linearGradient>
        </defs>

        <circle r={view} fill="url(#map-glow)" />

        {/* Range rings and crosshair axes */}
        {rings.map((ring) => (
          <circle key={ring} r={ring} fill="none" stroke="var(--hud)" strokeOpacity="0.16" strokeWidth={4 * k} />
        ))}
        <line x1={-view} x2={view} y1="0" y2="0" stroke="var(--hud)" strokeOpacity="0.12" strokeWidth={4 * k} />
        <line y1={-view} y2={view} x1="0" x2="0" stroke="var(--hud)" strokeOpacity="0.12" strokeWidth={4 * k} />
        {rings.map((ring) => (
          <text
            key={`l${ring}`}
            x={ring * Math.SQRT1_2 + 10 * k}
            y={-ring * Math.SQRT1_2 - 10 * k}
            fontSize={46 * k}
            fill="var(--hud)"
            fillOpacity="0.55"
            className="font-mono"
          >
            {ring / 100}m
          </text>
        ))}

        {/* Radar sweep while airborne */}
        {flying ? (
          <g className="radar-sweep">
            <path
              d={`M0 0 L0 ${-view} A${view} ${view} 0 0 1 ${view * Math.sin(Math.PI / 5)} ${-view * Math.cos(Math.PI / 5)} Z`}
              fill="url(#sweep)"
            />
          </g>
        ) : null}

        {/* Geofence (only once the view reaches it) */}
        {view > R ? (
          <circle
            r={R}
            fill="none"
            stroke={fenceStroke}
            strokeOpacity={fenceTone === "ok" ? 0.6 : 0.95}
            strokeWidth={8 * k}
            strokeDasharray={`${28 * k} ${16 * k}`}
          />
        ) : null}

        {/* Takeoff-heading marker */}
        <path d={`M0 ${-view + 24 * k} l${-22 * k} ${40 * k} h${44 * k} z`} fill="var(--hud)" fillOpacity="0.6" />

        {/* Trail */}
        {trail.length > 1 ? (
          <>
            <polyline
              points={trailPath}
              fill="none"
              stroke="var(--primary)"
              strokeOpacity="0.55"
              strokeWidth={8 * k}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            {trail.slice(1).map((p, i) => (
              <circle key={i} cx={p.x} cy={-p.y} r={10 * k} fill="var(--primary)" fillOpacity="0.8" />
            ))}
          </>
        ) : null}

        {/* Home pad */}
        <circle r={46 * k} fill="var(--card)" stroke="var(--foreground)" strokeOpacity="0.5" strokeWidth={6 * k} />
        <text y={17 * k} textAnchor="middle" fontSize={48 * k} fontWeight="700" fill="var(--foreground)" fillOpacity="0.75">
          H
        </text>

        {/* Drone */}
        <g style={{ transform: `translate(${px}px, ${py}px)`, transition: "transform 500ms ease-out" }}>
          {flying ? <circle r={90 * k} fill="var(--primary)" fillOpacity="0.14" className="animate-pulse" /> : null}
          <g style={{ transform: `rotate(${heading}deg) scale(${k})`, transition: "transform 500ms ease-out" }}>
            <path d="M0 -78 L52 52 L0 26 L-52 52 Z" fill="var(--primary)" stroke="var(--background)" strokeWidth="8" strokeLinejoin="round" />
          </g>
        </g>
      </svg>

      <div className="absolute top-0 right-0 font-mono text-[9px] text-muted-foreground tabular-nums">
        ±{(view / 100).toFixed(view < 1000 ? 1 : 0)} m
      </div>
      <div
        className={cn(
          "absolute bottom-0 left-0 rounded-md border bg-background/75 px-1.5 py-0.5 font-mono text-[10px] tabular-nums backdrop-blur",
          fenceTone === "danger"
            ? "border-destructive text-destructive"
            : fenceTone === "warn"
              ? "border-warning/60 text-warning"
              : "text-muted-foreground"
        )}
      >
        fence {Math.round(distance)}/{R}
      </div>
    </div>
  );
}

/** Bearing from the drone back to home, in the same frame as heading. */
export function bearingHome(x: number, y: number): number | undefined {
  if (Math.hypot(x, y) < 1) return undefined;
  const deg = (Math.atan2(-x, -y) * 180) / Math.PI;
  return (deg + 360) % 360;
}

function Readout({ label, value, unit, bar, tone }: { label: string; value: string; unit?: string; bar?: number; tone?: "warn" | "danger" }) {
  return (
    <div className="min-w-0">
      <p className="font-mono text-[9px] tracking-[0.16em] text-muted-foreground uppercase">{label}</p>
      <p
        className={cn(
          "mt-0.5 font-mono text-sm tabular-nums",
          tone === "danger" ? "text-destructive" : tone === "warn" ? "text-warning" : "text-foreground"
        )}
      >
        {value}
        {unit ? <span className="ml-0.5 text-[10px] text-muted-foreground">{unit}</span> : null}
      </p>
      {bar !== undefined ? (
        <div className="mt-1 h-1 overflow-hidden rounded-full bg-sunken">
          <div
            className={cn(
              "h-full rounded-full transition-[width] duration-500",
              tone === "danger" ? "bg-destructive" : tone === "warn" ? "bg-warning" : "bg-hud"
            )}
            style={{ width: `${Math.max(0, Math.min(1, bar)) * 100}%` }}
          />
        </div>
      ) : null}
    </div>
  );
}

/** Map plus the numbers a pilot glances at: range, bearing home, altitude. */
export function NavigationPanel({
  x,
  y,
  heading,
  height,
  trail,
  flying
}: {
  x: number;
  y: number;
  heading?: number;
  height?: number;
  trail: TrailPoint[];
  flying: boolean;
}) {
  const distance = Math.hypot(x, y);
  const home = bearingHome(x, y);
  const fence = distance / R;
  const alt = height ?? 0;
  const altRatio = alt / LIMITS.maxAltitudeCm;

  return (
    <div className="grid grid-cols-[minmax(0,1fr)_96px] items-center gap-3">
      <TacticalMap x={x} y={y} heading={heading} trail={trail} flying={flying} />
      <div className="space-y-2.5">
        <Readout
          label="Range"
          value={String(Math.round(distance))}
          unit="cm"
          bar={fence}
          tone={fence >= 0.9 ? "danger" : fence >= 0.7 ? "warn" : undefined}
        />
        <Readout
          label="Altitude"
          value={height === undefined ? "—" : String(Math.round(alt))}
          unit="cm"
          bar={altRatio}
          tone={altRatio >= 0.95 ? "danger" : altRatio >= 0.8 ? "warn" : undefined}
        />
        <Readout label="Heading" value={heading === undefined ? "—" : `${String(Math.round(((heading % 360) + 360) % 360)).padStart(3, "0")}°`} />
        <Readout label="Home" value={home === undefined ? "here" : `${String(Math.round(home)).padStart(3, "0")}°`} />
      </div>
    </div>
  );
}
