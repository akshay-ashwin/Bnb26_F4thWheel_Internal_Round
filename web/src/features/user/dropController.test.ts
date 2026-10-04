import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { isPending } from "../../api/idempotency";
import { ALLOCATION, DROP_ID, drop, entry, me, offered } from "../../test/fixtures";
import { apiError, mockFetch } from "../../test/mockFetch";
import { DropController } from "./dropController";
import { resolveUiState } from "./resolveUiState";

const ME = `GET /drops/${DROP_ID}/me`;
const DROP = `GET /drops/${DROP_ID}`;
const CLAIM = `POST /drops/${DROP_ID}/claim`;

let controller: DropController;

beforeEach(() => {
  sessionStorage.clear();
  controller = new DropController(DROP_ID);
});

afterEach(() => {
  controller.stop();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("claim: token refresh on TOKEN_INVALID", () => {
  it("refreshes /me once and retries with a fresh token and the SAME idempotency key", async () => {
    const f = mockFetch({
      [DROP]: () => ({ body: drop({ phase: "CLAIMING" }) }),
      [ME]: (_c, n) => ({
        body: me({ phase: "CLAIMING", entry: offered(n === 0 ? "old" : "fresh") }),
      }),
      [CLAIM]: (_c, n) => (n === 0 ? apiError(401, "TOKEN_INVALID") : { body: ALLOCATION }),
    });
    await controller.refresh();
    await controller.claim();

    const claims = f.of(CLAIM);
    expect(claims.map((c) => (c.body as { admission_token: string }).admission_token)).toEqual([
      "old",
      "fresh",
    ]);
    expect(claims[0]?.idempotencyKey).toBeTruthy();
    expect(claims[1]?.idempotencyKey).toBe(claims[0]?.idempotencyKey);
    expect(resolveUiState(controller.view.get())).toBe("ALLOCATED");
    expect(isPending(DROP_ID, "claim")).toBe(false);
  });

  it("refreshes only once: a second TOKEN_INVALID is surfaced, not looped", async () => {
    const f = mockFetch({
      [DROP]: () => ({ body: drop({ phase: "CLAIMING" }) }),
      [ME]: () => ({ body: me({ phase: "CLAIMING", entry: offered() }) }),
      [CLAIM]: () => apiError(401, "TOKEN_INVALID"),
    });
    await controller.refresh();
    await controller.claim();
    expect(f.of(CLAIM)).toHaveLength(2);
    expect(controller.view.get().actionError?.code).toBe("TOKEN_INVALID");
  });
});

describe("claim: lost response", () => {
  it("retries a dropped response with the same key and ends allocated with one key", async () => {
    vi.useFakeTimers();
    const f = mockFetch({
      [DROP]: () => ({ body: drop({ phase: "CLAIMING" }) }),
      [ME]: () => ({ body: me({ phase: "CLAIMING", entry: offered() }) }),
      // The server committed the first claim but the response never arrived.
      [CLAIM]: (_c, n) => (n === 0 ? "network-error" : { body: ALLOCATION }),
    });
    await controller.refresh();
    const done = controller.claim();
    await vi.runAllTimersAsync();
    await done;
    const keys = new Set(f.of(CLAIM).map((c) => c.idempotencyKey));
    expect(f.of(CLAIM)).toHaveLength(2);
    expect(keys.size).toBe(1);
    expect(controller.view.get().localAllocation?.seat_no).toBe(42);
  });

  it("a pending claim survives a refresh and resumes with the stored key", async () => {
    const f = mockFetch({
      [DROP]: () => ({ body: drop({ phase: "CLAIMING" }) }),
      [ME]: () => ({ body: me({ phase: "CLAIMING", entry: offered() }) }),
      [CLAIM]: () => ({ body: ALLOCATION }),
    });
    sessionStorage.setItem(`idem:${DROP_ID}:claim`, "11111111-1111-4111-8111-111111111111");
    sessionStorage.setItem(`pending:${DROP_ID}:claim`, "1");
    await controller.refresh(); // a fresh page load
    await vi.waitFor(() => expect(controller.view.get().localAllocation).not.toBeNull());
    expect(f.of(CLAIM)[0]?.idempotencyKey).toBe("11111111-1111-4111-8111-111111111111");
  });
});

describe("polling", () => {
  it("ignores a /me answer older than the one already applied", async () => {
    mockFetch({
      [DROP]: () => ({ body: drop({ phase: "CLAIMING" }) }),
      [ME]: (_c, n) =>
        n === 0
          ? {
              body: me({
                phase: "CLAIMING",
                allocation: ALLOCATION,
                server_time: "2026-10-04T10:00:05.000Z",
              }),
            }
          : {
              body: me({
                phase: "CLAIMING",
                entry: offered(),
                server_time: "2026-10-04T10:00:01.000Z",
              }),
            },
    });
    await controller.refresh();
    await controller.refresh();
    expect(controller.view.get().me?.allocation).not.toBeNull();
  });

  it("401 from /me shows VERIFY", async () => {
    mockFetch({
      [DROP]: () => ({ body: drop() }),
      [ME]: () => apiError(401, "UNAUTHENTICATED"),
    });
    await controller.refresh();
    expect(resolveUiState(controller.view.get())).toBe("VERIFY");
  });
});

describe("FIFO get a seat", () => {
  it("enters, takes the token from /me, claims", async () => {
    let entered = false;
    const f = mockFetch({
      [DROP]: () => ({ body: drop({ mode: "fifo", seats_remaining: 3 }) }),
      [ME]: () => ({
        body: me({ entry: entered ? entry({ admission_token: "fifo-tok" }) : null }),
      }),
      [`POST /drops/${DROP_ID}/entries`]: () => {
        entered = true;
        return { status: 201, body: { entry_id: "e1", status: "REGISTERED" } };
      },
      [CLAIM]: () => ({ body: ALLOCATION }),
    });
    await controller.refresh();
    expect(resolveUiState(controller.view.get())).toBe("FIFO_RACE");
    await controller.getSeat();
    expect((f.of(CLAIM)[0]?.body as { admission_token: string }).admission_token).toBe("fifo-tok");
    expect(resolveUiState(controller.view.get())).toBe("ALLOCATED");
  });

  it("SOLD_OUT ends the action without a retry", async () => {
    const f = mockFetch({
      [DROP]: (_c, n) => ({ body: drop({ mode: "fifo", seats_remaining: n === 0 ? 1 : 0 }) }),
      [ME]: () => ({ body: me({ entry: entry({ admission_token: "t" }) }) }),
      [CLAIM]: () => apiError(409, "SOLD_OUT"),
    });
    await controller.refresh();
    await controller.claim();
    await vi.waitFor(() => expect(resolveUiState(controller.view.get())).toBe("SOLD_OUT"));
    expect(f.of(CLAIM)).toHaveLength(1);
    expect(controller.view.get().actionError).toBeNull();
  });
});
