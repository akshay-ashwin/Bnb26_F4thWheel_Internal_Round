// Hand-written from docs/contract/openapi.json (only what the user app reads). Generated types
// and the drift check are Plan 15 items that were not needed for Plan 16.

export type Phase = "SCHEDULED" | "OPEN" | "CLOSED" | "DRAWN" | "CLAIMING" | "DONE";
export type Mode = "fair" | "fifo";
export type EntryStatus =
  | "REGISTERED"
  | "OFFERED"
  | "STEP_UP_REQUIRED"
  | "WAITLISTED"
  | "NOT_SELECTED"
  | "OFFER_EXPIRED"
  | "ALLOCATED"
  | "DISQUALIFIED";

export interface Timed {
  server_time: string;
}

export interface Drop extends Timed {
  id: string;
  name: string;
  capacity: number;
  mode: Mode;
  phase: Phase;
  reg_opens_at: string | null;
  reg_closes_at: string | null;
  claim_window_s: number;
  seats_remaining: number;
  seed_commit: string;
  seed: string | null;
  entry_set_hash: string | null;
}

export interface MeEntry {
  entry_id: string;
  status: EntryStatus;
  rank: number | null;
  waitlist_pos: number | null;
  offer_expires_at: string | null;
  step_up_required: boolean;
  admission_token: string | null;
  dev_otp: string | null;
}

export interface Allocation {
  allocation_id: string;
  seat_no: number;
  confirmed_at: string;
}

export interface Me extends Timed {
  phase: Phase;
  entry: MeEntry | null;
  allocation: Allocation | null;
  poll_after_ms: number;
}

export interface OtpRequestOut extends Timed {
  request_id: string;
  expires_in_s: number;
  dev_otp?: string | null;
}

export interface OtpVerifyOut extends Timed {
  session_token: string;
  user_public_id: string;
}

export interface EntryOut extends Timed {
  entry_id: string;
  status: "REGISTERED";
}

export type ClaimOut = Allocation & Timed;

export interface StepUpOut extends Timed {
  status: "OFFERED";
}
