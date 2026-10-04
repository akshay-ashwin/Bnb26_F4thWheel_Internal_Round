import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clockOffsetMs, recordServerTime, resetClock, serverNow } from "./clock";

const T0 = Date.parse("2026-10-04T10:00:00.000Z");

describe("server clock sync", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(T0);
    resetClock();
  });
  afterEach(() => vi.useRealTimers());

  /** One response from a server whose clock is `skewMs` ahead, with the given round trip. */
  const respond = (skewMs: number, rttMs: number) => {
    const startedAt = Date.now();
    vi.advanceTimersByTime(rttMs);
    const serverTime = new Date(startedAt + rttMs / 2 + skewMs).toISOString();
    recordServerTime(serverTime, startedAt, Date.now());
  };

  it("converges on a server that is 7 s ahead within 3 responses", () => {
    respond(7000, 80);
    respond(7000, 120);
    respond(7000, 40);
    expect(Math.abs(clockOffsetMs() - 7000)).toBeLessThan(50);
    expect(Math.abs(serverNow() - (Date.now() + 7000))).toBeLessThan(50);
  });

  it("ignores samples with a round trip over 2 s", () => {
    respond(7000, 60);
    const before = clockOffsetMs();
    respond(-20_000, 2500);
    expect(clockOffsetMs()).toBe(before);
  });

  it("smooths small jitter instead of jumping to each sample", () => {
    respond(1000, 50);
    respond(1200, 50);
    expect(clockOffsetMs()).toBeGreaterThan(1000);
    expect(clockOffsetMs()).toBeLessThan(1200);
  });

  it("ignores an unparseable server_time", () => {
    recordServerTime("not a date", T0, T0 + 10);
    expect(clockOffsetMs()).toBe(0);
  });
});
