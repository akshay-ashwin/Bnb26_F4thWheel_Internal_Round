import type { Drop, Me, MeAllocation } from "../../api/types";

/** Every screen the ticket panel can show. Exactly one applies for any (drop, /me, session). */
export type JourneyView =
  | { kind: "loading" }
  | { kind: "scheduled"; opensAt: string | null }
  | { kind: "sign_in" }
  | { kind: "can_enter" }
  | { kind: "entered"; closesAt: string | null }
  | { kind: "fifo_grab" }
  | { kind: "sold_out" }
  | { kind: "drawing" }
  | { kind: "offered"; expiresAt: string | null; rank: number | null }
  | { kind: "step_up"; expiresAt: string | null; devOtp: string | null }
  | { kind: "allocated"; allocation: MeAllocation; rank: number | null }
  | { kind: "waitlisted"; position: number | null }
  | { kind: "not_selected"; rank: number | null }
  | { kind: "offer_expired" }
  | { kind: "disqualified" }
  | { kind: "missed" };

/**
 * Pure mapping from server state to a screen. The server is the truth: nothing here guesses an
 * outcome before `/me` reports it.
 */
export function journeyView(drop: Drop | null, me: Me | null, authed: boolean | null): JourneyView {
  if (!drop) return { kind: "loading" };
  const phase = me?.phase ?? drop.phase;
  const entry = me?.entry ?? null;
  const fifo = drop.mode === "fifo";

  if (me?.allocation) {
    return { kind: "allocated", allocation: me.allocation, rank: entry?.rank ?? null };
  }

  if (entry) {
    switch (entry.status) {
      case "OFFERED":
        return { kind: "offered", expiresAt: entry.offer_expires_at, rank: entry.rank };
      case "STEP_UP_REQUIRED":
        return { kind: "step_up", expiresAt: entry.offer_expires_at, devOtp: entry.dev_otp };
      case "WAITLISTED":
        return { kind: "waitlisted", position: entry.waitlist_pos };
      case "NOT_SELECTED":
        return { kind: "not_selected", rank: entry.rank };
      case "OFFER_EXPIRED":
        return { kind: "offer_expired" };
      case "DISQUALIFIED":
        return { kind: "disqualified" };
      case "ALLOCATED":
        // The allocation object arrives with the next poll.
        return { kind: "loading" };
      case "REGISTERED":
        if (phase === "DONE") return { kind: "not_selected", rank: entry.rank };
        if (fifo) return drop.seats_remaining > 0 ? { kind: "fifo_grab" } : { kind: "sold_out" };
        if (phase === "OPEN") return { kind: "entered", closesAt: drop.reg_closes_at };
        return { kind: "drawing" };
    }
  }

  if (phase === "SCHEDULED") return { kind: "scheduled", opensAt: drop.reg_opens_at };
  if (phase !== "OPEN") return { kind: "missed" };
  if (fifo && drop.seats_remaining <= 0) return { kind: "sold_out" };
  if (authed === null) return { kind: "loading" };
  if (!authed) return { kind: "sign_in" };
  return fifo ? { kind: "fifo_grab" } : { kind: "can_enter" };
}
