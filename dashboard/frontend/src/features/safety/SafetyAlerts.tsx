import { Info, OctagonAlert, TriangleAlert, X } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import type { SafetyAlert } from "@/lib/dashboard-state";
import { cn } from "@/lib/utils";

const AUTO_DISMISS_MS = { info: 6000, warning: 12000 } as const;

const levelStyle = {
  critical: "border-destructive bg-destructive text-white dark:bg-destructive/90",
  warning: "border-warning/60 bg-card text-foreground",
  info: "border-border bg-card text-foreground"
} as const;

const levelIcon = {
  critical: OctagonAlert,
  warning: TriangleAlert,
  info: Info
} as const;

/**
 * Toast stack for safety events. Critical alerts stay until dismissed.
 * Anchored bottom-left so it can never cover the STOP / Land controls
 * (side panel on desktop, header button on narrow screens).
 */
export function SafetyAlerts({ alerts, onDismiss }: { alerts: SafetyAlert[]; onDismiss: (id: string) => void }) {
  return (
    <div className="pointer-events-none fixed inset-x-4 bottom-4 z-50 flex flex-col-reverse items-stretch gap-2 sm:right-auto sm:w-96">
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
        "pointer-events-auto flex items-start gap-3 rounded-lg border px-3 py-2.5 shadow-lg animate-in fade-in slide-in-from-bottom-2",
        levelStyle[alert.level]
      )}
    >
      <Icon className={cn("mt-0.5 size-4 shrink-0", alert.level === "warning" && "text-warning")} />
      <div className="min-w-0 flex-1">
        <p className="text-[11px] font-semibold tracking-wide uppercase opacity-90">
          {critical ? "Critical" : alert.level === "warning" ? "Warning" : "Notice"}
        </p>
        <p className="text-sm text-pretty wrap-break-word">{alert.message}</p>
      </div>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label="Dismiss alert"
        onClick={() => onDismiss(alert.id)}
        className={cn("shrink-0", critical && "text-white hover:bg-white/15 hover:text-white")}
      >
        <X />
      </Button>
    </div>
  );
}
