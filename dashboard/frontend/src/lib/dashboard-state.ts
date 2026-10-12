import type { DashboardState, TraceSummaryInput } from "./format";
import type { OperatorAction, OperatorResult, SafetyLevel } from "../types";

export type ChatRole = "user" | "assistant" | "system" | "safety" | "operator" | string;

export type ChatMessage = {
  id: string;
  role: ChatRole;
  text: string;
  receivedAt: number;
  /** Severity for safety/operator log rows. */
  level?: SafetyLevel;
};

export type ToolCall = {
  id: string;
  name: string;
  status: "ok" | "error" | string;
  args: unknown;
  result: unknown;
  receivedAt: number;
};

/** A toast-style alert (watchdog event, failed operator action, offline socket). */
export type SafetyAlert = {
  id: string;
  level: SafetyLevel;
  code: string;
  message: string;
  receivedAt: number;
};

export type DashboardMode = "live" | "legacy" | "unknown";

export type DashboardModel = {
  mode: DashboardMode;
  state?: DashboardState;
  messages: ChatMessage[];
  tools: ToolCall[];
  traces: TraceSummaryInput[];
  /** Newest first. */
  alerts: SafetyAlert[];
  frameSrc?: string;
};

export type DashboardMessage =
  | { type: "state"; data: DashboardState }
  | { type: "mode"; mode: DashboardMode }
  | { type: "chat"; role: ChatRole; text: string }
  | { type: "tool"; name: string; status: string; args?: unknown; result?: unknown }
  | { type: "trace"; data: TraceSummaryInput }
  | { type: "frame"; image_base64: string }
  | { type: "safety"; level: SafetyLevel; code: string; message: string }
  | { type: "operator_result"; action: OperatorAction | string; args?: Record<string, unknown>; result: OperatorResult }
  | { type: string; [key: string]: unknown };

const MAX_MESSAGES = 80;
const MAX_TOOLS = 40;
const MAX_TRACES = 60;
const MAX_ALERTS = 4;

/** Operator actions that put the AI on hold (server sets state.operator_hold). */
const HOLD_ACTIONS = new Set<string>(["hover", "land", "emergency_stop"]);

const OPERATOR_LABELS: Record<string, string> = {
  hover: "STOP",
  land: "Land",
  emergency_stop: "E-STOP",
  resume: "Resume AI",
  takeoff: "Takeoff",
  move: "Move",
  rotate: "Rotate",
  flip: "Flip"
};

export function createDashboardModel(): DashboardModel {
  return {
    mode: "unknown",
    messages: [],
    tools: [],
    traces: [],
    alerts: []
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
      return appendMessage(model, { role: payload.role ?? "system", text: payload.text ?? "" });
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
    case "safety": {
      const payload = message as { level?: string; code?: string; message?: string };
      const level = normalizeLevel(payload.level);
      const code = payload.code ?? "safety";
      const text = payload.message ?? "";
      let next = pushAlert(model, { level, code, message: text });
      next = appendMessage(next, { role: "safety", text, level });
      // Reflect link status immediately; the follow-up state push confirms it.
      if (code === "link_lost") {
        next = patchState(next, { link_ok: false });
      } else if (code === "link_restored") {
        next = patchState(next, { link_ok: true });
      }
      return next;
    }
    case "operator_result": {
      const payload = message as { action?: string; args?: Record<string, unknown>; result?: OperatorResult };
      const action = payload.action ?? "unknown";
      const result = payload.result ?? { success: false, error: "No result" };
      const ok = result.success !== false;
      const label = describeOperatorAction(action, payload.args);
      const detail = ok ? result.message ?? "done" : result.error ?? "failed";
      let next = appendMessage(model, {
        role: "operator",
        text: `${label}: ${detail}`,
        level: ok ? "info" : HOLD_ACTIONS.has(action) ? "critical" : "warning"
      });
      if (!ok) {
        next = pushAlert(next, {
          level: HOLD_ACTIONS.has(action) ? "critical" : "warning",
          code: `operator_${action}_failed`,
          message: `${label} failed: ${detail}`
        });
      } else if (HOLD_ACTIONS.has(action)) {
        next = patchState(next, { operator_hold: true });
      } else if (action === "resume") {
        next = patchState(next, { operator_hold: false });
      }
      return next;
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

export function pushAlert(
  model: DashboardModel,
  alert: { level: SafetyLevel; code: string; message: string }
): DashboardModel {
  return {
    ...model,
    alerts: [{ id: cryptoId("alert"), receivedAt: Date.now(), ...alert }, ...model.alerts].slice(0, MAX_ALERTS)
  };
}

export function dismissAlert(model: DashboardModel, id: string): DashboardModel {
  return { ...model, alerts: model.alerts.filter((alert) => alert.id !== id) };
}

/** True while an operator STOP/land/E-STOP is holding the AI. */
export function isOperatorHold(state: DashboardState | undefined): boolean {
  return state?.operator_hold === true;
}

/** True only when the server explicitly reports the drone link as lost. */
export function isLinkLost(state: DashboardState | undefined): boolean {
  return state?.link_ok === false;
}

export function describeOperatorAction(action: string, args?: Record<string, unknown>): string {
  const label = OPERATOR_LABELS[action] ?? action;
  if (action === "move" && args) {
    const direction = typeof args.direction === "string" ? args.direction : "forward";
    const cm = args.cm ?? 50;
    return `Move ${direction} ${cm} cm`;
  }
  if (action === "rotate" && args) {
    const degrees = Number(args.degrees ?? 90);
    return `Rotate ${degrees >= 0 ? "right" : "left"} ${Math.abs(degrees)}°`;
  }
  if (action === "flip" && args && typeof args.direction === "string") {
    return `Flip ${args.direction}`;
  }
  return label;
}

function appendMessage(
  model: DashboardModel,
  message: { role: ChatRole; text: string; level?: SafetyLevel }
): DashboardModel {
  return {
    ...model,
    messages: [
      ...model.messages,
      {
        id: cryptoId("msg"),
        receivedAt: Date.now(),
        ...message
      }
    ].slice(-MAX_MESSAGES)
  };
}

function patchState(model: DashboardModel, patch: Partial<DashboardState>): DashboardModel {
  return { ...model, state: { ...(model.state ?? {}), ...patch } as DashboardState };
}

function normalizeLevel(level: unknown): SafetyLevel {
  return level === "critical" || level === "warning" ? level : "info";
}

function cryptoId(prefix: string): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
