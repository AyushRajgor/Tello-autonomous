import { Info, OctagonAlert, TriangleAlert, X } from "lucide-react";
import * as React from "react";

import type { SafetyAlert } from "@/lib/dashboard-state";
import { cn } from "@/lib/utils";

const AUTO_DISMISS_MS = { info: 6000, warning: 12000 } as const;

const levelStyle = {
  critical: "border-destructive bg-destructive text-white shadow-destructive/30",
  warning: "border-warning/60 bg-popover text-foreground",
  info: "border-border bg-popover text-foreground"
} as const;

const levelIcon = {
  critical: OctagonAlert,
  warning: TriangleAlert,
  info: Info
} as const;

/**
 * Toast stack for safety events, centred over the camera view. Critical alerts
 * stay until dismissed. It never overlaps the STOP / Land controls (right rail
 * on desktop, bottom bar on phones).
 */
export function SafetyAlerts({ alerts, onDismiss }: { alerts: SafetyAlert[]; onDismiss: (id: string) => void }) {
  return (
    <div className="pointer-events-none fixed inset-x-3 top-16 z-50 mx-auto flex max-w-[440px] flex-col items-stretch gap-2 lg:right-[400px]">
      {alerts.map((alert) => (
        <AlertToast key={alert.id} alert={alert} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

function AlertToast({ alert, onDismiss }: { alert: SafetyAlert; onDismiss: (id: string) => void }) {
  React.useEffect(() => {
    if (alert.level === "critical") {
      return;
    }
    const timer = setTimeout(() => onDismiss(alert.id), AUTO_DISMISS_MS[alert.level]);
    return () => clearTimeout(timer);
  }, [alert.id, alert.level, onDismiss]);

  const Icon = levelIcon[alert.level];
  const critical = alert.level === "critical";

  return (
    <div
      role={critical ? "alert" : "status"}
      className={cn(
        "pointer-events-auto flex items-start gap-3 overflow-hidden rounded-xl border px-3.5 py-3 shadow-2xl animate-in fade-in slide-in-from-top-2",
        levelStyle[alert.level]
      )}
    >
      <Icon className={cn("mt-0.5 size-5 shrink-0", alert.level === "warning" && "text-warning")} />
      <div className="min-w-0 flex-1">
        <p className="font-mono text-[10px] font-semibold tracking-[0.16em] uppercase opacity-85">
          {critical ? "Critical" : alert.level === "warning" ? "Warning" : "Notice"}
        </p>
        <p className="mt-0.5 text-sm text-pretty wrap-break-word">{alert.message}</p>
      </div>
      <button
        type="button"
        aria-label="Dismiss alert"
        onClick={() => onDismiss(alert.id)}
        className={cn(
          "grid size-6 shrink-0 place-items-center rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring",
          critical ? "hover:bg-white/15" : "text-muted-foreground hover:bg-accent hover:text-foreground"
        )}
      >
        <X className="size-3.5" />
      </button>
    </div>
  );
}
