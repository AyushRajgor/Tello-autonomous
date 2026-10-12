export type TrailPoint = { x: number; y: number };

export const MAX_TRAIL_POINTS = 120;

/**
 * Append a dead-reckoned position to the flight trail. Repeated samples are
 * dropped (state is pushed every second whether or not the drone moved), and
 * a jump back to home while grounded starts a fresh trail (new takeoff).
 */
export function appendTrail(trail: TrailPoint[], point: TrailPoint, flying: boolean): TrailPoint[] {
  if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) {
    return trail;
  }
  const last = trail[trail.length - 1];
  if (last && Math.abs(last.x - point.x) < 0.5 && Math.abs(last.y - point.y) < 0.5) {
    return trail;
  }
  if (!flying && point.x === 0 && point.y === 0) {
    return [point];
  }
  return [...trail, point].slice(-MAX_TRAIL_POINTS);
}
