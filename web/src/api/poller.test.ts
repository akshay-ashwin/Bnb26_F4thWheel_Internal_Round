import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createPoller } from "./poller";

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("poller", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setHidden(false);
  });
  afterEach(() => {
    vi.useRealTimers();
    setHidden(false);
  });

  const setup = (delays: number[]) => {
    const fetch = vi.fn(async () => ({ poll_after_ms: delays.shift() ?? 1000 }));
    const onData = vi.fn();
    const poller = createPoller({ fetch, nextDelayMs: (d) => d.poll_after_ms, onData });
    return { fetch, onData, poller };
  };

  it("paces itself by the server's poll_after_ms", async () => {
    const { fetch, poller } = setup([2000, 5000]);
    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1999);
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetch).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(4999);
    expect(fetch).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetch).toHaveBeenCalledTimes(3);
    poller.stop();
  });

  it("pauses while the tab is hidden and fetches at once when it is visible again", async () => {
    const { fetch, poller } = setup([1000, 1000, 1000]);
    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledTimes(1);

    setHidden(true);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetch).toHaveBeenCalledTimes(1);

    setHidden(false);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledTimes(2);
    poller.stop();
  });

  it("fetches at once when the browser comes back online", async () => {
    const { fetch, poller } = setup([30_000, 30_000]);
    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    window.dispatchEvent(new Event("online"));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledTimes(2);
    poller.stop();
  });

  it("refresh() fetches now and never overlaps a request already in flight", async () => {
    const { fetch, poller } = setup([30_000, 30_000]);
    poller.start();
    const both = Promise.all([poller.refresh(), poller.refresh()]);
    await vi.advanceTimersByTimeAsync(0);
    await both;
    expect(fetch).toHaveBeenCalledTimes(1);

    await poller.refresh();
    expect(fetch).toHaveBeenCalledTimes(2);
    poller.stop();
  });

  it("backs off after errors and stops when onError returns false", async () => {
    const fetch = vi.fn(async (): Promise<{ poll_after_ms: number }> => {
      throw new Error("down");
    });
    let keepGoing = true;
    const poller = createPoller({
      fetch,
      nextDelayMs: (d) => d.poll_after_ms,
      onData: vi.fn(),
      onError: () => keepGoing,
      fallbackMs: 1000,
    });
    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetch).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1999);
    expect(fetch).toHaveBeenCalledTimes(2);
    keepGoing = false;
    await vi.advanceTimersByTimeAsync(1);
    expect(fetch).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("does nothing after stop()", async () => {
    const { fetch, poller } = setup([1000]);
    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    poller.stop();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
