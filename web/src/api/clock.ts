/**
 * Server clock sync. Every response carries `server_time`; we keep a smoothed offset between the
 * server's clock and this device's clock so countdowns are driven by the server, not the client.
 */
const MAX_RTT_MS = 2000;
const ALPHA = 0.3;
/** A jump this large means the device clock changed; smoothing would take too long to recover. */
const SNAP_MS = 1000;

let offsetMs = 0;
let samples = 0;

/** Record one response. `startedAt`/`endedAt` are `Date.now()` around the request. */
export function recordServerTime(serverTime: string, startedAt: number, endedAt: number): void {
  const server = Date.parse(serverTime);
  const rtt = endedAt - startedAt;
  if (Number.isNaN(server) || rtt < 0 || rtt > MAX_RTT_MS) return;
  const sample = server - (startedAt + rtt / 2);
  if (samples === 0 || Math.abs(sample - offsetMs) > SNAP_MS) {
    offsetMs = sample;
  } else {
    offsetMs += ALPHA * (sample - offsetMs);
  }
  samples += 1;
}

/** The server's "now" in epoch milliseconds. Use this for every countdown. */
export function serverNow(): number {
  return Date.now() + offsetMs;
}

export function clockOffsetMs(): number {
  return offsetMs;
}

export function resetClock(): void {
  offsetMs = 0;
  samples = 0;
}
