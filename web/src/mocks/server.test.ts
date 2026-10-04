import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAdminKey } from "../api/config";
import { api } from "../api/endpoints";
import { NetworkError } from "../api/errors";
import { setTransport, type Transport } from "../api/http";
import { clearIdempotencyKey, idempotencyKey } from "../api/idempotency";
import { withRetry } from "../api/retry";
import { verifyProof } from "../lib/draw";
import { mockControls, mockFetch } from "./server";

const noWait = { sleep: async () => undefined };

async function signIn(phone = "9876543210") {
  const sent = await api.requestOtp({ phone, device_id: "device-12345678" });
  return api.verifyOtp({
    request_id: sent.request_id,
    otp: sent.dev_otp ?? "",
    device_id: "device-12345678",
  });
}

const createDrop = (name: string) =>
  api.admin.createDrop({ name, mode: "fair", capacity: 500, window_s: 600, claim_window_s: 120 });

/** A fresh fair drop, opened, with the signed-in user entered and the draw forced to a win. */
async function drawnDropWithOffer() {
  const { drop_id: dropId } = await createDrop("Test drop");
  await api.admin.setPhase(dropId, { action: "open" });
  await signIn();
  await api.createEntry(dropId, idempotencyKey(dropId, "enter"));
  await mockControls.setOutcome("win");
  await api.admin.setPhase(dropId, { action: "close" });
  await api.admin.setPhase(dropId, { action: "draw" });
  return dropId;
}

describe("demo-mode API (mock of the contract)", () => {
  let calls: { url: string; key: string | null }[];

  beforeEach(async () => {
    calls = [];
    const recording: Transport = (url, init) => {
      calls.push({ url, key: new Headers(init.headers).get("Idempotency-Key") });
      return mockFetch(url, init);
    };
    setTransport(recording);
    setAdminKey("test-admin");
    mockControls.setLatency(0, 0);
    await mockControls.reset();
  });
  afterEach(() => setTransport(null));

  it("requires a session for /me and an idempotency key for claim", async () => {
    const { drop_id: dropId } = await createDrop("Auth");
    await expect(api.getMe(dropId)).rejects.toMatchObject({ code: "UNAUTHENTICATED", status: 401 });
    await signIn();
    await expect(api.claim(dropId, { admission_token: "x" }, "")).rejects.toMatchObject({
      code: "IDEMPOTENCY_KEY_MISSING",
    });
  });

  it("rejects a phone number that is not an Indian mobile", async () => {
    await expect(
      api.requestOtp({ phone: "12345", device_id: "device-12345678" }),
    ).rejects.toMatchObject({ code: "INVALID_PHONE", status: 400 });
  });

  it("gives one entry per identity however many times entry is requested", async () => {
    const { drop_id: dropId } = await createDrop("Once");
    await api.admin.setPhase(dropId, { action: "open" });
    await signIn();
    const first = await api.createEntry(dropId, "k1");
    const again = await api.createEntry(dropId, "k2");
    expect(again.entry_id).toBe(first.entry_id);
  });

  it("walks a fair drop from entry to a claimed seat", async () => {
    const dropId = await drawnDropWithOffer();
    const me = await api.getMe(dropId);
    expect(me.phase).toBe("CLAIMING");
    expect(me.entry?.status).toBe("OFFERED");
    expect(me.entry?.rank).toBeLessThanOrEqual(500);

    const token = me.entry?.admission_token ?? "";
    const seat = await api.claim(
      dropId,
      { admission_token: token },
      idempotencyKey(dropId, "claim"),
    );
    const after = await api.getMe(dropId);
    expect(after.entry?.status).toBe("ALLOCATED");
    expect(after.allocation?.seat_no).toBe(seat.seat_no);
  });

  it("lost response: the retry reuses the same key and ends on the same single seat", async () => {
    const dropId = await drawnDropWithOffer();
    const token = (await api.getMe(dropId)).entry?.admission_token ?? "";
    await mockControls.setFaults({ lostResponseOnce: true });
    const onRetry = vi.fn();

    const key = idempotencyKey(dropId, "claim");
    const seat = await withRetry(
      () => api.claim(dropId, { admission_token: token }, idempotencyKey(dropId, "claim")),
      { ...noWait, onRetry },
    );
    clearIdempotencyKey(dropId, "claim");

    const claims = calls.filter((c) => c.url.endsWith("/claim"));
    expect(claims).toHaveLength(2);
    expect(new Set(claims.map((c) => c.key))).toEqual(new Set([key]));
    expect(onRetry).toHaveBeenCalledWith(expect.objectContaining({ reason: "network" }));

    const me = await api.getMe(dropId);
    expect(me.entry?.status).toBe("ALLOCATED");
    expect(me.allocation).toMatchObject({
      seat_no: seat.seat_no,
      allocation_id: seat.allocation_id,
    });
    const integrity = await api.admin.integrity(dropId);
    expect(integrity).toMatchObject({ oversold: 0, invariant_ok: true });
  });

  it("recovers from one TOKEN_INVALID by fetching a fresh token, with the same key", async () => {
    const dropId = await drawnDropWithOffer();
    let token = "mock.stale.0";
    await mockControls.setFaults({ tokenInvalidOnce: true });
    const seat = await withRetry(
      () => api.claim(dropId, { admission_token: token }, idempotencyKey(dropId, "claim")),
      {
        ...noWait,
        refreshToken: async () => {
          token = (await api.getMe(dropId)).entry?.admission_token ?? "";
        },
      },
    );
    expect(seat.seat_no).toBeGreaterThan(0);
    const keys = calls.filter((c) => c.url.endsWith("/claim")).map((c) => c.key);
    expect(new Set(keys).size).toBe(1);
  });

  it("refuses one key reused for a different action", async () => {
    const dropId = await drawnDropWithOffer();
    const token = (await api.getMe(dropId)).entry?.admission_token ?? "";
    await api.claim(dropId, { admission_token: token }, "shared-key");
    await expect(api.stepUp(dropId, { otp: "000000" }, "shared-key")).rejects.toMatchObject({
      code: "IDEMPOTENCY_KEY_REUSED",
      status: 422,
    });
  });

  it("holds a flagged winner at step-up until the code is entered", async () => {
    const { drop_id: dropId } = await createDrop("Step-up");
    await api.admin.setPhase(dropId, { action: "open" });
    await signIn();
    await api.createEntry(dropId, "enter-key");
    await mockControls.setOutcome("step_up");
    await api.admin.setPhase(dropId, { action: "close" });
    await api.admin.setPhase(dropId, { action: "draw" });

    const me = await api.getMe(dropId);
    expect(me.entry).toMatchObject({
      status: "STEP_UP_REQUIRED",
      step_up_required: true,
      admission_token: null,
    });
    await expect(api.stepUp(dropId, { otp: "000000" }, "wrong")).rejects.toMatchObject({
      code: "OTP_INVALID",
    });
    await api.stepUp(dropId, { otp: me.entry?.dev_otp ?? "" }, "right");
    expect((await api.getMe(dropId)).entry?.status).toBe("OFFERED");
  });

  it("puts a waitlisted entrant behind every seat with a position", async () => {
    const { drop_id: dropId } = await createDrop("Waitlist");
    await api.admin.setPhase(dropId, { action: "open" });
    await signIn();
    await api.createEntry(dropId, "enter-key");
    await mockControls.setOutcome("waitlist");
    await api.admin.setPhase(dropId, { action: "close" });
    await api.admin.setPhase(dropId, { action: "draw" });

    const me = await api.getMe(dropId);
    expect(me.entry).toMatchObject({
      status: "WAITLISTED",
      waitlist_pos: 12,
      admission_token: null,
    });
    await expect(api.claim(dropId, { admission_token: "mock.x.0" }, "k")).rejects.toMatchObject({
      status: 401,
    });
  });

  it("publishes a proof that the browser verifier accepts", async () => {
    const dropId = await drawnDropWithOffer();
    const proof = await api.drawProof(dropId);
    const checks = await verifyProof(proof);
    expect(checks.every((c) => c.ok)).toBe(true);
    const drop = await api.getDrop(dropId);
    expect(drop.seed).toBe(proof.seed);
  });

  it("keeps the seed secret and the proof closed until the draw", async () => {
    const { drop_id: dropId } = await createDrop("Sealed");
    expect((await api.getDrop(dropId)).seed).toBeNull();
    await expect(api.drawProof(dropId)).rejects.toMatchObject({
      code: "INVALID_TRANSITION",
      status: 409,
    });
    await expect(api.admin.setPhase(dropId, { action: "draw" })).rejects.toMatchObject({
      code: "INVALID_TRANSITION",
    });
  });

  it("answers 429 with retry_after_ms and behaves like a dropped connection when offline", async () => {
    const dropId = (await mockControls.get()).dropIds[0] ?? "";
    await mockControls.setFaults({ rateLimit: 1 });
    await expect(api.getDrop(dropId)).rejects.toMatchObject({
      code: "RATE_LIMITED",
      retryAfterMs: 1500,
    });
    await mockControls.setFaults({ offline: true });
    await expect(api.getDrop(dropId)).rejects.toBeInstanceOf(NetworkError);
    await mockControls.setFaults({ offline: false });
    expect((await api.getDrop(dropId)).id).toBe(dropId);
  });
});
