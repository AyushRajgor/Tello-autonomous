import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  ChevronsDown,
  ChevronsUp,
  Eye,
  OctagonX,
  PlaneLanding,
  PlaneTakeoff,
  RotateCcw,
  RotateCw,
  Sparkles,
  Zap
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { LIMITS } from "@/lib/limits";
import { cn } from "@/lib/utils";
import type { MoveDirection, OperatorArgs, OperatorSender } from "@/types";

const STEP_OPTIONS = [20, 50, 100] as const;
const TURN_OPTIONS = [45, 90, 180] as const;
const DEFAULT_STEP_CM = 50;
const DEFAULT_TURN_DEG = 90;
const ESTOP_CONFIRM_WINDOW_MS = 5000;

export function Kbd({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <kbd
      className={cn(
        "rounded border border-current/30 px-1.5 py-0.5 font-mono text-[10px] leading-none font-medium opacity-80",
        className
      )}
    >
      {children}
    </kbd>
  );
}

/**
 * Always-visible safety controls: STOP (hover), Land and E-STOP.
 * These go straight to the drone — the AI is never in the loop.
 */
export function EmergencyControls({
  onOperator,
  connected = true
}: {
  onOperator: OperatorSender;
  connected?: boolean;
}) {
  return (
    <div className="space-y-2">
      {!connected ? (
        <p role="status" className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          Console offline — controls can't reach the drone until the socket reconnects.
        </p>
      ) : null}

      <button
        type="button"
        onClick={() => onOperator("hover")}
        aria-label="STOP — hover in place"
        aria-keyshortcuts="Space"
        className="group relative flex h-[68px] w-full items-center justify-center gap-3 overflow-hidden rounded-xl bg-destructive text-white shadow-[0_8px_30px_-8px] shadow-destructive/60 outline-none transition-[transform,background-color] hover:bg-destructive/90 focus-visible:ring-[3px] focus-visible:ring-destructive/50 active:scale-[0.985]"
      >
        <span className="absolute inset-x-0 top-0 h-px bg-white/30" />
        <OctagonX className="size-8" strokeWidth={2.25} />
        <span className="flex flex-col items-start leading-none">
          <span className="text-2xl font-bold tracking-[0.22em]">STOP</span>
          <span className="mt-1 text-[11px] font-medium tracking-wide text-white/80">Hover in place · pause AI</span>
        </span>
        <Kbd className="absolute right-3 bottom-2.5 border-white/40 text-white">Space</Kbd>
      </button>

      <div className="grid grid-cols-2 gap-2">
        <Button
          variant="secondary"
          onClick={() => onOperator("land")}
          aria-label="Land now"
          aria-keyshortcuts="Escape"
          className="h-11 bg-raised hover:bg-accent"
        >
          <PlaneLanding />
          Land
          <Kbd>Esc</Kbd>
        </Button>
        <EmergencyStop onConfirm={() => onOperator("emergency_stop")} />
      </div>
    </div>
  );
}

/** E-STOP needs a second, explicit click: it cuts the motors mid-air. */
function EmergencyStop({ onConfirm }: { onConfirm: () => void }) {
  const [armed, setArmed] = React.useState(false);
  const titleId = React.useId();
  const descId = React.useId();

  React.useEffect(() => {
    if (!armed) {
      return;
    }
    const timer = setTimeout(() => setArmed(false), ESTOP_CONFIRM_WINDOW_MS);
    return () => clearTimeout(timer);
  }, [armed]);

  if (!armed) {
    return (
      <Button
        variant="outline"
        onClick={() => setArmed(true)}
        aria-label="E-STOP — cut motors, drone falls"
        className="h-11 border-destructive/50 bg-transparent text-destructive hover:bg-destructive/10 hover:text-destructive dark:border-destructive/50 dark:bg-transparent"
      >
        <Zap />
        E-STOP
      </Button>
    );
  }

  return (
    <div
      role="group"
      aria-labelledby={titleId}
      aria-describedby={descId}
      className="col-span-2 space-y-2 rounded-lg border border-destructive bg-destructive/10 p-3 animate-in fade-in zoom-in-95"
    >
      <p id={titleId} className="text-sm font-semibold text-destructive">
        Cut all motors? The drone will fall.
      </p>
      <p id={descId} className="text-xs text-muted-foreground">
        Use only if the drone is about to hit someone. STOP or Land are safer in every other case.
      </p>
      <div className="grid grid-cols-2 gap-2">
        <Button
          variant="destructive"
          className="dark:bg-destructive"
          onClick={() => {
            setArmed(false);
            onConfirm();
          }}
        >
          <Zap />
          Cut motors
        </Button>
        <Button variant="outline" onClick={() => setArmed(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/** Thumb-reach STOP / Land for phones and tablets, pinned to the bottom edge. */
export function QuickSafetyBar({ onOperator }: { onOperator: OperatorSender }) {
  return (
    <div className="fixed inset-x-0 bottom-0 z-40 border-t bg-background/90 px-3 pt-2.5 pb-[max(0.625rem,env(safe-area-inset-bottom))] backdrop-blur-md lg:hidden">
      <div className="mx-auto grid max-w-xl grid-cols-[1fr_auto] gap-2">
        <button
          type="button"
          onClick={() => onOperator("hover")}
          aria-label="Quick STOP — hover in place"
          className="flex h-12 items-center justify-center gap-2 rounded-xl bg-destructive text-lg font-bold tracking-[0.2em] text-white shadow-lg shadow-destructive/30 active:scale-[0.98]"
        >
          <OctagonX className="size-6" />
          STOP
        </button>
        <Button variant="secondary" onClick={() => onOperator("land")} aria-label="Quick land" className="h-12 bg-raised px-5">
          <PlaneLanding />
          Land
        </Button>
      </div>
    </div>
  );
}

type StickControl = {
  slot: "n" | "e" | "s" | "w";
  ariaLabel: string;
  icon: LucideIcon;
  action: "move" | "rotate";
  args: OperatorArgs;
};

function moveControl(slot: StickControl["slot"], direction: MoveDirection, cm: number, icon: LucideIcon): StickControl {
  return { slot, icon, action: "move", args: { direction, cm }, ariaLabel: `Move ${direction} ${cm} cm` };
}

function turnControl(slot: StickControl["slot"], degrees: number, icon: LucideIcon): StickControl {
  const side = degrees < 0 ? "left" : "right";
  return { slot, icon, action: "rotate", args: { degrees }, ariaLabel: `Turn ${side} ${Math.abs(degrees)}°` };
}

const slotPosition: Record<StickControl["slot"], string> = {
  n: "col-start-2 row-start-1",
  w: "col-start-1 row-start-2",
  e: "col-start-3 row-start-2",
  s: "col-start-2 row-start-3"
};

/** One RC-transmitter style stick: four nudges around a centre cap. */
function Stick({
  title,
  caption,
  controls,
  onOperator,
  disabled
}: {
  title: string;
  caption: string;
  controls: StickControl[];
  onOperator: OperatorSender;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-col items-center gap-2">
      <div className="relative grid size-[124px] grid-cols-3 grid-rows-3 place-items-center rounded-full border bg-sunken p-1.5 shadow-[inset_0_2px_10px_oklch(0_0_0/45%)]">
        <span className="pointer-events-none absolute inset-[30%] rounded-full border border-hud/25 bg-raised shadow-[0_2px_6px_oklch(0_0_0/40%)]" />
        <span className="pointer-events-none absolute inset-[45%] rounded-full bg-hud/40" />
        {controls.map(({ slot, ariaLabel, icon: Icon, action, args }) => (
          <button
            key={ariaLabel}
            type="button"
            aria-label={ariaLabel}
            title={ariaLabel}
            disabled={disabled}
            onClick={() => onOperator(action, args)}
            className={cn(
              "relative z-10 grid size-9 place-items-center rounded-full text-muted-foreground outline-none transition-colors hover:bg-hud/15 hover:text-hud focus-visible:ring-2 focus-visible:ring-ring active:bg-hud/25 disabled:pointer-events-none disabled:opacity-40",
              slotPosition[slot]
            )}
          >
            <Icon className="size-5" />
          </button>
        ))}
      </div>
      <div className="text-center leading-tight">
        <p className="font-mono text-[10px] tracking-[0.16em] text-muted-foreground uppercase">{title}</p>
        <p className="text-[11px] text-muted-foreground/80">{caption}</p>
      </div>
    </div>
  );
}

function Segmented<T extends number>({
  label,
  unit,
  value,
  options,
  onChange
}: {
  label: string;
  unit: string;
  value: T;
  options: readonly T[];
  onChange: (value: T) => void;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex items-center gap-2">
      <span className="font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase">{label}</span>
      <div className="flex rounded-md border bg-sunken p-0.5">
        {options.map((option) => (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={option === value}
            onClick={() => onChange(option)}
            className={cn(
              "rounded-[5px] px-2 py-0.5 font-mono text-[11px] tabular-nums outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
              option === value ? "bg-raised text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
            )}
          >
            {option}
            {unit}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Manual flight. Everything here except "Observe" bypasses the AI. */
export function FlightControls({
  onOperator,
  onCommand,
  flying = false,
  battery
}: {
  onOperator: OperatorSender;
  onCommand: (command: string, keepHold?: boolean) => void;
  flying?: boolean;
  battery?: number;
}) {
  const [step, setStep] = React.useState<(typeof STEP_OPTIONS)[number]>(DEFAULT_STEP_CM);
  const [turn, setTurn] = React.useState<(typeof TURN_OPTIONS)[number]>(DEFAULT_TURN_DEG);
  const flipBlocked = battery !== undefined && battery <= LIMITS.flipMinBatteryPct;

  const leftStick: StickControl[] = [
    moveControl("n", "up", step, ChevronsUp),
    moveControl("s", "down", step, ChevronsDown),
    turnControl("w", -turn, RotateCcw),
    turnControl("e", turn, RotateCw)
  ];
  const rightStick: StickControl[] = [
    moveControl("n", "forward", step, ArrowUp),
    moveControl("s", "back", step, ArrowDown),
    moveControl("w", "left", step, ArrowLeft),
    moveControl("e", "right", step, ArrowRight)
  ];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Segmented label="Step" unit="" value={step} options={STEP_OPTIONS} onChange={setStep} />
        <Segmented label="Turn" unit="°" value={turn} options={TURN_OPTIONS} onChange={setTurn} />
      </div>

      <div className="flex items-start justify-around gap-2">
        <Stick title="Throttle · Yaw" caption="climb, descend, turn" controls={leftStick} onOperator={onOperator} />
        <Stick title="Pitch · Roll" caption={`${step} cm per nudge`} controls={rightStick} onOperator={onOperator} />
      </div>

      <div className="grid grid-cols-[1.3fr_1fr_1fr] gap-2">
        <Button
          onClick={() => onOperator("takeoff")}
          disabled={flying}
          className="h-10 font-semibold"
          aria-label={flying ? "Airborne" : "Takeoff"}
        >
          <PlaneTakeoff />
          {flying ? "Airborne" : "Takeoff"}
        </Button>
        <Button
          variant="outline"
          onClick={() => onOperator("flip", { direction: "forward" })}
          title={flipBlocked ? `Flip needs more than ${LIMITS.flipMinBatteryPct}% battery` : "Flip forward"}
          className="h-10"
        >
          <Sparkles />
          Flip
        </Button>
        <Button
          variant="outline"
          onClick={() => onCommand("Describe what you see. Do not move.", true)}
          title="Ask the AI to describe the camera view (keeps any STOP in place)"
          className="h-10 border-primary/40 text-primary hover:bg-primary/10 hover:text-primary"
        >
          <Eye />
          Observe
        </Button>
      </div>
    </div>
  );
}
