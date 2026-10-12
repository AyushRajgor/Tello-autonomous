import { ChevronRight, Hand, ShieldAlert, Sparkles, Terminal, User, Wrench } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import * as React from "react";

import type { ChatMessage, ToolCall } from "@/lib/dashboard-state";
import { buildTimeline, formatClock, formatToolSignature, type TimelineEntry } from "@/lib/timeline";
import { cn } from "@/lib/utils";

/**
 * The mission log: conversation, AI tool calls, operator actions and safety
 * events on one timeline, so "what happened and why" reads top to bottom.
 */
export function MissionLog({ messages, tools }: { messages: ChatMessage[]; tools: ToolCall[] }) {
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
        <EmptyLog />
      ) : (
        <ol className="relative space-y-3 px-3 py-3 before:absolute before:top-4 before:bottom-4 before:left-[23px] before:w-px before:bg-border">
          {entries.map((entry) => (
            <TimelineRow key={entry.id} entry={entry} />
          ))}
        </ol>
      )}
    </div>
  );
}

function EmptyLog() {
  const steps = [
    ["Take off", "from the flight sticks, or ask the AI."],
    ["Talk to it", "type below the camera or hold the mic."],
    ["Stay in charge", "Space stops, Esc lands — any time."]
  ];
  return (
    <div className="flex h-full flex-col justify-center gap-4 px-5 py-8">
      <div>
        <p className="font-mono text-[10px] tracking-[0.18em] text-muted-foreground uppercase">Mission log</p>
        <p className="mt-1 text-sm text-pretty text-muted-foreground">
          Everything said and done this session lands here — commands, AI tool calls, safety events.
        </p>
      </div>
      <ol className="space-y-2.5">
        {steps.map(([title, body], i) => (
          <li key={title} className="flex gap-3 text-sm">
            <span className="grid size-6 shrink-0 place-items-center rounded-full border font-mono text-[11px] text-hud">
              {i + 1}
            </span>
            <span className="text-pretty">
              <span className="font-medium">{title}</span> <span className="text-muted-foreground">{body}</span>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

type Tone = { icon: LucideIcon; label: string; node: string; body: string };

function messageTone(message: ChatMessage): Tone {
  switch (message.role) {
    case "user":
      return { icon: User, label: "You", node: "bg-foreground text-background", body: "text-foreground" };
    case "assistant":
      return {
        icon: Sparkles,
        label: "TelloAI",
        node: "bg-primary text-primary-foreground",
        body: "rounded-lg rounded-tl-sm border border-primary/20 bg-primary/[0.06] px-3 py-2 text-foreground"
      };
    case "safety": {
      const level = message.level ?? "info";
      return {
        icon: ShieldAlert,
        label: level === "critical" ? "Safety · critical" : level === "warning" ? "Safety · warning" : "Safety",
        node:
          level === "critical"
            ? "bg-destructive text-white"
            : level === "warning"
              ? "bg-warning text-background"
              : "bg-raised text-muted-foreground border",
        body:
          level === "critical"
            ? "rounded-lg border border-destructive/60 bg-destructive/12 px-3 py-2 text-foreground"
            : level === "warning"
              ? "rounded-lg border border-warning/40 bg-warning/8 px-3 py-2 text-foreground"
              : "text-muted-foreground"
      };
    }
    case "operator": {
      const failed = message.level === "critical" || message.level === "warning";
      return {
        icon: Hand,
        label: "Operator",
        node: failed ? "bg-destructive text-white" : "bg-raised text-warning border",
        body: failed ? "rounded-lg border border-destructive/50 bg-destructive/10 px-3 py-2" : "text-muted-foreground"
      };
    }
    default:
      return { icon: Terminal, label: "System", node: "bg-raised text-muted-foreground border", body: "font-mono text-xs text-muted-foreground" };
  }
}

function TimelineRow({ entry }: { entry: TimelineEntry }) {
  if (entry.kind === "tool") {
    return <ToolRow tool={entry.tool} />;
  }
  const { message } = entry;
  const tone = messageTone(message);
  const Icon = tone.icon;
  return (
    <li className="relative flex gap-3 animate-in fade-in slide-in-from-bottom-1 duration-200">
      <span className={cn("z-10 mt-0.5 grid size-[22px] shrink-0 place-items-center rounded-full", tone.node)}>
        <Icon className="size-3" strokeWidth={2.5} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="flex items-baseline gap-2 text-[11px]">
          <span className="font-semibold tracking-wide">{tone.label}</span>
          <time className="font-mono text-[10px] text-muted-foreground/70 tabular-nums">{formatClock(message.receivedAt)}</time>
        </p>
        <p className={cn("mt-1 w-fit max-w-full text-sm leading-relaxed text-pretty wrap-break-word", tone.body)}>
          {message.text}
        </p>
      </div>
    </li>
  );
}

function ToolRow({ tool }: { tool: ToolCall }) {
  const ok = tool.status === "ok";
  return (
    <li className="relative flex gap-3 animate-in fade-in duration-200">
      <span
        className={cn(
          "z-10 mt-1.5 grid size-[22px] shrink-0 place-items-center rounded-md border",
          ok ? "border-hud/40 bg-sunken text-hud" : "border-destructive/60 bg-destructive/15 text-destructive"
        )}
      >
        <Wrench className="size-3" />
      </span>
      <details className="group min-w-0 flex-1 rounded-lg border bg-sunken/60 open:bg-sunken">
        <summary className="flex cursor-pointer list-none items-center gap-2 px-2.5 py-1.5 [&::-webkit-details-marker]:hidden">
          <ChevronRight className="size-3 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" />
          <code className="min-w-0 flex-1 truncate font-mono text-[11px] text-hud">{formatToolSignature(tool)}</code>
          <span
            className={cn(
              "shrink-0 rounded px-1.5 py-px font-mono text-[10px] uppercase",
              ok ? "bg-success/15 text-success" : "bg-destructive/20 text-destructive"
            )}
          >
            {tool.status}
          </span>
        </summary>
        <pre className="max-h-48 overflow-auto border-t px-2.5 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap wrap-break-word text-muted-foreground">
          {safeJson(tool.result)}
        </pre>
      </details>
    </li>
  );
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? "null";
  } catch {
    return "Unable to serialize payload";
  }
}
