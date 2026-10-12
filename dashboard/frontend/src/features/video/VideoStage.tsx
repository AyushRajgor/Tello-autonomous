import { VideoOff, WifiOff } from "lucide-react";

import { LIMITS } from "@/lib/limits";
import { cn } from "@/lib/utils";
import type { VideoStatus } from "@/types";

export type HudTelemetry = {
  heading?: number;
  height?: number;
  speed?: number;
  x: number;
  y: number;
  z: number;
  flying: boolean;
};

/**
 * The camera feed with an avionics HUD drawn over it: heading tape, altitude
 * ladder (with the 2 m ceiling), reticle and corner readouts. When there is no
 * picture, a receding grid stands in so the HUD still has something to sit on.
 */
export function VideoStage({
  frameSrc,
  videoStatus,
  cameraOn = true,
  fps,
  latencyMs,
  telemetry,
  linkLost = false,
  showHud = true
}: {
  frameSrc?: string;
  videoStatus: VideoStatus;
  cameraOn?: boolean;
  fps?: number | null;
  latencyMs?: number | null;
  telemetry: HudTelemetry;
  linkLost?: boolean;
  showHud?: boolean;
}) {
  const hasPicture = cameraOn && Boolean(frameSrc);
  const sourceLabel = !cameraOn ? "Camera off" : videoStatus.active ? (videoStatus.source ?? "video") : "No signal";

  return (
    <section
      aria-label="Mission view"
      className="panel relative isolate min-h-[280px] flex-1 overflow-hidden bg-sunken max-lg:aspect-[4/3] max-lg:min-h-0 max-lg:flex-none"
    >
      {hasPicture ? (
        <img src={frameSrc} alt="Live camera frame" className="absolute inset-0 size-full object-contain" />
      ) : (
        <div className="viewport-void absolute inset-0 overflow-hidden">
          <div className="viewport-floor" />
          <div className="absolute inset-x-0 top-[38%] flex flex-col items-center gap-2 text-center">
            <VideoOff className="size-7 text-hud/50" />
            <p className="font-mono text-[11px] tracking-[0.2em] text-hud/80 uppercase">
              {!cameraOn ? "Camera off" : videoStatus.active ? "Acquiring frame" : "No video signal"}
            </p>
            {cameraOn && !videoStatus.active ? (
              <p className="max-w-[280px] px-4 text-xs text-muted-foreground max-sm:hidden">
                Start the agent with{" "}
                <code className="rounded bg-muted px-1 py-0.5 font-mono text-foreground">--webcam</code> or connect the
                Tello to stream video.
              </p>
            ) : null}
          </div>
        </div>
      )}

      {hasPicture ? <div className="scanlines pointer-events-none absolute inset-0" /> : null}

      {showHud ? (
        <div className="pointer-events-none absolute inset-0">
          <HeadingTape heading={telemetry.heading} />
          <AltitudeLadder height={telemetry.height} />
          {hasPicture ? <Reticle flying={telemetry.flying} /> : null}
        </div>
      ) : null}

      {/* Corner readouts */}
      <div className="pointer-events-none absolute inset-x-3 top-3 flex items-start justify-between gap-3 font-mono text-[11px] tabular-nums">
        <div className="flex items-center gap-2 rounded-md border border-white/10 bg-black/50 px-2 py-1 text-white/90 backdrop-blur-sm">
          <span className={cn("size-1.5 rounded-full", hasPicture ? "animate-pulse bg-destructive" : "bg-white/35")} />
          <span className="tracking-[0.14em] uppercase">{hasPicture ? `Live · ${sourceLabel}` : sourceLabel}</span>
        </div>
        {fps != null || latencyMs != null ? (
          <div className="flex items-center gap-3 rounded-md border border-white/10 bg-black/50 px-2 py-1 text-white/90 backdrop-blur-sm">
            {fps != null ? (
              <span>
                <span className="text-white/55">FPS</span> {fps}
              </span>
            ) : null}
            {latencyMs != null ? (
              <span>
                <span className="text-white/55">LAT</span> {latencyMs} ms
              </span>
            ) : null}
          </div>
        ) : null}
      </div>

      {showHud ? (
        <div className="hud-ink pointer-events-none absolute inset-x-3 bottom-3 flex items-end justify-between gap-3 font-mono text-[11px] tabular-nums">
          <div className="flex flex-col gap-0.5">
            <span className="text-[9px] tracking-[0.18em] opacity-75">POS · CM</span>
            <span>
              X {signed(telemetry.x)} Y {signed(telemetry.y)} Z {signed(telemetry.z)}
            </span>
          </div>
          <div className="flex flex-col items-end gap-0.5 pr-14">
            <span className="text-[9px] tracking-[0.18em] opacity-75">GND SPD</span>
            <span>{telemetry.speed === undefined ? "—" : `${Math.round(telemetry.speed)} cm/s`}</span>
          </div>
        </div>
      ) : null}

      {linkLost ? (
        <div role="alert" className="absolute inset-0 z-10 grid place-items-center bg-black/55 p-4 backdrop-blur-[2px]">
          <div className="w-full max-w-md overflow-hidden rounded-xl border-2 border-destructive bg-background/90 shadow-2xl">
            <div className="hazard h-3" />
            <div className="flex items-start gap-3 p-4">
              <WifiOff className="mt-0.5 size-7 shrink-0 animate-pulse text-destructive" />
              <div>
                <p className="text-lg font-bold tracking-[0.2em] text-destructive">LINK LOST</p>
                <p className="mt-1 text-sm text-pretty text-muted-foreground">
                  No telemetry from the drone. The Tello lands itself after 15 s without commands — keep clear of it.
                </p>
              </div>
            </div>
            <div className="hazard h-3" />
          </div>
        </div>
      ) : null}
    </section>
  );
}

/** `+050` / `−120`: fixed-width so the readout doesn't jitter. */
export function signed(n: number): string {
  const r = Math.round(n);
  return `${r < 0 ? "−" : "+"}${String(Math.abs(r)).padStart(3, "0")}`;
}

export function normalizeHeading(deg: number): number {
  return ((Math.round(deg) % 360) + 360) % 360;
}

const CARDINALS: Record<number, string> = { 0: "N", 90: "E", 180: "S", 270: "W" };

/** A compass tape spanning ±60° around the current heading. */
function HeadingTape({ heading }: { heading?: number }) {
  const h = heading ?? 0;
  const span = 60;
  const width = 360;
  const scale = width / (span * 2);
  const ticks: Array<{ deg: number; x: number }> = [];
  for (let deg = Math.ceil((h - span) / 10) * 10; deg <= h + span; deg += 10) {
    ticks.push({ deg: normalizeHeading(deg), x: width / 2 + (deg - h) * scale });
  }

  return (
    <div className="absolute top-12 left-1/2 w-[min(56%,420px)] -translate-x-1/2">
      <svg viewBox={`0 0 ${width} 38`} className="hud-stroke w-full overflow-visible text-hud" aria-hidden="true">
        <defs>
          <linearGradient id="tape-fade" x1="0" x2="1">
            <stop offset="0" stopColor="white" stopOpacity="0" />
            <stop offset="0.2" stopColor="white" stopOpacity="1" />
            <stop offset="0.8" stopColor="white" stopOpacity="1" />
            <stop offset="1" stopColor="white" stopOpacity="0" />
          </linearGradient>
          <mask id="tape-mask">
            <rect width={width} height="38" fill="url(#tape-fade)" />
          </mask>
        </defs>
        <g mask="url(#tape-mask)" fill="currentColor" stroke="currentColor">
          <line x1="0" x2={width} y1="30" y2="30" strokeWidth="1" opacity="0.5" />
          {ticks.map(({ deg, x }) => {
            const major = deg % 30 === 0;
            return (
              <g key={x.toFixed(1)}>
                <line x1={x} x2={x} y1={major ? 20 : 25} y2="30" strokeWidth={major ? 1.5 : 1} />
                {major ? (
                  <text
                    x={x}
                    y="15"
                    textAnchor="middle"
                    stroke="none"
                    fontSize="10"
                    fontWeight={CARDINALS[deg] ? 700 : 400}
                    className="font-mono"
                  >
                    {CARDINALS[deg] ?? String(deg / 10).padStart(2, "0")}
                  </text>
                ) : null}
              </g>
            );
          })}
        </g>
        <path d={`M${width / 2 - 5} 38 L${width / 2} 32 L${width / 2 + 5} 38 Z`} fill="currentColor" />
      </svg>
      <div className="hud-ink absolute top-full left-1/2 mt-1 -translate-x-1/2 rounded-sm border border-hud/70 bg-black/55 px-1.5 font-mono text-[11px] leading-4 tabular-nums">
        HDG {String(normalizeHeading(h)).padStart(3, "0")}°
      </div>
    </div>
  );
}

/** Vertical altitude scale up to just above the ceiling, with min/max marks. */
function AltitudeLadder({ height }: { height?: number }) {
  const top = LIMITS.maxAltitudeCm + 40;
  const h = Math.max(0, Math.min(top, height ?? 0));
  const y = (cm: number) => 8 + (1 - cm / top) * 184;
  const ticks = Array.from({ length: Math.floor(top / 20) + 1 }, (_, i) => i * 20);

  return (
    <div className="absolute top-1/2 right-3 h-[min(56%,280px)] -translate-y-1/2">
      <svg viewBox="0 0 56 200" className="hud-stroke h-full overflow-visible text-hud" aria-hidden="true">
        <line x1="40" x2="40" y1="8" y2="192" stroke="currentColor" strokeOpacity="0.5" />
        {ticks.map((cm) => (
          <g key={cm}>
            <line x1={cm % 100 === 0 ? 32 : 36} x2="40" y1={y(cm)} y2={y(cm)} stroke="currentColor" />
            {cm % 50 === 0 ? (
              <text x="29" y={y(cm) + 3} textAnchor="end" fontSize="8" fill="currentColor" className="font-mono">
                {cm}
              </text>
            ) : null}
          </g>
        ))}
        <line
          x1="30"
          x2="56"
          y1={y(LIMITS.maxAltitudeCm)}
          y2={y(LIMITS.maxAltitudeCm)}
          stroke="var(--destructive)"
          strokeWidth="1.5"
          strokeDasharray="3 2"
        />
        <text x="56" y={y(LIMITS.maxAltitudeCm) - 3} textAnchor="end" fontSize="7" fill="var(--destructive)" className="font-mono">
          CEIL
        </text>
        <line
          x1="34"
          x2="56"
          y1={y(LIMITS.minSafeAltitudeCm)}
          y2={y(LIMITS.minSafeAltitudeCm)}
          stroke="var(--warning)"
          strokeDasharray="2 2"
        />
        <g style={{ transform: `translateY(${y(h)}px)`, transition: "transform 400ms ease-out" }}>
          <path d="M40 0 L46 -6 H56 V6 H46 Z" fill="currentColor" />
        </g>
      </svg>
      <span className="hud-ink absolute top-full right-0 mt-1 font-mono text-[10px] whitespace-nowrap tabular-nums">
        ALT {height === undefined ? "—" : Math.round(height)}
      </span>
    </div>
  );
}

function Reticle({ flying }: { flying: boolean }) {
  return (
    <svg
      viewBox="0 0 120 120"
      className={cn(
        "hud-stroke absolute top-1/2 left-1/2 size-24 -translate-x-1/2 -translate-y-1/2 text-hud transition-opacity",
        flying ? "opacity-90" : "opacity-40"
      )}
      aria-hidden="true"
    >
      <g fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M20 40 V20 H40 M80 20 H100 V40 M100 80 V100 H80 M40 100 H20 V80" />
        <path d="M60 50 V56 M60 64 V70 M50 60 H56 M64 60 H70" />
      </g>
      <circle cx="60" cy="60" r="1.6" fill="currentColor" />
    </svg>
  );
}
