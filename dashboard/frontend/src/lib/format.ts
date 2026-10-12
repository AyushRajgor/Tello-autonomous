export type DashboardState = Record<string, unknown> & {
  position?: Record<string, unknown>;
};

export type TraceSummaryInput = Record<string, unknown> & {
  trace_id?: string;
  user_message?: string;
  total_duration_ms?: number;
  duration_ms?: number;
  spans?: unknown[];
};

export function buildWebSocketUrl(protocol: string, host: string): string {
  const wsProtocol = protocol === "https:" ? "wss:" : "ws:";
  return `${wsProtocol}//${host}/ws`;
}

export function getStateMetric<T>(state: DashboardState | undefined, key: string, fallback: T): T {
  const value = state?.[key];
  return value === undefined || value === null ? fallback : (value as T);
}

export function getPositionParts(state: DashboardState | undefined) {
  const position = state?.position ?? {};
  return {
    x: numberFrom(position.x, position.x_cm, 0),
    y: numberFrom(position.y, position.y_cm, 0),
    z: numberFrom(position.z, position.z_cm, 0)
  };
}

export function formatCentimeters(value: unknown): string {
  const numeric = toNumber(value);
  if (numeric === undefined) {
    return "-";
  }
  return `${Math.round(numeric)} cm`;
}

export function formatDegrees(value: unknown): string {
  const numeric = toNumber(value);
  if (numeric === undefined) {
    return "-";
  }
  return `${Math.round(numeric)} deg`;
}

export function formatSeconds(value: unknown): string {
  const numeric = toNumber(value);
  if (numeric === undefined) {
    return "0s";
  }
  if (numeric < 60) {
    return `${Math.round(numeric)}s`;
  }
  const minutes = Math.floor(numeric / 60);
  const seconds = Math.round(numeric % 60);
  return `${minutes}m ${seconds}s`;
}

export function formatDurationMs(value: unknown): string {
  const numeric = toNumber(value);
  if (numeric === undefined) {
    return "-";
  }
  if (numeric < 1000) {
    return `${Math.round(numeric)}ms`;
  }
  return `${(numeric / 1000).toFixed(2)}s`;
}

export function summarizeTrace(trace: TraceSummaryInput) {
  return {
    id: trace.trace_id ?? "unknown",
    command: trace.user_message ?? "No command captured",
    duration: formatDurationMs(trace.total_duration_ms ?? trace.duration_ms),
    spans: Array.isArray(trace.spans) ? trace.spans.length : 0
  };
}

export function toNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim() !== "") {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : undefined;
  }
  return undefined;
}

function numberFrom(...values: unknown[]): number {
  for (const value of values) {
    const numeric = toNumber(value);
    if (numeric !== undefined) {
      return numeric;
    }
  }
  return 0;
}
