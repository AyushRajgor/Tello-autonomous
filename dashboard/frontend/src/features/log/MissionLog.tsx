import { CircleCheck, CircleX, Hand, ShieldAlert, Sparkles, Terminal, Wrench } from "lucide-react";
import * as React from "react";

import type { ChatMessage, ToolCall } from "@/lib/dashboard-state";
import { buildTimeline, formatClock, formatToolSignature, type TimelineEntry } from "@/lib/timeline";
import { cn } from "@/lib/utils";

export const SUGGESTIONS = [
  "Take off and hover",
  "What do you see?",
  "Turn around slowly and describe the room",
  "Come back home and land"
];

/**
 * One timeline for the session: what you asked, what the AI said and did,
 * and what you and the safety watchdog did directly.
 */
export function MissionLog({
  messages,
  tools,
  onSuggest
}: {
  messages: ChatMessage[];
  tools: ToolCall[];
  onSuggest: (text: string) => void;
}) {
  const entries = React.useMemo(() => buildTimeline(messages, tools), [messages, tools]);
  const scrollRef = React.useRef<HTMLDivElement | null>(null);
  const pinnedRef = React.useRef(true);

  React.useEffect(() => {
    const el = scrollRef.current;
    if (el && pinnedRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [entries.length]);

  return (
    <div
      ref={scrollRef}
      onScroll={(event) => {
        const el = event.currentTarget;
        pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
      }}
      className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
    >
      {entries.length === 0 ? (
        <EmptyLog onSuggest={onSuggest} />
      ) : (
        <ol className="flex flex-col gap-4 p-4">
          {entries.map((entry) => (
            <Row key={entry.id} entry={entry} />
          ))}
        </ol>
      )}
    </div>
  );
}

function EmptyLog({ onSuggest }: { onSuggest: (text: string) => void }) {
  return (
    <div className="flex flex-col gap-4 p-4">
      <div>
        <p className="text-sm font-medium">No messages yet</p>
        <p className="mt-1 text-[13px] text-pretty text-muted-foreground">
          Tell TelloAI what to do in plain words. Its replies and every action it takes show up here.
        </p>
      </div>
      <div className="flex flex-col items-start gap-2">
        <p className="text-[13px] text-muted-foreground">Try one of these:</p>
        {SUGGESTIONS.map((suggestion) => (
          <button
            key={suggestion}
            type="button"
            onClick={() => onSuggest(suggestion)}
            className="rounded-lg bg-secondary px-3 py-1.5 text-start text-sm transition-[background-color,scale] duration-150 ease-out outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 motion-safe:active:scale-[0.96]"
          >
            {suggestion}
          </button>
        ))}
      </div>
    </div>
  );
}

function Time({ at }: { at: number }) {
  return <time className="text-xs text-muted-foreground tabular-nums">{formatClock(at)}</time>;
}

function Row({ entry }: { entry: TimelineEntry }) {
  if (entry.kind === "tool") {
    return <ToolRow tool={entry.tool} />;
  }
  const { message } = entry;

  if (message.role === "user") {
    return (
      <li className="flex flex-col items-end gap-1">
        <p className="max-w-[85%] rounded-2xl rounded-ee-md bg-secondary px-3 py-2 text-sm leading-relaxed text-pretty wrap-break-word">
          {message.text}
        </p>
        <Time at={message.receivedAt} />
      </li>
    );
  }

  if (message.role === "assistant") {
    return (
      <li className="flex gap-2.5">
        <span className="grid size-6 shrink-0 place-items-center rounded-full bg-primary-subtle text-primary-text" aria-hidden="true">
          <Sparkles className="size-3.5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="flex items-baseline gap-2">
            <span className="text-[13px] font-semibold">TelloAI</span>
            <Time at={message.receivedAt} />
          </p>
          <p className="mt-0.5 text-sm leading-relaxed text-pretty wrap-break-word">{message.text}</p>
        </div>
      </li>
    );
  }

  return <EventRow message={message} />;
}

/** Operator actions, safety events and system notes: one line with an icon and a label. */
function EventRow({ message }: { message: ChatMessage }) {
  const level = message.level ?? "info";
  const failed = level === "critical" || level === "warning";
  const kind =
    message.role === "safety"
      ? { icon: ShieldAlert, label: "Safety" }
      : message.role === "operator"
        ? { icon: Hand, label: "You" }
        : { icon: Terminal, label: "System" };
  const Icon = kind.icon;

  return (
    <li
      className={cn(
        "flex items-start gap-2.5 text-[13px]",
        failed && "rounded-lg p-2.5",
        level === "critical" && "bg-destructive-subtle",
        level === "warning" && "bg-warning-subtle"
      )}
    >
      <Icon
        className={cn(
          "mt-0.5 size-4 shrink-0",
          level === "critical" ? "text-destructive-text" : level === "warning" ? "text-warning-text" : "text-muted-foreground"
        )}
        strokeWidth={1.5}
        aria-hidden="true"
      />
      <p className="min-w-0 flex-1 text-pretty wrap-break-word">
        <span className="font-medium">{kind.label}</span>
        <span className={failed ? "" : "text-muted-foreground"}> · {message.text}</span>
      </p>
      <Time at={message.receivedAt} />
    </li>
  );
}

function ToolRow({ tool }: { tool: ToolCall }) {
  const ok = tool.status === "ok";
  const StatusIcon = ok ? CircleCheck : CircleX;
  return (
    <li className="ps-[34px]">
      <details className="group rounded-lg bg-muted">
        <summary className="flex cursor-pointer list-none items-center gap-2 rounded-lg px-2.5 py-1.5 text-[13px] outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
          <Wrench className="size-3.5 shrink-0 text-muted-foreground" strokeWidth={1.5} aria-hidden="true" />
          <code className="min-w-0 flex-1 truncate font-mono text-xs">{formatToolSignature(tool)}</code>
          <span className={cn("flex shrink-0 items-center gap-1", ok ? "text-success-text" : "text-destructive-text")}>
            <StatusIcon className="size-3.5" aria-hidden="true" />
            {ok ? "Done" : "Failed"}
          </span>
        </summary>
        <dl className="grid gap-2 px-2.5 pb-2.5 font-mono text-xs">
          <div>
            <dt className="font-sans text-muted-foreground">Arguments</dt>
            <dd className="mt-0.5 whitespace-pre-wrap wrap-break-word">{safeJson(tool.args)}</dd>
          </div>
          <div>
            <dt className="font-sans text-muted-foreground">Result</dt>
            <dd className="mt-0.5 max-h-48 overflow-auto whitespace-pre-wrap wrap-break-word">{safeJson(tool.result)}</dd>
          </div>
        </dl>
      </details>
    </li>
  );
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? "null";
  } catch {
    return "Unable to show this value";
  }
}
