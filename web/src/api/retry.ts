// The one retry policy for every write (Plan 15 §4.6). Retries reuse the caller's
// Idempotency-Key (the caller closes over it), so a retry can never create a second effect.
// Retries exist to survive bad networks, never to be faster: backoff grows, attempts are bounded.
import { ApiError, NetworkError } from "./client";
import { browserOffline, connectivity, rateLimitedUntil, waitForOnline } from "./connectivity";

const BASE_DELAY_MS = 300;
const MAX_DELAY_MS = 5_000;
const MAX_TRANSIENT_ATTEMPTS = 8;
const MAX_RATE_LIMIT_RETRIES = 2;

export interface RetryDeps {
  sleep: (ms: number) => Promise<void>;
  random: () => number;
}

const realDeps: RetryDeps = {
  sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
  random: Math.random,
};

/** Network failure, 5xx, or SERVICE_UNAVAILABLE: nothing was decided, same key is safe. */
export function isTransient(err: unknown): boolean {
  if (err instanceof NetworkError) return true;
  return err instanceof ApiError && (err.status >= 500 || err.code === "SERVICE_UNAVAILABLE");
}

export function backoffMs(attempt: number, retryAfterMs: number | null, random: () => number) {
  const cap = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** attempt);
  return Math.max(retryAfterMs ?? 0, Math.floor(random() * cap));
}

export async function withRetry<T>(fn: () => Promise<T>, deps: RetryDeps = realDeps): Promise<T> {
  let transient = 0;
  let limited = 0;
  for (;;) {
    try {
      const out = await fn();
      connectivity.set("online");
      return out;
    } catch (err) {
      if (isTransient(err)) {
        connectivity.set("reconnecting");
        if (browserOffline()) {
          await waitForOnline(); // waiting for the network is not an attempt
          continue;
        }
        if (++transient >= MAX_TRANSIENT_ATTEMPTS) throw err;
        const after = err instanceof ApiError ? err.retryAfterMs : null;
        await deps.sleep(backoffMs(transient, after, deps.random));
        continue;
      }
      if (err instanceof ApiError && err.code === "RATE_LIMITED") {
        if (++limited > MAX_RATE_LIMIT_RETRIES) throw err;
        const wait = (err.retryAfterMs ?? 1000) + Math.floor(deps.random() * 250);
        if (limited > 1) rateLimitedUntil.set(Date.now() + wait);
        await deps.sleep(wait);
        continue;
      }
      throw err;
    }
  }
}
