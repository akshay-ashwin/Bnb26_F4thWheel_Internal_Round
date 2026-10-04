import { reportReachable } from "../state/connectivity";
import { recordServerTime } from "./clock";
import { adminKey, mocksEnabled } from "./config";
import { ApiError, NetworkError } from "./errors";
import type { ErrorEnvelope } from "./types";

export const DEFAULT_TIMEOUT_MS = 10_000;
export const CLAIM_TIMEOUT_MS = 8_000;

export type Transport = (url: string, init: RequestInit) => Promise<Response>;

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT";
  body?: unknown;
  query?: Record<string, string | number>;
  idempotencyKey?: string;
  admin?: boolean;
  timeoutMs?: number;
  /** Return the raw text instead of parsed JSON (the NDJSON export). */
  text?: boolean;
}

let transportOverride: Transport | null = null;

/** Tests inject a transport; the app picks the mock or the real `fetch`. */
export function setTransport(transport: Transport | null): void {
  transportOverride = transport;
}

async function resolveTransport(): Promise<Transport> {
  if (transportOverride) return transportOverride;
  if (mocksEnabled()) {
    const { mockFetch } = await import("../mocks/server");
    return mockFetch;
  }
  return (url, init) => fetch(url, init);
}

function isEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const error = (value as { error: unknown }).error;
  return typeof error === "object" && error !== null && "code" in error;
}

function retryAfterHeaderMs(res: Response): number | null {
  const seconds = Number(res.headers.get("Retry-After"));
  return Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : null;
}

function serverTimeOf(payload: unknown, res: Response): string | null {
  if (typeof payload === "object" && payload !== null && "server_time" in payload) {
    const value = (payload as { server_time: unknown }).server_time;
    if (typeof value === "string") return value;
  }
  return res.headers.get("X-Server-Time");
}

/**
 * The one place the app talks to the API: same-origin `/api`, cookie session, JSON in and out.
 * Feeds the clock from `server_time` on every response and turns error envelopes into `ApiError`.
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, query, idempotencyKey, admin, text } = options;
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
  if (admin) headers["X-Admin-Key"] = adminKey() ?? "";

  const search = query
    ? `?${new URLSearchParams(Object.entries(query).map(([k, v]) => [k, String(v)])).toString()}`
    : "";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const startedAt = Date.now();

  let res: Response;
  let raw: string;
  try {
    const transport = await resolveTransport();
    res = await transport(`/api${path}${search}`, {
      method,
      headers,
      credentials: "same-origin",
      signal: controller.signal,
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    raw = await res.text();
  } catch (cause) {
    reportReachable(false);
    const timedOut = controller.signal.aborted;
    throw new NetworkError(
      timedOut ? `Request timed out after ${timeoutMs} ms` : "Network request failed",
      timedOut,
      cause,
    );
  } finally {
    clearTimeout(timer);
  }
  const endedAt = Date.now();

  let payload: unknown = null;
  if (!text || !res.ok) {
    try {
      payload = raw ? JSON.parse(raw) : null;
    } catch {
      payload = null;
    }
  }
  const serverTime = serverTimeOf(payload, res);
  if (serverTime) recordServerTime(serverTime, startedAt, endedAt);

  // A gateway error means the API itself did not answer; anything else proves it is reachable.
  reportReachable(![502, 503, 504].includes(res.status));

  if (res.ok) return (text ? raw : payload) as T;

  if (isEnvelope(payload)) {
    throw new ApiError({
      code: payload.error.code,
      status: res.status,
      message: payload.error.message,
      retryAfterMs: payload.error.retry_after_ms ?? retryAfterHeaderMs(res),
      details: payload.error.details ?? null,
    });
  }
  // Not our envelope: a proxy or the dev server answered instead of the API.
  throw new ApiError({
    code: res.status >= 500 ? "SERVICE_UNAVAILABLE" : "UNKNOWN",
    status: res.status,
    message: `Unexpected response (HTTP ${res.status})`,
    retryAfterMs: retryAfterHeaderMs(res),
  });
}
