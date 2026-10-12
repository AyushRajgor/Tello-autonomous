import type { DashboardState, TraceSummaryInput } from "./format";

export type ChatRole = "user" | "assistant" | "system" | string;

export type ChatMessage = {
  id: string;
  role: ChatRole;
  text: string;
  receivedAt: number;
};

export type ToolCall = {
  id: string;
  name: string;
  status: "ok" | "error" | string;
  args: unknown;
  result: unknown;
  receivedAt: number;
};

export type DashboardMode = "live" | "legacy" | "unknown";

export type DashboardModel = {
  mode: DashboardMode;
  state?: DashboardState;
  messages: ChatMessage[];
  tools: ToolCall[];
  traces: TraceSummaryInput[];
  frameSrc?: string;
};

export type DashboardMessage =
  | { type: "state"; data: DashboardState }
  | { type: "mode"; mode: DashboardMode }
  | { type: "chat"; role: ChatRole; text: string }
  | { type: "tool"; name: string; status: string; args?: unknown; result?: unknown }
  | { type: "trace"; data: TraceSummaryInput }
  | { type: "frame"; image_base64: string }
  | { type: string; [key: string]: unknown };

const MAX_MESSAGES = 80;
const MAX_TOOLS = 40;
const MAX_TRACES = 60;

export function createDashboardModel(): DashboardModel {
  return {
    mode: "unknown",
    messages: [],
    tools: [],
    traces: []
  };
}

export function reduceDashboardMessage(model: DashboardModel, message: DashboardMessage): DashboardModel {
  switch (message.type) {
    case "state": {
      const payload = message as { data?: DashboardState };
      return { ...model, state: payload.data };
    }
    case "mode": {
      const payload = message as { mode?: DashboardMode };
      return { ...model, mode: payload.mode ?? "unknown" };
    }
    case "chat": {
      const payload = message as { role?: ChatRole; text?: string };
      return {
        ...model,
        messages: [
          ...model.messages,
          {
            id: cryptoId("msg"),
            role: payload.role ?? "system",
            text: payload.text ?? "",
            receivedAt: Date.now()
          }
        ].slice(-MAX_MESSAGES)
      };
    }
    case "tool": {
      const payload = message as { name?: string; status?: string; args?: unknown; result?: unknown };
      return {
        ...model,
        tools: [
          {
            id: cryptoId("tool"),
            name: payload.name ?? "unknown",
            status: payload.status ?? "ok",
            args: payload.args ?? {},
            result: payload.result ?? null,
            receivedAt: Date.now()
          },
          ...model.tools
        ].slice(0, MAX_TOOLS)
      };
    }
    case "trace": {
      const payload = message as { data?: TraceSummaryInput };
      return {
        ...model,
        traces: payload.data ? [payload.data, ...model.traces].slice(0, MAX_TRACES) : model.traces
      };
    }
    case "frame": {
      const payload = message as { image_base64?: string };
      return {
        ...model,
        frameSrc: payload.image_base64 ? `data:image/jpeg;base64,${payload.image_base64}` : model.frameSrc
      };
    }
    default:
      return model;
  }
}

export function mergeFetchedTraces(model: DashboardModel, traces: TraceSummaryInput[]): DashboardModel {
  return {
    ...model,
    traces: traces.slice(0, MAX_TRACES)
  };
}

function cryptoId(prefix: string): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
