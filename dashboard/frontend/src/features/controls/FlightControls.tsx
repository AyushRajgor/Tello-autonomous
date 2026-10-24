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

const press = "transition-[background-color,scale] duration-150 ease-out motion-safe:enabled:active:scale-[0.96]";
const focusRing = "outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50";

export function Kbd({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <kbd
      className={cn(
        "rounded border border-current/30 px-1.5 py-0.5 font-mono text-xs leading-none font-medium opacity-80",
        className
      )}
    >
      {children}
    </kbd>
  );
}

/**
 * Always-available safety controls: Stop (hover), Land and Emergency stop.
 * These go straight to the drone; the AI is never in the loop.
 */
export function EmergencyControls({
  onOperator,
  connected = true
}: {
  onOperator: OperatorSender;
  connected?: boolean;
}) {
  return (
    <div className="flex flex-col gap-2">
      <p role="status" className={cn("text-[13px] text-destructive-text", connected && "sr-only")}>
        {connected ? "" : "The dashboard is offline. These controls reach the drone again once it reconnects."}
      </p>

      <button
        type="button"
        onClick={() => onOperator("hover")}
        aria-label="STOP — hover in place"
        aria-keyshortcuts="Space"
        className={cn(
          "flex h-14 w-full items-center gap-3 rounded-lg bg-destructive px-3 text-start text-white hover:bg-destructive/90",
          press,
          "outline-none focus-visible:ring-[3px] focus-visible:ring-destructive/40"
        )}
      >
        <OctagonX className="size-6 shrink-0" aria-hidden="true" />
        <span className="min-w-0 flex-1">
          <span className="block text-base leading-tight font-semibold">Stop</span>
          <span className="block text-[13px] leading-tight text-white">Hover and pause the AI</span>
        </span>
        <Kbd className="border-white/50 text-white opacity-100">Space</Kbd>
      </button>

      <div className="grid grid-cols-2 gap-2">
        <Button variant="secondary" onClick={() => onOperator("land")} aria-label="Land now" aria-keyshortcuts="Escape" className="h-10">
          <PlaneLanding aria-hidden="true" />
          Land
          <Kbd className="ms-auto">Esc</Kbd>
        </Button>
        <EmergencyStop onConfirm={() => onOperator("emergency_stop")} />
      </div>
    </div>
  );
}

/** Emergency stop cuts the motors mid-air, so it asks once more. */
function EmergencyStop({ onConfirm }: { onConfirm: () => void }) {
  const [armed, setArmed] = React.useState(false);
  const titleId = React.useId();
  const descId = React.useId();
  const cancelRef = React.useRef<HTMLButtonElement | null>(null);

  React.useEffect(() => {
    if (!armed) {
      return;
    }
    cancelRef.current?.focus();
    const timer = setTimeout(() => setArmed(false), ESTOP_CONFIRM_WINDOW_MS);
    return () => clearTimeout(timer);
  }, [armed]);

  if (!armed) {
    return (
      <Button
        variant="outline"
        onClick={() => setArmed(true)}
        aria-label="Emergency stop — cut motors, the drone falls"
        className="h-10 text-destructive-text hover:text-destructive-text"
      >
        <Zap aria-hidden="true" />
        Emergency stop
      </Button>
    );
  }

  return (
    <div
      role="group"
      aria-labelledby={titleId}
      aria-describedby={descId}
      className="col-span-2 rounded-lg bg-destructive-subtle p-3 motion-safe:animate-in motion-safe:fade-in"
    >
      <p id={titleId} className="text-sm font-semibold text-destructive-text">
        Cut all motors? The drone will fall.
      </p>
      <p id={descId} className="mt-1 text-[13px] text-pretty">
        Use this only if the drone is about to hit someone. Stop or Land is safer in every other case.
      </p>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <Button
          variant="destructive"
          onClick={() => {
            setArmed(false);
            onConfirm();
          }}
        >
          <Zap aria-hidden="true" />
          Cut motors
        </Button>
        <Button ref={cancelRef} variant="outline" onClick={() => setArmed(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/** Thumb-reach Stop and Land for phones and tablets, pinned to the bottom edge. */
export function QuickSafetyBar({ onOperator }: { onOperator: OperatorSender }) {
  return (
    <div className="fixed inset-x-0 bottom-0 z-40 border-t bg-background/95 px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur-sm lg:hidden">
      <div className="mx-auto grid max-w-xl grid-cols-[minmax(0,1fr)_auto] gap-3">
        <button
          type="button"
          onClick={() => onOperator("hover")}
          aria-label="Quick STOP — hover in place"
          className={cn(
            "flex h-12 items-center justify-center gap-2 rounded-lg bg-destructive text-base font-semibold text-white",
            press,
            "outline-none focus-visible:ring-[3px] focus-visible:ring-destructive/40"
          )}
        >
          <OctagonX className="size-5" aria-hidden="true" />
          Stop
        </button>
        <Button variant="secondary" onClick={() => onOperator("land")} aria-label="Quick land" className="h-12 px-5">
          <PlaneLanding aria-hidden="true" />
          Land
        </Button>
      </div>
    </div>
  );
}

type PadControl = {
  slot: "n" | "e" | "s" | "w";
  ariaLabel: string;
  icon: LucideIcon;
  action: "move" | "rotate";
  args: OperatorArgs;
};

function moveControl(slot: PadControl["slot"], direction: MoveDirection, cm: number, icon: LucideIcon): PadControl {
  return { slot, icon, action: "move", args: { direction, cm }, ariaLabel: `Move ${direction} ${cm} cm` };
}

function turnControl(slot: PadControl["slot"], degrees: number, icon: LucideIcon): PadControl {
  const side = degrees < 0 ? "left" : "right";
  return { slot, icon, action: "rotate", args: { degrees }, ariaLabel: `Turn ${side} ${Math.abs(degrees)}°` };
}

const slotPosition: Record<PadControl["slot"], string> = {
  n: "col-start-2 row-start-1",
  w: "col-start-1 row-start-2",
  e: "col-start-3 row-start-2",
  s: "col-start-2 row-start-3"
};

/** A plus-shaped pad of four 44px buttons. */
function Pad({ label, controls, onOperator }: { label: string; controls: PadControl[]; onOperator: OperatorSender }) {
  return (
    <div role="group" aria-label={label} className="flex flex-col items-center gap-2">
      <div className="grid grid-cols-[repeat(3,2.75rem)] grid-rows-[repeat(3,2.75rem)] gap-1">
        {controls.map(({ slot, ariaLabel, icon: Icon, action, args }) => (
          <button
            key={ariaLabel}
            type="button"
            aria-label={ariaLabel}
            title={ariaLabel}
            onClick={() => onOperator(action, args)}
            className={cn(
              "grid place-items-center rounded-lg bg-secondary text-secondary-foreground hover:bg-accent",
              press,
              focusRing,
              slotPosition[slot]
            )}
          >
            <Icon className="size-5" aria-hidden="true" />
          </button>
        ))}
      </div>
      <p className="text-[13px] text-muted-foreground">{label}</p>
    </div>
  );
}

/** Native radios styled as a segmented control: arrow keys work for free. */
function Segmented<T extends number>({
  legend,
  name,
  unit,
  value,
  options,
  onChange
}: {
  legend: string;
  name: string;
  unit: string;
  value: T;
  options: readonly T[];
  onChange: (value: T) => void;
}) {
  return (
    <fieldset className="flex min-w-0 items-center gap-2">
      <legend className="sr-only">{legend}</legend>
      <span aria-hidden="true" className="text-[13px] text-muted-foreground">
        {legend}
      </span>
      <div className="flex rounded-lg bg-muted p-0.5">
        {options.map((option) => (
          <label key={option} className="relative">
            <input
              type="radio"
              name={name}
              value={option}
              checked={option === value}
              onChange={() => onChange(option)}
              className="peer sr-only"
            />
            <span className="block cursor-pointer rounded-md px-2 py-1 text-[13px] text-muted-foreground tabular-nums transition-[background-color,color] duration-150 peer-checked:bg-card peer-checked:font-medium peer-checked:text-foreground peer-checked:shadow-border peer-focus-visible:ring-[3px] peer-focus-visible:ring-ring/50 hover:text-foreground">
              {option}
              {unit}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

/** Manual flight. Everything here except Observe goes straight to the drone. */
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

  return (
    <div className="@container flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Segmented legend="Distance" name="step" unit=" cm" value={step} options={STEP_OPTIONS} onChange={setStep} />
        <Segmented legend="Turn" name="turn" unit="°" value={turn} options={TURN_OPTIONS} onChange={setTurn} />
      </div>

      {/* Side by side whenever both 140px pads fit, sized by the panel, not the
          viewport: a scrollbar in the controls column must not stack them. */}
      <div className="grid justify-items-center gap-4 @min-[17.5rem]:grid-cols-2 @min-[17.5rem]:gap-x-0">
        <Pad
          label="Climb and turn"
          onOperator={onOperator}
          controls={[
            moveControl("n", "up", step, ChevronsUp),
            moveControl("s", "down", step, ChevronsDown),
            turnControl("w", -turn, RotateCcw),
            turnControl("e", turn, RotateCw)
          ]}
        />
        <Pad
          label="Move"
          onOperator={onOperator}
          controls={[
            moveControl("n", "forward", step, ArrowUp),
            moveControl("s", "back", step, ArrowDown),
            moveControl("w", "left", step, ArrowLeft),
            moveControl("e", "right", step, ArrowRight)
          ]}
        />
      </div>

      <div className="grid grid-cols-3 gap-2">
        <Button variant="secondary" onClick={() => onOperator("takeoff")} disabled={flying}>
          <PlaneTakeoff aria-hidden="true" />
          {flying ? "Flying" : "Take off"}
        </Button>
        <Button
          variant="secondary"
          onClick={() => onOperator("flip", { direction: "forward" })}
          title={flipBlocked ? `Flips need more than ${LIMITS.flipMinBatteryPct}% battery` : "Flip forward"}
        >
          <Sparkles aria-hidden="true" />
          Flip
        </Button>
        <Button
          variant="secondary"
          onClick={() => onCommand("Describe what you see. Do not move.", true)}
          title="Ask the AI to describe the camera view. Any stop stays in place."
        >
          <Eye aria-hidden="true" />
          Observe
        </Button>
      </div>
    </div>
  );
}
