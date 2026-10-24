import { ArrowUp, Mic, Square } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** Text and push-to-talk input for the AI. */
export function Composer({
  value,
  onChange,
  onSend,
  onMicStart,
  onMicStop,
  micActive,
  awaitingReply,
  voiceEnabled = true,
  inputRef
}: {
  value: string;
  onChange: (value: string) => void;
  onSend: (text: string) => void;
  onMicStart: () => void;
  onMicStop: () => void;
  micActive: boolean;
  awaitingReply: boolean;
  voiceEnabled?: boolean;
  inputRef?: React.Ref<HTMLInputElement>;
}) {
  const id = React.useId();

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = value.trim();
    if (!trimmed) return;
    onSend(trimmed);
    onChange("");
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2 border-t p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <label htmlFor={id} className="text-[13px] font-medium">
          Message TelloAI
        </label>
        <p role="status" className={cn("text-[13px]", micActive ? "text-destructive-text" : "text-muted-foreground")}>
          {micActive ? "Recording. Select Stop recording to send." : awaitingReply ? "TelloAI is working on it…" : ""}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Input
          ref={inputRef}
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder="Fly up 50 cm and look left"
          autoComplete="off"
          className="h-10"
        />
        {voiceEnabled ? (
          <Button
            type="button"
            size="icon-lg"
            variant={micActive ? "destructive" : "outline"}
            onClick={() => (micActive ? onMicStop() : onMicStart())}
            aria-label={micActive ? "Stop recording" : "Start voice message"}
            aria-pressed={micActive}
            title={micActive ? "Stop recording" : "Start voice message"}
          >
            {micActive ? <Square className="fill-current" aria-hidden="true" /> : <Mic aria-hidden="true" />}
          </Button>
        ) : null}
        <Button type="submit" size="icon-lg" disabled={value.trim().length === 0} aria-label="Send">
          <ArrowUp aria-hidden="true" />
        </Button>
      </div>
    </form>
  );
}
