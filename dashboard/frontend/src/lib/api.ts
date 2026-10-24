import type { TraceSummaryInput } from "./format";
import type { OperatorAction, OperatorArgs, VideoStatus } from "../types";

/**
 * Shared-secret passthrough: when the dashboard is opened as
 * `http://host:8080/?token=...`, the same token is attached to the
 * WebSocket URL and every API request (server enforces DASHBOARD_TOKEN).
 */
export function getDashboardToken(search: string = typeof window !== "undefined" ? window.location.search : ""): string | null {
  try {
    const token = new URLSearchParams(search).get("token");
    return token && token.length > 0 ? token : null;
  } catch {
    return null;
  }
}

export function withToken(url: string, token: string | null = getDashboardToken()): string {
  if (!token) {
    return url;
  }
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(token)}`;
}

export async function fetchVideoStatus(signal?: AbortSignal): Promise<VideoStatus> {
  const response = await fetch(withToken("/video-status"), { signal });
  if (!response.ok) {
    throw new Error(`Video status failed: ${response.status}`);
  }
  return response.json();
}

export async function fetchTraces(limit = 50, signal?: AbortSignal): Promise<TraceSummaryInput[]> {
  const response = await fetch(withToken(`/api/traces?limit=${limit}`), { signal });
  if (!response.ok) {
    throw new Error(`Trace fetch failed: ${response.status}`);
  }
  const payload = (await response.json()) as { traces?: TraceSummaryInput[] };
  return Array.isArray(payload.traces) ? payload.traces : [];
}

export function sendJson(socket: WebSocket | null, payload: unknown): boolean {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return false;
  }
  socket.send(JSON.stringify(payload));
  return true;
}

/** Direct operator control — executed by the server without the AI. */
export function sendOperator(socket: WebSocket | null, action: OperatorAction, args?: OperatorArgs): boolean {
  return sendJson(socket, args ? { type: "operator", action, args } : { type: "operator", action });
}
