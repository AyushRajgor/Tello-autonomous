import { Braces, Clock, Terminal, Wrench } from "lucide-react";
import type * as React from "react";

import { Badge } from "@/components/ui/badge";
import type { ToolCall } from "@/lib/dashboard-state";
import { summarizeTrace, type TraceSummaryInput } from "@/lib/format";

export function TracePanel({ traces, tools }: { traces: TraceSummaryInput[]; tools: ToolCall[] }) {
  return (
    <div className="space-y-5 p-3">
      <section>
        <SectionTitle icon={Wrench}>Tool calls</SectionTitle>
        {tools.length === 0 ? (
          <Empty>No tool calls yet.</Empty>
        ) : (
          <div className="space-y-2">
            {tools.map((tool) => (
              <article key={tool.id} className="space-y-2 rounded-lg border p-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="flex min-w-0 items-center gap-2 text-sm font-medium">
                    <Wrench className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate">{tool.name}</span>
                  </span>
                  <Badge variant={tool.status === "ok" ? "default" : "destructive"} className="font-normal">
                    {tool.status}
                  </Badge>
                </div>
                <Pre value={{ args: tool.args, result: tool.result }} />
              </article>
            ))}
          </div>
        )}
      </section>

      <section>
        <SectionTitle icon={Terminal}>Traces</SectionTitle>
        {traces.length === 0 ? (
          <Empty>No completed traces yet.</Empty>
        ) : (
          <div className="space-y-2">
            {traces.map((trace, index) => {
              const summary = summarizeTrace(trace);
              return (
                <article key={summary.id + index} className="space-y-2 rounded-lg border p-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="flex min-w-0 items-center gap-2 text-sm">
                      <Terminal className="size-3.5 shrink-0 text-muted-foreground" />
                      <span className="truncate">{summary.command}</span>
                    </span>
                    <Badge variant="secondary" className="font-normal">{summary.spans} spans</Badge>
                  </div>
                  <div className="flex items-center gap-4 font-mono text-[11px] tabular-nums text-muted-foreground">
                    <span className="flex items-center gap-1.5">
                      <Clock className="size-3" /> {summary.duration}
                    </span>
                    <span className="flex min-w-0 items-center gap-1.5">
                      <Braces className="size-3 shrink-0" /> <span className="truncate">{summary.id}</span>
                    </span>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}

function SectionTitle({ icon: Icon, children }: { icon: typeof Wrench; children: React.ReactNode }) {
  return (
    <h4 className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
      <Icon className="size-3.5" />
      {children}
    </h4>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed px-4 py-6 text-center text-xs text-muted-foreground">{children}</div>
  );
}

function Pre({ value }: { value: unknown }) {
  let text: string;
  try {
    text = JSON.stringify(value, null, 2);
  } catch {
    text = "Unable to serialize payload";
  }
  return (
    <pre className="max-h-48 overflow-auto rounded-md border bg-background p-2.5 font-mono text-[11px] leading-relaxed whitespace-pre-wrap wrap-break-word text-muted-foreground">
      {text}
    </pre>
  );
}
