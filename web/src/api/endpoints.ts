import { CLAIM_TIMEOUT_MS, request } from "./http";
import type { Body, Ok } from "./types";

/** Every call the app makes. Shapes come from the generated OpenAPI types; no ad-hoc fetches. */
export const api = {
  getDrop: (dropId: string) => request<Ok<"getDrop">>(`/drops/${dropId}`),

  getMe: (dropId: string) => request<Ok<"getMe">>(`/drops/${dropId}/me`),

  requestOtp: (body: Body<"requestOtp">) =>
    request<Ok<"requestOtp">>("/auth/otp/request", { method: "POST", body }),

  verifyOtp: (body: Body<"verifyOtp">) =>
    request<Ok<"verifyOtp">>("/auth/otp/verify", { method: "POST", body }),

  createEntry: (dropId: string, idempotencyKey: string) =>
    request<Ok<"createEntry", 201>>(`/drops/${dropId}/entries`, {
      method: "POST",
      body: {} satisfies Body<"createEntry">,
      idempotencyKey,
    }),

  claim: (dropId: string, body: Body<"claim">, idempotencyKey: string) =>
    request<Ok<"claim">>(`/drops/${dropId}/claim`, {
      method: "POST",
      body,
      idempotencyKey,
      timeoutMs: CLAIM_TIMEOUT_MS,
    }),

  stepUp: (dropId: string, body: Body<"stepUp">, idempotencyKey: string) =>
    request<Ok<"stepUp">>(`/drops/${dropId}/step-up`, { method: "POST", body, idempotencyKey }),

  drawProof: (dropId: string) => request<Ok<"publicDrawProof">>(`/drops/${dropId}/draw-proof`),

  admin: {
    listDrops: () => request<Ok<"adminListDrops">>("/admin/drops", { admin: true }),

    createDrop: (body: Body<"adminCreateDrop">) =>
      request<Ok<"adminCreateDrop", 201>>("/admin/drops", { method: "POST", body, admin: true }),

    setPhase: (dropId: string, body: Body<"adminSetPhase">) =>
      request<Ok<"adminSetPhase">>(`/admin/drops/${dropId}/phase`, {
        method: "POST",
        body,
        admin: true,
      }),

    metrics: (dropId: string, windowS = 60) =>
      request<Ok<"adminMetrics">>(`/admin/drops/${dropId}/metrics`, {
        admin: true,
        query: { window_s: windowS },
      }),

    integrity: (dropId: string) =>
      request<Ok<"adminIntegrity">>(`/admin/drops/${dropId}/integrity`, { admin: true }),

    sim: (dropId: string) =>
      request<Ok<"adminSimLatest">>(`/admin/drops/${dropId}/sim`, { admin: true }),

    exportNdjson: (dropId: string) =>
      request<string>(`/admin/drops/${dropId}/export`, { admin: true, text: true }),

    getAbuseConfig: () =>
      request<Ok<"adminGetAbuseConfig">>("/admin/abuse/config", { admin: true }),

    putAbuseConfig: (body: Body<"adminAbuseConfig">) =>
      request<Ok<"adminAbuseConfig">>("/admin/abuse/config", { method: "PUT", body, admin: true }),
  },
};
