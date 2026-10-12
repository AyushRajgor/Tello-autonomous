import { ArrowUp, Mic, Sparkles, Square } from "lucide-react";
import * as React from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

const SUGGESTIONS = [
  "Take off and hover",
  "What do you see?",
  "Scan the room slowly",
  "Find a person and face them",
  "Come back home and land"
];

/**
 * The AI's input: a single command line with push-to-talk. Suggestions fill
 * the field rather than sending, so nothing flies on a stray tap.
 */
export function CommandDeck({
  onSend,
  onMicStart,
  onMicStop,
  micActive,
  connected,
  awaitingReply,
  voiceEnabled = true,
  mode
}: {
  onSend: (text: string) => void;
  onMicStart: () => void;
  onMicStop: () => void;
  micActive: boolean;
  connected: boolean;
  awaitingReply: boolean;
  voiceEnabled?: boolean;
  mode: string;
}) {
  const [text, setText] = React.useState("");
  const inputRef = React.useRef<HTMLInputElement | null>(null);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setText("");
  }

  return (
    <section aria-label="Command the AI" className="panel shrink-0 p-2.5">
      <form onSubmit={submit} className="flex items-center gap-2">
        <span
          className={cn(
            "grid size-10 shrink-0 place-items-center rounded-lg border",
            awaitingReply ? "border-primary/50 bg-primary/10 text-primary" : "bg-sunken text-muted-foreground"
          )}
          aria-hidden="true"
        >
          <Sparkles className={cn("size-4", awaitingReply && "animate-pulse")} />
        </span>

        <div className="relative min-w-0 flex-1">
          <input
            ref={inputRef}
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={
              micActive
                ? "Listening — speak your instruction…"
                : connected
                  ? "Tell the drone what to do, or ask what it sees…"
                  : "Reconnecting — you can still type…"
            }
            aria-label="Command input"
            className="h-10 w-full rounded-lg border bg-sunken px-3 text-sm outline-none placeholder:text-muted-foreground/80 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/30"
          />
        </div>

        {voiceEnabled ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={() => (micActive ? onMicStop() : onMicStart())}
                aria-label={micActive ? "Stop listening" : "Start listening"}
                aria-pressed={micActive}
                className={cn(
                  "relative grid size-10 shrink-0 place-items-center rounded-lg border outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring/40",
                  micActive
                    ? "border-destructive bg-destructive text-white"
                    : "bg-raised text-foreground hover:bg-accent"
                )}
              >
                {micActive ? (
                  <>
                    <span className="absolute inset-0 animate-ping rounded-lg bg-destructive/40" />
                    <Square className="relative size-3.5 fill-current" />
                  </>
                ) : (
                  <Mic className="size-4" />
                )}
              </button>
            </TooltipTrigger>
            <TooltipContent>{micActive ? "Stop listening" : "Push to talk"}</TooltipContent>
          </Tooltip>
        ) : null}

        <button
          type="submit"
          disabled={text.trim().length === 0}
          aria-label="Send"
          className="grid size-10 shrink-0 place-items-center rounded-lg bg-primary text-primary-foreground outline-none transition-opacity focus-visible:ring-[3px] focus-visible:ring-ring/40 disabled:opacity-35"
        >
          <ArrowUp className="size-4" strokeWidth={2.5} />
        </button>
      </form>

      <div className="mt-2 flex items-center gap-2 overflow-x-auto pb-0.5 [scrollbar-width:none]">
        <span className="shrink-0 font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase">
          {awaitingReply ? (
            <span className="text-primary">AI working…</span>
          ) : micActive ? (
            <span className="text-destructive">● Rec</span>
          ) : (
            <>Try</>
          )}
        </span>
        {SUGGESTIONS.map((suggestion) => (
          <button
            key={suggestion}
            type="button"
            onClick={() => {
              setText(suggestion);
              inputRef.current?.focus();
            }}
            className="shrink-0 rounded-full border bg-raised/60 px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
          >
            {suggestion}
          </button>
        ))}
        <span className="ml-auto shrink-0 pl-2 font-mono text-[10px] text-muted-foreground/70 max-sm:hidden">
          {mode === "live" ? "gemini live" : mode === "legacy" ? "function-calling" : ""}
        </span>
      </div>
    </section>
  );
}
