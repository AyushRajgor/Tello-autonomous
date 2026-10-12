import type { ChatMessage, ToolCall } from "./dashboard-state";

export type TimelineEntry =
  | { kind: "message"; id: string; at: number; message: ChatMessage }
  | { kind: "tool"; id: string; at: number; tool: ToolCall };

/**
 * One chronological mission log: what was said (user, AI, system), what the
 * AI did (tool calls), and what the operator and safety watchdog did.
 * Messages arrive oldest-first and tools newest-first; ties keep messages
 * ahead of tools so a reply lands after the command that caused it.
 */
export function buildTimeline(messages: ChatMessage[], tools: ToolCall[]): TimelineEntry[] {
  const entries: TimelineEntry[] = [
    ...messages.map((message) => ({ kind: "message" as const, id: message.id, at: message.receivedAt, message })),
    ...tools.map((tool) => ({ kind: "tool" as const, id: tool.id, at: tool.receivedAt, tool }))
  ];
  return entries
    .map((entry, index) => ({ entry, index }))
    .sort((a, b) => a.entry.at - b.entry.at || a.index - b.index)
    .map(({ entry }) => entry);
}

/** `move(direction: forward, cm: 50)` — a one-line signature for a tool call. */
export function formatToolSignature(tool: Pick<ToolCall, "name" | "args">): string {
  const args = tool.args;
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    return `${tool.name}()`;
  }
  const parts = Object.entries(args as Record<string, unknown>).map(([key, value]) => {
    const text = typeof value === "string" ? value : JSON.stringify(value);
    return `${key}: ${text && text.length > 24 ? `${text.slice(0, 23)}…` : text}`;
  });
  return `${tool.name}(${parts.join(", ")})`;
}

export function formatClock(at: number): string {
  const date = new Date(at);
  return [date.getHours(), date.getMinutes(), date.getSeconds()].map((n) => String(n).padStart(2, "0")).join(":");
}
