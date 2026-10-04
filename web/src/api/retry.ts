import { ApiError, NetworkError } from "./errors";

export type RetryReason = "network" | "unavailable" | "rate_limited" | "token";

export interface RetryOptions {
  /** Total tries allowed for network errors, timeouts and 5xx. */
  maxAttempts?: number;
  /** Extra tries allowed after a 429. */
  maxRateLimitRetries?: number;
  /** On `TOKEN_INVALID`: fetch a fresh admission token (from `/me`). Used at most once. */
  refreshToken?: () => Promise<void>;
  onRetry?: (info: { reason: RetryReason; attempt: number; delayMs: number }) => void;
  sleep?: (ms: number) => Promise<void>;
  random?: () => number;
}

const BASE_MS = 300;
const CAP_MS = 5000;

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function transientReason(err: unknown): RetryReason | null {
  if (err instanceof NetworkError) return "network";
  if (err instanceof ApiError) {
    if ([502, 503, 504].includes(err.status) || err.code === "SERVICE_UNAVAILABLE") {
      return "unavailable";
    }
    if (err.code === "INTERNAL") return "unavailable";
  }
  return null;
}

/**
 * The single retry policy for every write. The caller passes the SAME idempotency key on every
 * attempt, so a retry can only repeat the same action, never create a second one. Attempts are
 * bounded and spaced out: retrying is for surviving a bad network, not for being faster.
 */
export async function withRetry<T>(
  run: (attempt: number) => Promise<T>,
  options: RetryOptions = {},
): Promise<T> {
  const maxAttempts = options.maxAttempts ?? 5;
  const maxRateLimitRetries = options.maxRateLimitRetries ?? 2;
  const sleep = options.sleep ?? defaultSleep;
  const random = options.random ?? Math.random;

  let transientFailures = 0;
  let rateLimitRetries = 0;
  let tokenRefreshed = false;

  for (let attempt = 1; ; attempt += 1) {
    try {
      return await run(attempt);
    } catch (err) {
      let reason: RetryReason;
      let delayMs: number;

      const transient = transientReason(err);
      if (transient) {
        transientFailures += 1;
        if (transientFailures >= maxAttempts) throw err;
        reason = transient;
        // Exponential backoff with full jitter, never shorter than the server asked for.
        const backoff = random() * Math.min(CAP_MS, BASE_MS * 2 ** (transientFailures - 1));
        const asked = err instanceof ApiError ? (err.retryAfterMs ?? 0) : 0;
        delayMs = Math.max(backoff, asked);
      } else if (err instanceof ApiError && err.status === 429) {
        rateLimitRetries += 1;
        if (rateLimitRetries > maxRateLimitRetries) throw err;
        reason = "rate_limited";
        delayMs = (err.retryAfterMs ?? 1000) + random() * 250;
      } else if (
        err instanceof ApiError &&
        err.code === "TOKEN_INVALID" &&
        options.refreshToken &&
        !tokenRefreshed
      ) {
        tokenRefreshed = true;
        reason = "token";
        delayMs = 0;
        await options.refreshToken();
      } else {
        throw err;
      }

      options.onRetry?.({ reason, attempt, delayMs });
      if (delayMs > 0) await sleep(delayMs);
    }
  }
}
