// The single pure function that decides which screen the user sees.
//
// Precedence: (no drop / no session) > allocation > entry status > phase.
// An allocation always wins, so a confirmed seat is never hidden by a stale phase or status.
// The phase comes from /me when we have it (fresher for this user), else from /drops/{id}.
import type { Allocation, Drop, Me } from "../../api/types";

export type UiState =
  | "LOADING"
  | "DROP_NOT_FOUND"
  | "VERIFY"
  | "BEFORE_WINDOW"
  | "CAN_ENTER"
  | "ENTERED_WAITING_CLOSE"
  | "WAITING_DRAW"
  | "FIFO_RACE"
  | "OFFERED"
  | "STEP_UP"
  | "ALLOCATED"
  | "WAITLISTED"
  | "OFFER_EXPIRED"
  | "NOT_SELECTED"
  | "SOLD_OUT"
  | "REGISTRATION_CLOSED";

export interface ResolverInput {
  drop: Drop | null;
  dropMissing: boolean;
  /** null = not known yet; false = /me answered 401. */
  authenticated: boolean | null;
  me: Me | null;
  /** A claim's 200 body, used until /me reflects it (avoids flicker back to OFFERED). */
  localAllocation: Allocation | null;
}

export function resolveUiState(input: ResolverInput): UiState {
  const { drop, me } = input;
  if (input.dropMissing) return "DROP_NOT_FOUND";
  if (!drop) return "LOADING";
  if (input.authenticated === false) return "VERIFY";
  if (!me) return "LOADING";

  // 1. Allocation.
  if (me.allocation ?? input.localAllocation) return "ALLOCATED";

  const phase = me.phase;
  const fifo = drop.mode === "fifo";
  // FIFO only: a FIFO drop with no seats left is sold out for anyone without a seat.
  const fifoSoldOut = fifo && (drop.seats_remaining <= 0 || phase === "DONE");

  // 2. Entry status.
  const entry = me.entry;
  if (entry) {
    switch (entry.status) {
      case "ALLOCATED":
        return "ALLOCATED";
      case "OFFERED":
        return "OFFERED";
      case "STEP_UP_REQUIRED":
        return "STEP_UP";
      case "WAITLISTED":
        return "WAITLISTED";
      case "OFFER_EXPIRED":
        return "OFFER_EXPIRED";
      case "NOT_SELECTED":
      case "DISQUALIFIED":
        return "NOT_SELECTED";
      case "REGISTERED":
        if (fifoSoldOut) return "SOLD_OUT";
        switch (phase) {
          case "SCHEDULED":
            return "BEFORE_WINDOW";
          case "OPEN":
            return fifo ? "FIFO_RACE" : "ENTERED_WAITING_CLOSE";
          case "CLOSED":
          case "DRAWN":
          case "CLAIMING":
            return fifo ? "REGISTRATION_CLOSED" : "WAITING_DRAW";
          case "DONE":
            return "NOT_SELECTED";
        }
    }
  }

  // 3. Phase (no entry).
  if (fifoSoldOut) return "SOLD_OUT";
  switch (phase) {
    case "SCHEDULED":
      return "BEFORE_WINDOW";
    case "OPEN":
      return fifo ? "FIFO_RACE" : "CAN_ENTER";
    default:
      return "REGISTRATION_CLOSED";
  }
}
