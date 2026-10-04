import { describe, expect, it, vi } from "vitest";

import { ApiError, NetworkError } from "./errors";
import { withRetry, type RetryOptions } from "./retry";
import type { ErrorCode } from "./types";

const apiError = (status: number, code: ErrorCode, retryAfterMs: number | null = null) =>
  new ApiError({ code, status, message: code, retryAfterMs });

/** Runs `withRetry` against a scripted list of outcomes and records every wait. */
interface Played {
  value?: string;
  error?: unknown;
  calls: number;
  waits: number[];
}

async function play(outcomes: unknown[], extra: RetryOptions = {}): Promise<Played> {
  const waits: number[] = [];
  const run = vi.fn(async () => {
    const next = outcomes.shift();
    if (next === "ok" || next === undefined) return "done";
    throw next;
  });
  const result = await withRetry(run, {
    sleep: async (ms) => {
      waits.push(ms);
    },
    random: () => 0.5,
    ...extra,
  }).then(
    (value): Pick<Played, "value" | "error"> => ({ value }),
    (error: unknown): Pick<Played, "value" | "error"> => ({ error }),
  );
  return { ...result, calls: run.mock.calls.length, waits };
}

describe("retry policy", () => {
  it("returns straight away on success", async () => {
    expect(await play(["ok"])).toMatchObject({ value: "done", calls: 1, waits: [] });
  });

  it("retries a network error with exponential backoff and jitter", async () => {
    const r = await play([new NetworkError("down"), new NetworkError("down"), "ok"]);
    expect(r).toMatchObject({ value: "done", calls: 3 });
    // 0.5 * 300, then 0.5 * 600
    expect(r.waits).toEqual([150, 300]);
  });

  it("retries a timeout the same way", async () => {
    const r = await play([new NetworkError("timeout", true), "ok"]);
    expect(r).toMatchObject({ value: "done", calls: 2 });
  });

  it.each([502, 503, 504])("retries HTTP %i", async (status) => {
    const r = await play([apiError(status, "SERVICE_UNAVAILABLE"), "ok"]);
    expect(r).toMatchObject({ value: "done", calls: 2 });
  });

  it("waits at least as long as the server's Retry-After", async () => {
    const r = await play([apiError(503, "SERVICE_UNAVAILABLE", 4000), "ok"]);
    expect(r.waits).toEqual([4000]);
  });

  it("caps the backoff at 5 s", async () => {
    const down = () => new NetworkError("down");
    const r = await play([down(), down(), down(), down(), down(), down(), "ok"], {
      maxAttempts: 8,
      random: () => 1,
    });
    expect(Math.max(...r.waits)).toBe(5000);
  });

  it("gives up after the bounded number of attempts and surfaces the error", async () => {
    const down = () => new NetworkError("down");
    const r = await play([down(), down(), down(), down(), down(), down()]);
    expect(r.calls).toBe(5);
    expect(r.error).toBeInstanceOf(NetworkError);
  });

  it("waits retry_after_ms plus jitter on 429, at most twice", async () => {
    const limited = () => apiError(429, "RATE_LIMITED", 2000);
    const ok = await play([limited(), limited(), "ok"]);
    expect(ok).toMatchObject({ value: "done", calls: 3 });
    expect(ok.waits).toEqual([2125, 2125]);

    const stuck = await play([limited(), limited(), limited(), "ok"]);
    expect(stuck.calls).toBe(3);
    expect(stuck.error).toMatchObject({ code: "RATE_LIMITED" });
  });

  it("refreshes the token once on TOKEN_INVALID, then surfaces the error", async () => {
    const refreshToken = vi.fn(async () => undefined);
    const recovered = await play([apiError(401, "TOKEN_INVALID"), "ok"], { refreshToken });
    expect(recovered).toMatchObject({ value: "done", calls: 2, waits: [] });
    expect(refreshToken).toHaveBeenCalledTimes(1);

    const again = await play([apiError(401, "TOKEN_INVALID"), apiError(401, "TOKEN_INVALID")], {
      refreshToken,
    });
    expect(again.calls).toBe(2);
    expect(again.error).toMatchObject({ code: "TOKEN_INVALID" });
  });

  it("does not retry TOKEN_INVALID when there is no token to refresh", async () => {
    const r = await play([apiError(401, "TOKEN_INVALID"), "ok"]);
    expect(r.calls).toBe(1);
  });

  it.each<[number, ErrorCode]>([
    [401, "UNAUTHENTICATED"],
    [403, "NOT_OFFERED"],
    [409, "SOLD_OUT"],
    [409, "OFFER_EXPIRED"],
    [423, "STEP_UP_REQUIRED"],
    [422, "IDEMPOTENCY_KEY_REUSED"],
    [400, "VALIDATION_ERROR"],
  ])("never retries %i %s", async (status, code) => {
    const r = await play([apiError(status, code), "ok"]);
    expect(r.calls).toBe(1);
    expect(r.error).toMatchObject({ code });
  });

  it("reports each retry so the UI can show 'Reconnecting…'", async () => {
    const onRetry = vi.fn();
    await play([new NetworkError("down"), apiError(429, "RATE_LIMITED", 100), "ok"], { onRetry });
    expect(onRetry.mock.calls.map(([info]) => info.reason)).toEqual(["network", "rate_limited"]);
  });
});
