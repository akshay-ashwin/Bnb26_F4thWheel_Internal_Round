import type { EntryStatus, MeAllocation, Mode, Phase } from "../api/types";
import { bytesToHex, seedCommit } from "../lib/draw";
import { randomId, readItem, removeItem, writeItem } from "../lib/storage";

/** Demo-mode only: lets a presenter pick what the draw does to the signed-in user. */
export type ForcedOutcome = "natural" | "win" | "step_up" | "waitlist" | "not_selected";

export interface Faults {
  /** Every request fails like a dropped connection. */
  offline: boolean;
  /** This many upcoming public requests answer 429 RATE_LIMITED. */
  rateLimit: number;
  /** The next claim answers 401 TOKEN_INVALID once. */
  tokenInvalidOnce: boolean;
  /** The next claim is committed, but the response is lost on the way back. */
  lostResponseOnce: boolean;
}

export interface MockEntry {
  entry_id: string;
  status: EntryStatus;
  rank: number | null;
  offer_expires_at: string | null;
  risk: number;
  entered_at: string;
  step_up_otp: string | null;
}

export interface MockDrop {
  id: string;
  name: string;
  capacity: number;
  mode: Mode;
  phase: Phase;
  run_no: number;
  window_s: number;
  claim_window_s: number;
  reg_opens_at: string | null;
  reg_closes_at: string | null;
  /** Demo autopilot: a scheduled drop opens by itself at this time (epoch ms). */
  auto_open_at: number | null;
  seed: string;
  seed_commit: string;
  entry_set_hash: string | null;
  /** How many simulated entrants a full window attracts. */
  crowd_target: number;
  /** Rank 1 first; set by the draw. */
  ranked: string[] | null;
  closed_at: number | null;
  drawn_at: number | null;
  /** Waitlist places promoted when the first claim window ended; null before that. */
  promoted: number | null;
  /** Entries of people signed in to this browser, by user_public_id. */
  entries: Record<string, MockEntry>;
  allocations: Record<string, MeAllocation>;
  idem: Record<string, string>;
}

export interface MockState {
  version: 2;
  drops: MockDrop[];
  /** phone -> user_public_id */
  users: Record<string, string>;
  /** session token -> user_public_id */
  sessions: Record<string, string>;
  /** Stands in for the httpOnly cookie: shared by every tab. */
  cookie: string | null;
  otps: Record<string, { phone: string; otp: string; device: string; expires: number }>;
  abuse: { layers: Record<string, boolean>; thresholds: Record<string, number> };
  faults: Faults;
  outcome: ForcedOutcome;
}

const STORAGE_KEY = "fd:mock:v2";
const BASE32 = "abcdefghijklmnopqrstuvwxyz234567";

/** 16 random bytes, base32, lowercase, no padding: 26 characters (docs/GLOSSARY.md). */
export function randomPublicId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  let bits = 0;
  let value = 0;
  let out = "";
  for (const b of bytes) {
    value = (value << 8) | b;
    bits += 8;
    while (bits >= 5) {
      out += BASE32[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) out += BASE32[(value << (5 - bits)) & 31];
  return out;
}

export function randomSeed(): string {
  return bytesToHex(crypto.getRandomValues(new Uint8Array(32)));
}

export async function newDrop(args: {
  id?: string;
  name: string;
  mode: Mode;
  capacity?: number;
  window_s: number;
  claim_window_s?: number;
  crowd_target?: number;
  auto_open_at?: number | null;
}): Promise<MockDrop> {
  const seed = randomSeed();
  const capacity = args.capacity ?? 500;
  return {
    id: args.id ?? randomId(),
    name: args.name,
    capacity,
    mode: args.mode,
    phase: "SCHEDULED",
    run_no: 1,
    window_s: args.window_s,
    claim_window_s: args.claim_window_s ?? 120,
    reg_opens_at: null,
    reg_closes_at: null,
    auto_open_at: args.auto_open_at ?? null,
    seed,
    seed_commit: await seedCommit(seed),
    entry_set_hash: null,
    crowd_target: args.crowd_target ?? Math.round(capacity * 3.7),
    ranked: null,
    closed_at: null,
    drawn_at: null,
    promoted: null,
    entries: {},
    allocations: {},
    idem: {},
  };
}

/** Stable ids so demo links keep working across "reset demo data". */
export const DEMO_DROP_IDS = {
  live: "6f1c2a9e-4b1d-4c7a-9a55-0d2f6b1e7a01",
  soon: "6f1c2a9e-4b1d-4c7a-9a55-0d2f6b1e7a02",
  fifo: "6f1c2a9e-4b1d-4c7a-9a55-0d2f6b1e7a03",
  past: "6f1c2a9e-4b1d-4c7a-9a55-0d2f6b1e7a04",
  later: "6f1c2a9e-4b1d-4c7a-9a55-0d2f6b1e7a05",
} as const;

export async function seedState(now: number): Promise<MockState> {
  const live = await newDrop({
    id: DEMO_DROP_IDS.live,
    name: "Arijit Singh: Live in Pune",
    mode: "fair",
    window_s: 300,
    claim_window_s: 90,
    crowd_target: 1840,
  });
  live.phase = "OPEN";
  live.reg_opens_at = new Date(now - 45_000).toISOString();
  live.reg_closes_at = new Date(now - 45_000 + live.window_s * 1000).toISOString();

  const soon = await newDrop({
    id: DEMO_DROP_IDS.soon,
    name: "Sunburn Arena ft. Martin Garrix",
    mode: "fair",
    window_s: 180,
    claim_window_s: 60,
    crowd_target: 2360,
    auto_open_at: now + 120_000,
  });
  const fifo = await newDrop({
    id: DEMO_DROP_IDS.fifo,
    name: "Zakir Khan: Tathastu",
    mode: "fifo",
    window_s: 120,
    crowd_target: 2100,
    auto_open_at: now + 75_000,
  });
  // A finished drop so the "verify the draw" page has a real proof to check on first visit.
  const past = await newDrop({
    id: DEMO_DROP_IDS.past,
    name: "Prateek Kuhad: Silhouettes Tour",
    mode: "fair",
    window_s: 600,
    crowd_target: 1400,
  });
  past.phase = "OPEN";
  past.reg_opens_at = new Date(now - 3 * 3600_000).toISOString();
  past.reg_closes_at = new Date(now - 3 * 3600_000 + 600_000).toISOString();

  const later = await newDrop({
    id: DEMO_DROP_IDS.later,
    name: "Indie Under the Stars",
    mode: "fair",
    window_s: 900,
    auto_open_at: now + 2 * 86_400_000,
  });

  return {
    version: 2,
    drops: [live, soon, fifo, past, later],
    users: {},
    sessions: {},
    cookie: null,
    otps: {},
    abuse: {
      layers: Object.fromEntries(
        ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"].map((l) => [l, true]),
      ),
      thresholds: { step_up_risk: 60, per_ip_rps: 20, per_session_rps: 5 },
    },
    faults: { offline: false, rateLimit: 0, tokenInvalidOnce: false, lostResponseOnce: false },
    outcome: "natural",
  };
}

export function readState(): MockState | null {
  const raw = readItem("local", STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as MockState;
    return parsed.version === 2 ? parsed : null;
  } catch {
    return null;
  }
}

export function writeState(state: MockState): void {
  writeItem("local", STORAGE_KEY, JSON.stringify(state));
}

export function clearState(): void {
  removeItem("local", STORAGE_KEY);
}
