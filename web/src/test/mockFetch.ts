import { vi } from "vitest";

import { T0 } from "./fixtures";

export interface Call {
  method: string;
  path: string;
  body: unknown;
  idempotencyKey: string | null;
}

type Reply = { status?: number; body: unknown } | "network-error";
type Handler = (call: Call, n: number) => Reply;

/**
 * Routes `fetch("/api/...")` to handlers keyed by "METHOD /path". Each handler sees the call
 * and how many times that route was hit before (0-based). Unknown routes fail the test.
 */
export function mockFetch(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  const hits = new Map<string, number>();
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const path = url.replace(/^\/api/, "");
    const headers = (init.headers ?? {}) as Record<string, string>;
    const call: Call = {
      method,
      path,
      body: init.body ? JSON.parse(String(init.body)) : undefined,
      idempotencyKey: headers["Idempotency-Key"] ?? null,
    };
    calls.push(call);
    const route = `${method} ${path}`;
    const handler = routes[route];
    if (!handler) throw new Error(`unmocked route ${route}`);
    const n = hits.get(route) ?? 0;
    hits.set(route, n + 1);
    const reply = handler(call, n);
    if (reply === "network-error") throw new TypeError("Failed to fetch");
    const body = { server_time: T0, ...(reply.body as object) };
    return new Response(JSON.stringify(body), {
      status: reply.status ?? 200,
      headers: { "Content-Type": "application/json", "X-Request-ID": "req-test" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, of: (route: string) => calls.filter((c) => `${c.method} ${c.path}` === route) };
}

export function apiError(status: number, code: string, retryAfterMs?: number): Reply {
  return { status, body: { error: { code, message: code, retry_after_ms: retryAfterMs } } };
}
