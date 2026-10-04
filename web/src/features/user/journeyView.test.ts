import { describe, expect, it } from "vitest";

import type { Drop, EntryStatus, Me, Phase } from "../../api/types";
import { journeyView } from "./journeyView";

const drop = (over: Partial<Drop> = {}): Drop => ({
  id: "6f1c2a9e-4b1d-4c7a-9a55-0d2f6b1e7a01",
  name: "Test",
  capacity: 500,
  mode: "fair",
  phase: "OPEN",
  reg_opens_at: "2026-10-04T10:00:00Z",
  reg_closes_at: "2026-10-04T10:05:00Z",
  claim_window_s: 120,
  seats_remaining: 500,
  seed_commit: "c",
  seed: null,
  entry_set_hash: null,
  server_time: "2026-10-04T10:01:00.000Z",
  ...over,
});

const me = (phase: Phase, status: EntryStatus | null, over: Partial<Me> = {}): Me => ({
  phase,
  entry: status && {
    entry_id: "e",
    status,
    rank: 187,
    waitlist_pos: status === "WAITLISTED" ? 37 : null,
    offer_expires_at: "2026-10-04T10:17:30Z",
    step_up_required: status === "STEP_UP_REQUIRED",
    admission_token: status === "OFFERED" ? "t" : null,
    dev_otp: null,
  },
  allocation: null,
  poll_after_ms: 1000,
  server_time: "2026-10-04T10:01:00.000Z",
  ...over,
});

describe("journeyView", () => {
  it("is loading until the drop arrives", () => {
    expect(journeyView(null, null, null).kind).toBe("loading");
  });

  it("asks a signed-out visitor to verify during the window", () => {
    expect(journeyView(drop(), null, false).kind).toBe("sign_in");
  });

  it("offers entry to a signed-in visitor without an entry", () => {
    expect(journeyView(drop(), me("OPEN", null), true).kind).toBe("can_enter");
  });

  it("shows the countdown before the window opens", () => {
    expect(journeyView(drop({ phase: "SCHEDULED" }), null, false).kind).toBe("scheduled");
  });

  it.each<[Phase, EntryStatus, string]>([
    ["OPEN", "REGISTERED", "entered"],
    ["CLOSED", "REGISTERED", "drawing"],
    ["CLAIMING", "OFFERED", "offered"],
    ["CLAIMING", "STEP_UP_REQUIRED", "step_up"],
    ["CLAIMING", "WAITLISTED", "waitlisted"],
    ["DONE", "NOT_SELECTED", "not_selected"],
    ["CLAIMING", "OFFER_EXPIRED", "offer_expired"],
    ["CLAIMING", "DISQUALIFIED", "disqualified"],
  ])("maps %s / %s to the %s screen", (phase, status, kind) => {
    expect(journeyView(drop({ phase }), me(phase, status), true).kind).toBe(kind);
  });

  it("shows the ticket only when the server reports an allocation", () => {
    const allocation = { allocation_id: "a", seat_no: 42, confirmed_at: "2026-10-04T10:16:00Z" };
    expect(journeyView(drop(), me("CLAIMING", "ALLOCATED", { allocation }), true)).toMatchObject({
      kind: "allocated",
      allocation: { seat_no: 42 },
    });
    // ALLOCATED without the allocation object yet: wait, never invent a seat.
    expect(journeyView(drop(), me("CLAIMING", "ALLOCATED"), true).kind).toBe("loading");
  });

  it("carries the waitlist position", () => {
    expect(journeyView(drop(), me("CLAIMING", "WAITLISTED"), true)).toEqual({
      kind: "waitlisted",
      position: 37,
    });
  });

  it("uses one grab step for first-come drops and shows sold out at zero seats", () => {
    const fifo = drop({ mode: "fifo" });
    expect(journeyView(fifo, me("OPEN", null), true).kind).toBe("fifo_grab");
    expect(journeyView(fifo, me("OPEN", "REGISTERED"), true).kind).toBe("fifo_grab");
    expect(journeyView({ ...fifo, seats_remaining: 0 }, me("OPEN", "REGISTERED"), true).kind).toBe(
      "sold_out",
    );
  });

  it("tells a visitor without an entry that the window has closed", () => {
    expect(journeyView(drop({ phase: "CLAIMING" }), me("CLAIMING", null), true).kind).toBe(
      "missed",
    );
  });
});
