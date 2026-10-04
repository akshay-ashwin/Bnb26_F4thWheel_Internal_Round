import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useConnectivity } from "../state/connectivity";
import { clockOffsetMs, resetClock } from "./clock";
import { setAdminKey } from "./config";
import { ApiError, NetworkError } from "./errors";
import { request, setTransport, type Transport } from "./http";

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });

describe("request", () => {
  beforeEach(() => {
    resetClock();
    useConnectivity.getState().set("online");
  });
  afterEach(() => setTransport(null));

  it("sends JSON to /api with the cookie session and returns the parsed body", async () => {
    const transport = vi.fn<Transport>(async () =>
      json(200, { status: "ok", server_time: new Date().toISOString() }),
    );
    setTransport(transport);

    const body = await request<{ status: string }>("/drops/abc/entries", {
      method: "POST",
      body: {},
      idempotencyKey: "key-1",
    });

    expect(body.status).toBe("ok");
    const [url, init] = transport.mock.calls[0] ?? [];
    expect(url).toBe("/api/drops/abc/entries");
    expect(init?.credentials).toBe("same-origin");
    expect(init?.headers).toMatchObject({
      "Idempotency-Key": "key-1",
      "Content-Type": "application/json",
    });
  });

  it("attaches X-Admin-Key only to admin calls", async () => {
    setAdminKey("s3cret");
    const transport = vi.fn<Transport>(async () => json(200, { server_time: "x" }));
    setTransport(transport);
    await request("/admin/drops", { admin: true });
    await request("/drops/abc");
    expect(transport.mock.calls[0]?.[1].headers).toMatchObject({ "X-Admin-Key": "s3cret" });
    expect(transport.mock.calls[1]?.[1].headers).not.toHaveProperty("X-Admin-Key");
  });

  it("turns the error envelope into a typed ApiError", async () => {
    setTransport(async () =>
      json(429, {
        error: { code: "RATE_LIMITED", message: "Slow down", retry_after_ms: 2000 },
        server_time: new Date().toISOString(),
      }),
    );
    const err = await request("/drops/abc/me").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ code: "RATE_LIMITED", status: 429, retryAfterMs: 2000 });
  });

  it("falls back to the Retry-After header when the body has no retry_after_ms", async () => {
    setTransport(async () =>
      json(
        503,
        { error: { code: "SERVICE_UNAVAILABLE", message: "busy" }, server_time: "x" },
        { "Retry-After": "3" },
      ),
    );
    const err = await request("/drops/abc").catch((e: unknown) => e);
    expect(err).toMatchObject({ code: "SERVICE_UNAVAILABLE", retryAfterMs: 3000 });
  });

  it("feeds the clock from server_time on errors too", async () => {
    const ahead = new Date(Date.now() + 7000).toISOString();
    setTransport(async () =>
      json(403, { error: { code: "WINDOW_CLOSED", message: "closed" }, server_time: ahead }),
    );
    await request("/drops/abc/entries", { method: "POST", body: {} }).catch(() => undefined);
    expect(Math.abs(clockOffsetMs() - 7000)).toBeLessThan(200);
  });

  it("raises NetworkError and marks the app as reconnecting when nothing answers", async () => {
    setTransport(async () => {
      throw new TypeError("Failed to fetch");
    });
    const err = await request("/drops/abc").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(NetworkError);
    expect(useConnectivity.getState().status).toBe("reconnecting");
  });

  it("aborts and reports a timeout", async () => {
    setTransport(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => reject(new DOMException("x", "AbortError")));
        }),
    );
    const err = await request("/drops/abc", { timeoutMs: 20 }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(NetworkError);
    expect(err).toMatchObject({ timedOut: true });
  });

  it("treats a non-envelope 502 from a proxy as retryable unavailability", async () => {
    setTransport(async () => new Response("<html>Bad Gateway</html>", { status: 502 }));
    const err = await request("/drops/abc").catch((e: unknown) => e);
    expect(err).toMatchObject({ code: "SERVICE_UNAVAILABLE", status: 502 });
    expect(useConnectivity.getState().status).toBe("reconnecting");
  });
});
