import { summarizeTrace, type TraceSummaryInput } from "@/lib/format";

/** Completed AI turns from the tracer. Individual actions live in the log. */
export function TracePanel({ traces }: { traces: TraceSummaryInput[] }) {
  if (traces.length === 0) {
    return (
      <div className="p-4">
        <p className="text-sm font-medium">No traces yet</p>
        <p className="mt-1 text-[13px] text-pretty text-muted-foreground">
          Each finished AI turn appears here with how long it took, so you can spot slow steps.
        </p>
      </div>
    );
  }

  return (
    <ol className="flex flex-col gap-2 p-4">
      {traces.map((trace, index) => {
        const summary = summarizeTrace(trace);
        return (
          <li key={summary.id + index} className="rounded-lg bg-muted p-3">
            <p className="text-sm text-pretty wrap-break-word">{summary.command}</p>
            <p className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted-foreground tabular-nums">
              <span>{summary.duration}</span>
              <span>{summary.spans} steps</span>
              <span className="font-mono break-all">{summary.id}</span>
            </p>
          </li>
        );
      })}
    </ol>
  );
}
