import type { DashboardMode, ToolCall } from "./lib/dashboard-state";
import type { DashboardState, TraceSummaryInput } from "./lib/format";

export type ConnectionState = "connecting" | "connected" | "disconnected";

export type VideoStatus = {
  active: boolean;
  source?: "webcam" | "drone" | string;
};

export type DashboardSnapshot = {
  mode: DashboardMode;
  connection: ConnectionState;
  state?: DashboardState;
  videoStatus: VideoStatus;
  tools: ToolCall[];
  traces: TraceSummaryInput[];
};
