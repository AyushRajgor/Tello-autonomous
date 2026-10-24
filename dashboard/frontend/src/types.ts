import type { DashboardMode, ToolCall } from "./lib/dashboard-state";
import type { DashboardState, TraceSummaryInput } from "./lib/format";

export type ConnectionState = "connecting" | "connected" | "disconnected";

export type VideoStatus = {
  active: boolean;
  source?: "webcam" | "drone" | string;
};

export type DashboardSnapshot = {
  mode: DashboardMode;
  simulated: boolean;
  connection: ConnectionState;
  state?: DashboardState;
  videoStatus: VideoStatus;
  tools: ToolCall[];
  traces: TraceSummaryInput[];
};

/** Severity of a safety watchdog event (server `{"type": "safety"}`). */
export type SafetyLevel = "info" | "warning" | "critical";

/**
 * Direct operator actions. Sent as `{"type": "operator", action, args}` and
 * executed by the server WITHOUT the AI (toolkit.operator_command).
 */
export type OperatorAction =
  | "hover"
  | "land"
  | "emergency_stop"
  | "resume"
  | "takeoff"
  | "move"
  | "rotate"
  | "flip";

export type MoveDirection = "forward" | "back" | "left" | "right" | "up" | "down";
export type FlipDirection = "forward" | "back" | "left" | "right";

export type OperatorArgs =
  | { direction: MoveDirection; cm: number }
  | { degrees: number }
  | { direction: FlipDirection }
  | Record<string, never>;

export type OperatorResult = {
  success: boolean;
  message?: string;
  error?: string;
  [key: string]: unknown;
};

export type OperatorSender = (action: OperatorAction, args?: OperatorArgs) => void;
