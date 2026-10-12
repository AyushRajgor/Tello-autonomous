import type { TraceSummaryInput } from "./format";
import type { VideoStatus } from "../types";

export async function fetchVideoStatus(signal?: AbortSignal): Promise<VideoStatus> {
  const response = await fetch("/video-status", { signal });
  if (!response.ok) {
    throw new Error(`Video status failed: ${response.status}`);
  }
  return response.json();
}

export async function fetchTraces(limit = 50, signal?: AbortSignal): Promise<TraceSummaryInput[]> {
  const response = await fetch(`/api/traces?limit=${limit}`, { signal });
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
