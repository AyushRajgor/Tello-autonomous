import { Clock, Layers } from "lucide-react";

import { summarizeTrace, type TraceSummaryInput } from "@/lib/format";

/** Completed agent turns from the tracer. Tool calls live in the mission log. */
export function TracePanel({ traces }: { traces: TraceSummaryInput[] }) {
  if (traces.length === 0) {
    return (
      <div className="m-3 rounded-lg border border-dashed px-4 py-8 text-center text-xs text-muted-foreground">
        No completed traces yet. Each AI turn shows up here with its timing.
      </div>
    );
  }

  return (
    <ol className="space-y-2 p-3">
      {traces.map((trace, index) => {
        const summary = summarizeTrace(trace);
        return (
          <li key={summary.id + index} className="rounded-lg border bg-sunken/50 p-3">
            <p className="text-sm text-pretty">{summary.command}</p>
            <div className="mt-2 flex items-center gap-4 font-mono text-[10px] text-muted-foreground tabular-nums">
              <span className="flex items-center gap-1.5">
                <Clock className="size-3" /> {summary.duration}
              </span>
              <span className="flex items-center gap-1.5">
                <Layers className="size-3" /> {summary.spans} spans
              </span>
              <span className="ml-auto min-w-0 truncate opacity-70">{summary.id}</span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
