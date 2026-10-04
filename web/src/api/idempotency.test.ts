import { describe, expect, it } from "vitest";

import { clearIdempotencyKey, idempotencyKey, isPending, markPending } from "./idempotency";

const DROP = "6f1c2a9e-4b1d-4c7a-9a55-0d2f6b1e7a01";

describe("idempotency keys", () => {
  it("returns the same key for every retry of the same action", () => {
    const first = idempotencyKey(DROP, "claim");
    expect(first).toMatch(/^[0-9a-f-]{36}$/);
    expect(idempotencyKey(DROP, "claim")).toBe(first);
  });

  it("survives a page refresh because it lives in sessionStorage", () => {
    const first = idempotencyKey(DROP, "claim");
    // A refresh keeps sessionStorage and throws away everything in memory.
    expect(window.sessionStorage.getItem(`idem:${DROP}:claim`)).toBe(first);
    expect(idempotencyKey(DROP, "claim")).toBe(first);
  });

  it("uses different keys for different actions and different drops", () => {
    const claim = idempotencyKey(DROP, "claim");
    expect(idempotencyKey(DROP, "enter")).not.toBe(claim);
    expect(idempotencyKey("another-drop", "claim")).not.toBe(claim);
  });

  it("issues a fresh key only after a terminal outcome clears the old one", () => {
    const first = idempotencyKey(DROP, "claim");
    clearIdempotencyKey(DROP, "claim");
    expect(idempotencyKey(DROP, "claim")).not.toBe(first);
  });

  it("tracks a pending action until it is cleared", () => {
    expect(isPending(DROP, "claim")).toBe(false);
    markPending(DROP, "claim");
    expect(isPending(DROP, "claim")).toBe(true);
    clearIdempotencyKey(DROP, "claim");
    expect(isPending(DROP, "claim")).toBe(false);
  });
});
