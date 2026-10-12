import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Eye,
  PlaneLanding,
  PlaneTakeoff,
  RotateCw,
  Sparkles
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

type Control = {
  label: string;
  command: string;
  icon: LucideIcon;
  variant: "default" | "destructive" | "secondary" | "outline";
  span?: boolean;
};

const controls: Control[] = [
  { label: "Takeoff", command: "take off", icon: PlaneTakeoff, variant: "default" },
  { label: "Land", command: "land", icon: PlaneLanding, variant: "destructive" },
  { label: "Forward", command: "move forward 50 cm", icon: ArrowUp, variant: "secondary" },
  { label: "Back", command: "move back 50 cm", icon: ArrowDown, variant: "secondary" },
  { label: "Left", command: "move left 50 cm", icon: ArrowLeft, variant: "secondary" },
  { label: "Right", command: "move right 50 cm", icon: ArrowRight, variant: "secondary" },
  { label: "Spin", command: "spin 360 degrees", icon: RotateCw, variant: "secondary" },
  { label: "Observe", command: "describe what you see", icon: Eye, variant: "secondary" },
  { label: "Flip", command: "flip", icon: Sparkles, variant: "outline", span: true }
];

export function FlightControls({
  onCommand,
  connected
}: {
  onCommand: (command: string) => void;
  connected?: boolean;
}) {
  return (
    <div className="space-y-3">
      {!connected ? (
        <p className="text-xs text-muted-foreground">
          Link offline — commands report back in the log until the socket reconnects.
        </p>
      ) : null}
      <div className="grid grid-cols-2 gap-2">
        {controls.map(({ label, command, icon: Icon, variant, span }) => (
          <Button
            key={label}
            variant={variant}
            onClick={() => onCommand(command)}
            className={span ? "col-span-2" : undefined}
          >
            <Icon />
            {label}
          </Button>
        ))}
      </div>
    </div>
  );
}
