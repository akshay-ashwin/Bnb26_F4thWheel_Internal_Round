// Mock payloads shaped like docs/contract (README.md §2 and openapi.json).
import type { Allocation, Drop, Me, MeEntry } from "../api/types";

export const DROP_ID = "e63057b1-2916-4a6c-92e3-ac7a759e810d";
export const T0 = "2026-10-04T10:00:00.000Z";

export function drop(p: Partial<Drop> = {}): Drop {
  return {
    server_time: T0,
    id: DROP_ID,
    name: "Campus Fest",
    capacity: 500,
    mode: "fair",
    phase: "OPEN",
    reg_opens_at: "2026-10-04T09:59:00Z",
    reg_closes_at: "2026-10-04T10:10:00Z",
    claim_window_s: 120,
    seats_remaining: 500,
    seed_commit: "b4f6b1aa9fa8854bec450f9611425ea42c99d0cedc2a0804763b028983285345",
    seed: null,
    entry_set_hash: null,
    ...p,
  };
}

export function entry(p: Partial<MeEntry> = {}): MeEntry {
  return {
    entry_id: "0b6c5d34-8c40-4d6d-9d57-2a53f3c9a001",
    status: "REGISTERED",
    rank: null,
    waitlist_pos: null,
    offer_expires_at: null,
    step_up_required: false,
    admission_token: null,
    dev_otp: null,
    ...p,
  };
}

export const ALLOCATION: Allocation = {
  allocation_id: "7d1f0c2e-3b4a-4c5d-8e9f-0a1b2c3d4e5f",
  seat_no: 42,
  confirmed_at: "2026-10-04T10:15:31Z",
};

export function me(p: Partial<Me> = {}): Me {
  return {
    server_time: T0,
    phase: "OPEN",
    entry: null,
    allocation: null,
    poll_after_ms: 1000,
    ...p,
  };
}

export function offered(token = "tok-1"): MeEntry {
  return entry({
    status: "OFFERED",
    rank: 1,
    offer_expires_at: "2026-10-04T10:17:30Z",
    admission_token: token,
  });
}
