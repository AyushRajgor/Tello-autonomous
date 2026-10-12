import { Drone, Hand, Mic, MicOff, SendHorizontal, ShieldAlert, Terminal, User } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { ChatMessage } from "@/lib/dashboard-state";
import { cn } from "@/lib/utils";

function RoleIcon({ role }: { role: string }) {
  if (role === "user") return <User className="size-3.5" />;
  if (role === "system") return <Terminal className="size-3.5" />;
  return <Drone className="size-4" />;
}

export function ConversationPanel({
  messages,
  onSend,
  onMicStart,
  onMicStop,
  micActive,
  connected,
  voiceEnabled = true
}: {
  messages: ChatMessage[];
  onSend: (text: string) => void;
  onMicStart: () => void;
  onMicStop: () => void;
  micActive: boolean;
  connected?: boolean;
  voiceEnabled?: boolean;
}) {
  const [text, setText] = React.useState("");
  const endRef = React.useRef<HTMLDivElement | null>(null);

  React.useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages.length]);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setText("");
  }

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      <ScrollArea className="min-h-0 flex-1">
        {messages.length === 0 ? (
          <div className="grid h-full place-content-center justify-items-center gap-2 px-6 py-10 text-center">
            <Drone className="size-7 text-muted-foreground/40" />
            <p className="max-w-[220px] text-sm text-balance text-muted-foreground">
              Awaiting command stream. Type below or tap the mic to talk.
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-4 p-3">
            {messages.map((message) => {
              if (message.role === "safety" || message.role === "operator") {
                return <LogRow key={message.id} message={message} />;
              }
              const isUser = message.role === "user";
              return (
                <div key={message.id} className={cn("flex gap-2.5", isUser && "flex-row-reverse")}>
                  <span
                    className={cn(
                      "mt-0.5 grid size-7 shrink-0 place-items-center rounded-full",
                      isUser ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground"
                    )}
                  >
                    <RoleIcon role={message.role} />
                  </span>
                  <div className={cn("flex min-w-0 max-w-[85%] flex-col gap-1", isUser && "items-end")}>
                    <p
                      className={cn(
                        "w-fit rounded-2xl px-3 py-2 text-sm leading-relaxed text-pretty wrap-break-word",
                        isUser
                          ? "rounded-tr-sm border border-primary/25 bg-primary/10"
                          : "rounded-tl-sm border bg-card"
                      )}
                    >
                      {message.text}
                    </p>
                  </div>
                </div>
              );
            })}
            <div ref={endRef} />
          </div>
        )}
      </ScrollArea>

      {micActive ? (
        <div className="flex shrink-0 items-center gap-2 border-t px-3 py-2 text-xs font-medium text-destructive">
          <span className="relative flex size-2">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-destructive opacity-75" />
            <span className="relative inline-flex size-2 rounded-full bg-destructive" />
          </span>
          Listening… tap the mic to stop
        </div>
      ) : null}

      <form onSubmit={submit} className="flex shrink-0 items-center gap-2 border-t p-3">
        {voiceEnabled ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className={cn(
                  "shrink-0",
                  micActive && "bg-destructive/15 text-destructive hover:bg-destructive/20 hover:text-destructive"
                )}
                onClick={() => (micActive ? onMicStop() : onMicStart())}
                aria-label={micActive ? "Stop listening" : "Start listening"}
                aria-pressed={micActive}
              >
                {micActive ? <MicOff /> : <Mic />}
              </Button>
            </TooltipTrigger>
            <TooltipContent>{micActive ? "Stop listening" : "Start listening"}</TooltipContent>
          </Tooltip>
        ) : null}

        <Input
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={connected ? "Command the drone or ask what it sees…" : "Reconnecting — type a command…"}
          aria-label="Command input"
        />

        <Button type="submit" size="icon" className="shrink-0" disabled={text.trim().length === 0} aria-label="Send">
          <SendHorizontal />
        </Button>
      </form>
    </div>
  );
}

const logLevelClass = {
  critical: "border-destructive/70 bg-destructive/15 text-destructive",
  warning: "border-warning/50 bg-warning/10 text-foreground",
  info: "border-border bg-muted/40 text-muted-foreground"
} as const;

/** Compact, full-width log row for safety events and direct operator actions. */
function LogRow({ message }: { message: ChatMessage }) {
  const level = message.level ?? "info";
  const isSafety = message.role === "safety";
  const Icon = isSafety ? ShieldAlert : Hand;
  return (
    <div className={cn("flex items-start gap-2 rounded-lg border px-3 py-2 text-xs leading-relaxed", logLevelClass[level])}>
      <Icon className={cn("mt-0.5 size-3.5 shrink-0", level === "warning" && "text-warning")} />
      <p className="min-w-0 text-pretty wrap-break-word">
        <span className="mr-1.5 font-semibold tracking-wide uppercase">{isSafety ? "Safety" : "Operator"}</span>
        {message.text}
      </p>
    </div>
  );
}
