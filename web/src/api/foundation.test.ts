import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, NetworkError } from "./client";
import { recordServerTime, resetClock, serverNow } from "./clock";
import { connectivity } from "./connectivity";
import { actionKey, finishAction, isPending, markPending } from "./idempotency";
import { backoffMs, withRetry, type RetryDeps } from "./retry";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("clock sync", () => {
  beforeEach(resetClock);
  afterEach(() => vi.useRealTimers());

  it("converges on a +7 s server offset within 3 responses", () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_000_000);
    for (let i = 0; i < 3; i++) {
      const now = Date.now();
      recordServerTime(new Date(now + 7000 + (i % 2 ? 40 : -40)).toISOString(), now - 50, now + 50);
    }
    expect(Math.abs(serverNow() - (Date.now() + 7000))).toBeLessThan(100);
  });

  it("ignores samples with a round trip over 2 s", () => {
    recordServerTime(new Date(Date.now() + 60_000).toISOString(), 0, 5000);
    expect(Math.abs(serverNow() - Date.now())).toBeLessThan(50);
  });
});

describe("idempotency keys", () => {
  beforeEach(() => sessionStorage.clear());

  it("one UUID per (drop, action), stable until finished", () => {
    const k = actionKey("d1", "claim");
    expect(k).toMatch(UUID);
    expect(actionKey("d1", "claim")).toBe(k);
    expect(actionKey("d1", "enter")).not.toBe(k);
    expect(actionKey("d2", "claim")).not.toBe(k);
    markPending("d1", "claim");
    expect(isPending("d1", "claim")).toBe(true);
    finishAction("d1", "claim");
    expect(isPending("d1", "claim")).toBe(false);
    expect(actionKey("d1", "claim")).not.toBe(k);
  });

  it("survives a reload (sessionStorage)", () => {
    const k = actionKey("d1", "claim");
    expect(sessionStorage.getItem("idem:d1:claim")).toBe(k);
  });
});

describe("retry policy", () => {
  const deps: RetryDeps = { sleep: vi.fn(async () => {}), random: () => 0.5 };
  beforeEach(() => connectivity.set("online"));

  it("network error and 503 retry (same closure, so same key) then succeed", async () => {
    const fn = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new NetworkError("down"))
      .mockRejectedValueOnce(new ApiError("SERVICE_UNAVAILABLE", 503, "x", 1000, null))
      .mockResolvedValue("ok");
    await expect(withRetry(fn, deps)).resolves.toBe("ok");
    expect(fn).toHaveBeenCalledTimes(3);
    expect(connectivity.get()).toBe("online");
  });

  it("shows reconnecting after the first failure", async () => {
    let seen = "";
    const fn = vi.fn(async () => {
      if (fn.mock.calls.length === 1) throw new NetworkError("down");
      seen = connectivity.get();
      return 1;
    });
    await withRetry(fn, deps);
    expect(seen).toBe("reconnecting");
  });

  it("transient retries are bounded", async () => {
    const fn = vi.fn(async () => {
      throw new NetworkError("down");
    });
    await expect(withRetry(fn, deps)).rejects.toBeInstanceOf(NetworkError);
    expect(fn).toHaveBeenCalledTimes(8);
  });

  it("RATE_LIMITED waits retry_after_ms and retries at most twice", async () => {
    const sleep = vi.fn(async () => {});
    const fn = vi.fn(async () => {
      throw new ApiError("RATE_LIMITED", 429, "x", 2000, null);
    });
    await expect(withRetry(fn, { sleep, random: () => 0 })).rejects.toBeInstanceOf(ApiError);
    expect(fn).toHaveBeenCalledTimes(3);
    expect(sleep).toHaveBeenCalledWith(2000);
  });

  it.each(["NOT_OFFERED", "OFFER_EXPIRED", "SOLD_OUT", "TOKEN_INVALID", "UNAUTHENTICATED"])(
    "%s is not retried",
    async (code) => {
      const fn = vi.fn(async () => {
        throw new ApiError(code, 409, "x", null, null);
      });
      await expect(withRetry(fn, deps)).rejects.toBeInstanceOf(ApiError);
      expect(fn).toHaveBeenCalledTimes(1);
    },
  );

  it("backoff honours Retry-After and caps at 5 s", () => {
    expect(backoffMs(1, 3000, () => 0)).toBe(3000);
    expect(backoffMs(20, null, () => 0.999)).toBeLessThan(5000);
  });
});
