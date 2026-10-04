// Server clock sync. Countdowns use serverNow(), never the client clock.
// offset = server_time - midpoint(request start, response end), smoothed with an EMA.

const MAX_RTT_MS = 2000;
const ALPHA = 0.5;

let offsetMs: number | null = null;

export function recordServerTime(serverTime: string, startedAt: number, endedAt: number): void {
  const server = Date.parse(serverTime);
  if (Number.isNaN(server) || endedAt - startedAt > MAX_RTT_MS) return;
  const sample = server - (startedAt + endedAt) / 2;
  offsetMs = offsetMs === null ? sample : offsetMs + ALPHA * (sample - offsetMs);
}

export function serverNow(): number {
  return Date.now() + (offsetMs ?? 0);
}

export function clockOffsetMs(): number {
  return offsetMs ?? 0;
}

export function resetClock(): void {
  offsetMs = null;
}
