import { recordServerTime } from "./clock";
import type { ClaimOut, Drop, EntryOut, Me, OtpRequestOut, OtpVerifyOut, StepUpOut } from "./types";

const BASE = "/api";
const DEFAULT_TIMEOUT_MS = 10_000;
const CLAIM_TIMEOUT_MS = 8_000;

/** The server answered with the contract error envelope. */
export class ApiError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    message: string,
    readonly retryAfterMs: number | null,
    readonly requestId: string | null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** No usable answer: offline, connection dropped, timeout, or a proxy page instead of JSON. */
export class NetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NetworkError";
  }
}

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  idempotencyKey?: string;
  timeoutMs?: number;
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const startedAt = Date.now();
  let res: Response;
  let data: unknown;
  try {
    res = await fetch(BASE + path, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
    });
    data = await res.json();
  } catch (err) {
    throw new NetworkError(err instanceof Error ? err.message : "network error");
  } finally {
    clearTimeout(timer);
  }

  const serverTime = (data as { server_time?: unknown } | null)?.server_time;
  if (typeof serverTime === "string") recordServerTime(serverTime, startedAt, Date.now());

  if (res.ok) return data as T;
  const env = (data as { error?: { code?: string; message?: string; retry_after_ms?: number } })
    ?.error;
  if (!env?.code) throw new NetworkError(`HTTP ${res.status} without an error envelope`);
  throw new ApiError(
    env.code,
    res.status,
    env.message ?? env.code,
    env.retry_after_ms ?? null,
    res.headers.get("X-Request-ID"),
  );
}

export const api = {
  drop: (dropId: string) => request<Drop>(`/drops/${dropId}`),
  me: (dropId: string) => request<Me>(`/drops/${dropId}/me`),
  requestOtp: (phone: string, deviceId: string) =>
    request<OtpRequestOut>("/auth/otp/request", {
      method: "POST",
      body: { phone, device_id: deviceId },
    }),
  verifyOtp: (requestId: string, otp: string, deviceId: string) =>
    request<OtpVerifyOut>("/auth/otp/verify", {
      method: "POST",
      body: { request_id: requestId, otp, device_id: deviceId },
    }),
  enter: (dropId: string, key: string) =>
    request<EntryOut>(`/drops/${dropId}/entries`, {
      method: "POST",
      body: {},
      idempotencyKey: key,
    }),
  claim: (dropId: string, admissionToken: string, key: string) =>
    request<ClaimOut>(`/drops/${dropId}/claim`, {
      method: "POST",
      body: { admission_token: admissionToken },
      idempotencyKey: key,
      timeoutMs: CLAIM_TIMEOUT_MS,
    }),
  stepUp: (dropId: string, otp: string, key: string) =>
    request<StepUpOut>(`/drops/${dropId}/step-up`, {
      method: "POST",
      body: { otp },
      idempotencyKey: key,
    }),
};
