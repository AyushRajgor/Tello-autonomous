import { Info, OctagonAlert, TriangleAlert, X } from "lucide-react";
import * as React from "react";

import type { SafetyAlert } from "@/lib/dashboard-state";
import { cn } from "@/lib/utils";

/** Only notices time out. Warnings and critical alerts are errors: they stay until dismissed. */
const NOTICE_DISMISS_MS = 6000;

const levelIcon = { critical: OctagonAlert, warning: TriangleAlert, info: Info } as const;
const levelLabel = { critical: "Critical", warning: "Warning", info: "Notice" } as const;

/**
 * Toasts over the main column. Two live regions render empty from the start,
 * so screen readers announce what is added: urgent alerts assertively, the
 * rest politely.
 */
export function SafetyAlerts({ alerts, onDismiss }: { alerts: SafetyAlert[]; onDismiss: (id: string) => void }) {
  const critical = alerts.filter((alert) => alert.level === "critical");
  const other = alerts.filter((alert) => alert.level !== "critical");
  return (
    <div className="pointer-events-none fixed inset-x-4 top-16 z-50 mx-auto flex max-w-md flex-col gap-2 lg:end-[calc(340px+2rem)] xl:top-4">
      <div role="alert" className="flex flex-col gap-2">
        {critical.map((alert) => (
          <AlertToast key={alert.id} alert={alert} onDismiss={onDismiss} />
        ))}
      </div>
      <div role="status" className="flex flex-col gap-2">
        {other.map((alert) => (
          <AlertToast key={alert.id} alert={alert} onDismiss={onDismiss} />
        ))}
      </div>
    </div>
  );
}

function AlertToast({ alert, onDismiss }: { alert: SafetyAlert; onDismiss: (id: string) => void }) {
  React.useEffect(() => {
    if (alert.level !== "info") {
      return;
    }
    const timer = setTimeout(() => onDismiss(alert.id), NOTICE_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [alert.id, alert.level, onDismiss]);

  const Icon = levelIcon[alert.level];
  const critical = alert.level === "critical";

  return (
    <div
      className={cn(
        "pointer-events-auto flex items-start gap-3 rounded-xl border border-transparent p-3 shadow-overlay",
        "motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-top-3 motion-safe:duration-300 motion-reduce:animate-in motion-reduce:fade-in",
        critical ? "bg-destructive text-white" : "bg-popover text-popover-foreground"
      )}
    >
      <Icon
        className={cn("mt-0.5 size-5 shrink-0", alert.level === "warning" && "text-warning-text", alert.level === "info" && "text-muted-foreground")}
        aria-hidden="true"
      />
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-semibold">{levelLabel[alert.level]}</p>
        <p className="text-sm text-pretty wrap-break-word">{alert.message}</p>
      </div>
      <button
        type="button"
        aria-label="Dismiss alert"
        onClick={() => onDismiss(alert.id)}
        className={cn(
          "-me-1 -mt-1 grid size-8 shrink-0 place-items-center rounded-md outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
          critical ? "hover:bg-white/15" : "text-muted-foreground hover:bg-accent hover:text-foreground"
        )}
      >
        <X className="size-4" aria-hidden="true" />
      </button>
    </div>
  );
}
