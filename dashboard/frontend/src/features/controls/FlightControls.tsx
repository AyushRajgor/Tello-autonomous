import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
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
import { cn } from "@/lib/utils";
import type { OperatorArgs, OperatorSender } from "@/types";

const STEP_CM = 50;
const TURN_DEG = 90;
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
    <div className="space-y-2.5">
      {!connected ? (
        <p role="status" className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          Dashboard offline — controls can't reach the drone until the socket reconnects.
        </p>
      ) : null}

      <Button
        variant="destructive"
        onClick={() => onOperator("hover")}
        aria-label="STOP — hover in place"
        aria-keyshortcuts="Space"
        className="h-16 w-full text-xl font-bold tracking-widest text-white shadow-lg shadow-destructive/25 hover:bg-destructive/85 dark:bg-destructive dark:hover:bg-destructive/85 [&_svg:not([class*='size-'])]:size-7"
      >
        <OctagonX />
        STOP
        <Kbd className="ml-1 text-xs">Space</Kbd>
      </Button>

      <div className="grid grid-cols-2 gap-2">
        <Button
          variant="secondary"
          onClick={() => onOperator("land")}
          aria-label="Land now"
          aria-keyshortcuts="Escape"
          className="h-10"
        >
          <PlaneLanding />
          Land
          <Kbd>Esc</Kbd>
        </Button>
        <EmergencyStop onConfirm={() => onOperator("emergency_stop")} />
      </div>

      <p className="text-[11px] leading-relaxed text-muted-foreground">
        <Kbd>Space</Kbd> STOP · <Kbd>Esc</Kbd> Land. Shortcuts pause while you type. STOP and Land also pause the AI
        until you resume or give a new instruction.
      </p>
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
        className="h-10 border-destructive/60 text-destructive hover:bg-destructive/10 hover:text-destructive dark:border-destructive/60"
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
      className="col-span-2 space-y-2 rounded-lg border border-destructive bg-destructive/10 p-3"
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

type PadControl = {
  label: string;
  ariaLabel: string;
  icon: LucideIcon;
  args: OperatorArgs;
  action: "move" | "rotate";
};

const pad: PadControl[] = [
  { label: `${TURN_DEG}°`, ariaLabel: `Turn left ${TURN_DEG}°`, icon: RotateCcw, action: "rotate", args: { degrees: -TURN_DEG } },
  { label: "Forward", ariaLabel: `Move forward ${STEP_CM} cm`, icon: ArrowUp, action: "move", args: { direction: "forward", cm: STEP_CM } },
  { label: `${TURN_DEG}°`, ariaLabel: `Turn right ${TURN_DEG}°`, icon: RotateCw, action: "rotate", args: { degrees: TURN_DEG } },
  { label: "Left", ariaLabel: `Move left ${STEP_CM} cm`, icon: ArrowLeft, action: "move", args: { direction: "left", cm: STEP_CM } },
  { label: "Back", ariaLabel: `Move back ${STEP_CM} cm`, icon: ArrowDown, action: "move", args: { direction: "back", cm: STEP_CM } },
  { label: "Right", ariaLabel: `Move right ${STEP_CM} cm`, icon: ArrowRight, action: "move", args: { direction: "right", cm: STEP_CM } }
];

/** Manual flight pad. Everything here except "Observe" bypasses the AI. */
export function FlightControls({
  onOperator,
  onCommand
}: {
  onOperator: OperatorSender;
  onCommand: (command: string, keepHold?: boolean) => void;
}) {
  return (
    <div className="space-y-2">
      <Button variant="outline" onClick={() => onOperator("takeoff")} className="w-full">
        <PlaneTakeoff />
        Takeoff
      </Button>

      <div className="grid grid-cols-3 gap-2">
        {pad.map(({ label, ariaLabel, icon: Icon, action, args }) => (
          <Button key={ariaLabel} variant="secondary" aria-label={ariaLabel} title={ariaLabel} onClick={() => onOperator(action, args)}>
            <Icon />
            {label}
          </Button>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Button variant="outline" onClick={() => onOperator("flip", { direction: "forward" })} title="Flip forward (needs >50% battery)">
          <Sparkles />
          Flip
        </Button>
        <Button variant="outline" onClick={() => onCommand("Describe what you see. Do not move.", true)} title="Ask the AI to describe the camera view (keeps any STOP in place)">
          <Eye />
          Observe
          <span className="text-[10px] font-normal text-muted-foreground">AI</span>
        </Button>
      </div>

      <p className="text-[11px] text-muted-foreground">
        Moves are {STEP_CM} cm, turns {TURN_DEG}°, sent directly to the drone (safety limits still apply).
      </p>
    </div>
  );
}
