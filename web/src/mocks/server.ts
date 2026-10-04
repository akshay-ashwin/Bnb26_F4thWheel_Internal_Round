/**
 * Demo mode: an in-browser stand-in for the Fair Drop API that follows docs/contract. It keeps its
 * state in localStorage (so tabs and refreshes agree, like a real server), runs the real draw
 * (src/lib/draw.ts) and simulates a crowd so every screen can be reached without a backend.
 * It is a UI aid only: nothing here is evidence about the real system.
 */
import type {
  AbuseConfig,
  Drop,
  DrawProof,
  ErrorCode,
  Integrity,
  Me,
  Metrics,
  PhaseAction,
} from "../api/types";
import { byteOrder, entrySetHash, rankKeys, seedCommit } from "../lib/draw";
import { randomId } from "../lib/storage";
import {
  clearState,
  newDrop,
  randomPublicId,
  randomSeed,
  readState,
  seedState,
  writeState,
  type Faults,
  type ForcedOutcome,
  type MockDrop,
  type MockEntry,
  type MockState,
} from "./state";

const DRAW_GRACE_MS = 3000;
const TOKEN_TTL_MS = 60_000;
const STEP_UP_RISK = 60;
/** Share of simulated winners who claim in time; the rest free seats for the waitlist. */
const CLAIM_RATE = 0.9;

let latencyMs: [number, number] = [70, 220];
let queue: Promise<unknown> = Promise.resolve();

class MockHttpError extends Error {
  constructor(
    readonly status: number,
    readonly code: ErrorCode,
    message: string,
    readonly retryAfterMs: number | null = null,
  ) {
    super(message);
  }
}

const iso = (ms: number) => new Date(ms).toISOString();
const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const ease = (x: number) => 1 - (1 - clamp01(x)) ** 2;

/** Deterministic 0..1 noise so charts are stable between polls. */
function noise(t: number, salt: number): number {
  let h = (t * 374761393 + salt * 668265263) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
}

async function load(now: number): Promise<MockState> {
  const existing = readState();
  if (existing) return existing;
  const fresh = await seedState(now);
  for (const drop of fresh.drops) await advance(fresh, drop, now);
  writeState(fresh);
  return fresh;
}

/** Runs `fn` against the latest state, one request at a time, then saves. */
function withState<T>(fn: (state: MockState, now: number) => Promise<T> | T): Promise<T> {
  const run = queue.then(async () => {
    const now = Date.now();
    const state = await load(now);
    try {
      return await fn(state, now);
    } finally {
      writeState(state);
    }
  });
  queue = run.catch(() => undefined);
  return run;
}

// ---------------------------------------------------------------------------------------------
// Drop life cycle
// ---------------------------------------------------------------------------------------------

function crowdCount(d: MockDrop, now: number): number {
  if (!d.reg_opens_at) return 0;
  const opens = Date.parse(d.reg_opens_at);
  const end = Math.min(now, d.closed_at ?? now);
  const t = Math.max(0, (end - opens) / 1000);
  return Math.floor(
    d.crowd_target * (1 - Math.exp(-t / Math.min(20, Math.max(4, d.window_s * 0.25)))),
  );
}

function entriesCount(d: MockDrop, now: number): number {
  if (d.ranked) return d.ranked.length;
  return crowdCount(d, now) + Object.keys(d.entries).length;
}

function realInRanks(d: MockDrop, from: number, to: number): number {
  return Object.values(d.entries).filter((e) => e.rank !== null && e.rank >= from && e.rank <= to)
    .length;
}

/** Seats taken by simulated people right now. */
function syntheticSold(d: MockDrop, now: number): number {
  const real = Object.keys(d.allocations).length;
  if (d.mode === "fifo") {
    if (!d.reg_opens_at) return 0;
    // Scripts take almost everything within a few seconds of the open: the unfair "before".
    const t = (Math.min(now, d.closed_at ?? now) - Date.parse(d.reg_opens_at)) / 6000;
    return Math.min(d.capacity - real, Math.floor(d.capacity * clamp01(t) ** 1.4));
  }
  if (d.drawn_at === null || !d.ranked) return 0;
  const winners = Math.min(d.capacity, d.ranked.length) - realInRanks(d, 1, d.capacity);
  const firstRound = Math.floor(winners * CLAIM_RATE);
  const windowMs = d.claim_window_s * 1000;
  if (d.promoted === null) {
    return Math.floor(firstRound * ease((now - d.drawn_at) / (0.7 * windowMs)));
  }
  const promotedSynthetic = d.promoted - realInRanks(d, d.capacity + 1, d.capacity + d.promoted);
  const second = Math.floor(
    promotedSynthetic * ease((now - d.drawn_at - windowMs) / (0.5 * windowMs)),
  );
  return Math.min(d.capacity - real, firstRound + second);
}

function sold(d: MockDrop, now: number): number {
  return syntheticSold(d, now) + Object.keys(d.allocations).length;
}

function openDrop(d: MockDrop, at: number): void {
  d.phase = "OPEN";
  d.auto_open_at = null;
  d.reg_opens_at = iso(at);
  d.reg_closes_at = iso(at + d.window_s * 1000);
}

function closeDrop(d: MockDrop, at: number): void {
  d.closed_at = at;
  d.reg_closes_at = iso(at);
  if (d.mode === "fifo") finishDrop(d);
  else d.phase = "CLOSED";
}

function finishDrop(d: MockDrop): void {
  d.phase = "DONE";
  for (const entry of Object.values(d.entries)) {
    if (entry.status === "REGISTERED" || entry.status === "WAITLISTED") {
      entry.status = "NOT_SELECTED";
    } else if (entry.status === "OFFERED" || entry.status === "STEP_UP_REQUIRED") {
      entry.status = "OFFER_EXPIRED";
    }
  }
}

function offer(entry: MockEntry, expiresAt: number): void {
  entry.offer_expires_at = iso(expiresAt);
  if (entry.risk >= STEP_UP_RISK) {
    entry.status = "STEP_UP_REQUIRED";
    entry.step_up_otp = String(100000 + Math.floor(Math.random() * 900000));
  } else {
    entry.status = "OFFERED";
  }
}

/**
 * The real draw: freeze the entry set, publish its hash, rank by HMAC(seed, drop|id).
 * In demo mode the presenter can steer the signed-in user's result by changing how many simulated
 * entrants there are; the published proof still verifies because the crowd is fixed before hashing.
 */
async function drawDrop(state: MockState, d: MockDrop, at: number): Promise<void> {
  const realIds = Object.keys(d.entries);
  const target = Math.max(0, crowdCount(d, at));
  let crowd = Array.from({ length: target }, randomPublicId);
  let keys = await rankKeys(d.seed, d.id, [...crowd, ...realIds]);

  const me = state.cookie ? state.sessions[state.cookie] : undefined;
  const myKey = me ? keys.get(me) : undefined;
  if (me && myKey && d.entries[me] && state.outcome !== "natural") {
    const ahead = crowd.filter((id) => (keys.get(id) ?? "") < myKey);
    const behind = crowd.filter((id) => (keys.get(id) ?? "") >= myKey);
    const wantAhead =
      state.outcome === "waitlist"
        ? d.capacity + 11
        : state.outcome === "not_selected"
          ? d.capacity + Math.ceil(d.capacity * 0.6)
          : Math.min(ahead.length, Math.floor(d.capacity * 0.37));
    for (let batch = 0; ahead.length < wantAhead && batch < 60; batch += 1) {
      const extra = Array.from({ length: 400 }, randomPublicId);
      const extraKeys = await rankKeys(d.seed, d.id, extra);
      for (const id of extra) {
        if ((extraKeys.get(id) ?? "") < myKey) ahead.push(id);
        else behind.push(id);
      }
    }
    crowd = [...ahead.slice(0, wantAhead), ...behind];
    keys = await rankKeys(d.seed, d.id, [...crowd, ...realIds]);
    const mine = d.entries[me];
    if (mine) mine.risk = state.outcome === "step_up" ? 72 : Math.min(mine.risk, 20);
  }

  const eligible = [...crowd, ...realIds];
  d.entry_set_hash = await entrySetHash(eligible);
  d.ranked = eligible.sort(
    (a, b) => byteOrder(keys.get(a) ?? "", keys.get(b) ?? "") || byteOrder(a, b),
  );
  d.drawn_at = at;
  d.promoted = null;
  d.phase = "CLAIMING";
  const expires = at + d.claim_window_s * 1000;
  d.ranked.forEach((id, index) => {
    const entry = d.entries[id];
    if (!entry) return;
    entry.rank = index + 1;
    if (entry.rank <= d.capacity) offer(entry, expires);
    else entry.status = "WAITLISTED";
  });
}

/** Moves a drop forward to `now`: autopilot open/close/draw, offer expiry, waitlist promotion. */
async function advance(state: MockState, d: MockDrop, now: number): Promise<void> {
  if (d.phase === "SCHEDULED" && d.auto_open_at !== null && now >= d.auto_open_at) {
    openDrop(d, d.auto_open_at);
  }
  if (d.phase === "OPEN" && d.reg_closes_at && now >= Date.parse(d.reg_closes_at)) {
    closeDrop(d, Date.parse(d.reg_closes_at));
  }
  if (d.phase === "CLOSED" && d.closed_at !== null && now >= d.closed_at + DRAW_GRACE_MS) {
    await drawDrop(state, d, d.closed_at + DRAW_GRACE_MS);
  }
  if (d.phase !== "CLAIMING" || d.drawn_at === null || !d.ranked) return;

  const windowMs = d.claim_window_s * 1000;
  const firstEnd = d.drawn_at + windowMs;
  for (const entry of Object.values(d.entries)) {
    const open = entry.status === "OFFERED" || entry.status === "STEP_UP_REQUIRED";
    if (open && entry.offer_expires_at && now >= Date.parse(entry.offer_expires_at)) {
      entry.status = "OFFER_EXPIRED";
    }
  }
  if (d.promoted === null && now >= firstEnd) {
    const free = d.capacity - sold(d, firstEnd);
    d.promoted = Math.max(0, Math.min(free, d.ranked.length - d.capacity));
    for (const entry of Object.values(d.entries)) {
      const promoted =
        entry.status === "WAITLISTED" &&
        entry.rank !== null &&
        entry.rank <= d.capacity + d.promoted;
      if (promoted) offer(entry, firstEnd + windowMs);
    }
  }
  const openOffers = Object.values(d.entries).some(
    (e) => e.status === "OFFERED" || e.status === "STEP_UP_REQUIRED",
  );
  const secondRoundOver = d.promoted !== null && now >= firstEnd + 0.5 * windowMs && !openOffers;
  if (now >= firstEnd + windowMs || secondRoundOver) finishDrop(d);
}

// ---------------------------------------------------------------------------------------------
// Response shapes
// ---------------------------------------------------------------------------------------------

function dropOut(d: MockDrop, now: number): Omit<Drop, "server_time"> {
  const revealed = d.ranked !== null;
  return {
    id: d.id,
    name: d.name,
    capacity: d.capacity,
    mode: d.mode,
    phase: d.phase,
    reg_opens_at: d.reg_opens_at ?? (d.auto_open_at !== null ? iso(d.auto_open_at) : null),
    reg_closes_at: d.reg_closes_at,
    claim_window_s: d.claim_window_s,
    seats_remaining: Math.max(0, d.capacity - sold(d, now)),
    seed_commit: d.seed_commit,
    seed: revealed ? d.seed : null,
    entry_set_hash: d.entry_set_hash,
  };
}

function pollAfter(d: MockDrop, entry: MockEntry | undefined): number {
  if (entry) {
    if (entry.status === "OFFERED" || entry.status === "STEP_UP_REQUIRED") return 1000;
    if (entry.status === "WAITLISTED") return 2000;
    if (entry.status !== "REGISTERED") return 15_000;
  }
  switch (d.phase) {
    case "SCHEDULED":
      return 5000;
    case "OPEN":
      return d.mode === "fifo" ? 1000 : entry ? 4000 : 3000;
    case "CLOSED":
    case "DRAWN":
      return 1500;
    default:
      return 5000;
  }
}

function meOut(d: MockDrop, user: string, now: number): Omit<Me, "server_time"> {
  const entry = d.entries[user];
  const canClaim =
    entry?.status === "OFFERED" ||
    (d.mode === "fifo" && d.phase === "OPEN" && entry?.status === "REGISTERED");
  return {
    phase: d.phase,
    entry: entry
      ? {
          entry_id: entry.entry_id,
          status: entry.status,
          rank: entry.rank,
          waitlist_pos:
            entry.status === "WAITLISTED" && entry.rank !== null
              ? entry.rank - d.capacity - (d.promoted ?? 0)
              : null,
          offer_expires_at: entry.offer_expires_at,
          step_up_required: entry.status === "STEP_UP_REQUIRED",
          admission_token: canClaim ? `mock.${entry.entry_id}.${now + TOKEN_TTL_MS}` : null,
          dev_otp: entry.status === "STEP_UP_REQUIRED" ? entry.step_up_otp : null,
        }
      : null,
    allocation: d.allocations[user] ?? null,
    poll_after_ms: pollAfter(d, entry),
  };
}

function integrityOut(d: MockDrop, now: number): Omit<Integrity, "server_time"> {
  const taken = sold(d, now);
  return {
    seats_total: d.capacity,
    sold: taken,
    free: d.capacity - taken,
    oversold: 0,
    duplicate_entries_with_seats: 0,
    invariant_ok: true,
    extra: {
      capacity: d.capacity,
      allocations_count: taken,
      sold_without_allocation: 0,
      allocation_without_sold_seat: 0,
      entries_allocated_count: taken,
      entries_allocated_mismatch: 0,
      sold_seat_entry_not_allocated: 0,
      free_seat_with_sold_at: 0,
    },
  };
}

function metricsOut(
  d: MockDrop,
  now: number,
  windowS: number,
  sessions: number,
): Omit<Metrics, "server_time"> {
  const nowS = Math.floor(now / 1000);
  const opens = d.reg_opens_at ? Date.parse(d.reg_opens_at) / 1000 : Infinity;
  const closes = d.closed_at !== null ? d.closed_at / 1000 : Infinity;
  const drawn = d.drawn_at !== null ? d.drawn_at / 1000 : Infinity;
  const fifo = d.mode === "fifo";

  const rps: Metrics["rps_series"] = [];
  const accepted: number[] = [];
  const rateLimited: number[] = [];
  const tokenRejected: number[] = [];
  const duplicate: number[] = [];
  const layers: Record<string, number[]> = { L1: [], L2: [], L3: [] };
  for (let t = nowS - windowS + 1; t <= nowS; t += 1) {
    const wobble = 0.85 + 0.3 * noise(t, 1);
    let humans = 12;
    let bots = 0;
    if (t >= opens && t < closes) {
      const rush = Math.exp(-(t - opens) / 8);
      humans = fifo ? 300 + 2400 * rush : 420 + 500 * rush;
      bots = fifo ? 900 + 7600 * rush : 5200;
    } else if (t >= drawn && d.phase === "CLAIMING") {
      humans = 180 + 520 * Math.exp(-(t - drawn) / 12);
      bots = 240;
    }
    const total = Math.round((humans + bots) * wobble);
    const limited = fifo ? Math.round(bots * 0.04 * wobble) : Math.round(bots * 0.965 * wobble);
    const dup = Math.round(total * 0.03 * noise(t, 2));
    const rejected = t >= drawn ? Math.round(bots * 0.08 * noise(t, 3)) : 0;
    rps.push({ t, total });
    rateLimited.push(limited);
    duplicate.push(dup);
    tokenRejected.push(rejected);
    accepted.push(Math.max(0, total - limited - dup - rejected));
    layers.L1?.push(Math.round(limited * 0.15));
    layers.L2?.push(Math.round(limited * 0.6));
    layers.L3?.push(Math.round(limited * 0.25));
  }
  const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
  const entries = entriesCount(d, now);
  const taken = sold(d, now);
  const busyFifo = fifo && d.phase === "OPEN";
  const issued = d.ranked ? Math.floor(Math.min(d.capacity, d.ranked.length) * 0.08) : 0;
  return {
    rps_series: rps,
    outcomes_series: {
      accepted,
      rate_limited: rateLimited,
      token_rejected: tokenRejected,
      duplicate,
    },
    latency: busyFifo ? { p50: 140, p95: 960, p99: 2400 } : { p50: 14, p95: 72, p99: 190 },
    error_rate: busyFifo ? 0.041 : 0.001,
    active_sessions: Math.round(entries * 1.15) + sessions,
    entries,
    offers: d.ranked ? Math.min(d.capacity, d.ranked.length) + (d.promoted ?? 0) : 0,
    allocated: taken,
    remaining: d.capacity - taken,
    flagged_entries: Math.floor(entries * 0.19),
    step_ups: {
      issued,
      passed: Math.floor(issued * ease((now - (d.drawn_at ?? now)) / 60_000) * 0.3),
      failed: Math.floor(issued * ease((now - (d.drawn_at ?? now)) / 60_000) * 0.6),
    },
    phase: d.phase,
    mode: d.mode,
    run_no: d.run_no,
    capacity: d.capacity,
    oversold: 0,
    invariant_ok: true,
    claims_ok: taken,
    claims_sold_out: fifo ? Math.max(0, entries - taken) : 0,
    blocked_requests: sum(rateLimited) + sum(tokenRejected),
    throttled_requests: sum(rateLimited),
    duplicate_requests: sum(duplicate),
    rate_limited_by_layer: layers,
    window_s: windowS,
    metrics_dropped: 0,
  };
}

/** What the simulator would report: who is really a bot. Display only, like the real endpoint. */
function simLatest(d: MockDrop, now: number): Record<string, unknown> | null {
  if (!d.reg_opens_at) return null;
  const entries = entriesCount(d, now);
  const botIds = Math.round(entries * 0.2);
  const humanIds = entries - botIds;
  const taken = sold(d, now);
  const botSeatShare = d.mode === "fifo" ? 0.88 : 0.2 + (noise(d.run_no, 7) - 0.5) * 0.04;
  const botSeats = Math.round(taken * botSeatShare);
  const humanSeats = taken - botSeats;
  const ratio =
    humanSeats > 0 && botIds > 0 && humanIds > 0
      ? botSeats / botIds / (humanSeats / humanIds)
      : null;
  return {
    run_id: `demo_${d.id.slice(0, 8)}_run${d.run_no}`,
    attack_phase: d.phase === "OPEN" ? "flood" : d.phase === "CLAIMING" ? "claim" : "idle",
    clients_by_label: { human: humanIds, bot: botIds * 5 },
    identities_by_label: { human: humanIds, bot: botIds },
    requests_by_label: { human: humanIds * 6, bot: botIds * 3200 },
    fairness_live: {
      bot_identity_share: Number((botIds / Math.max(1, entries)).toFixed(3)),
      bot_seat_share: taken > 0 ? Number((botSeats / taken).toFixed(3)) : null,
      advantage_ratio: ratio === null ? null : Number(ratio.toFixed(2)),
    },
  };
}

function exportNdjson(d: MockDrop): string {
  const rows: string[] = [];
  const ids = d.ranked ?? Object.keys(d.entries);
  for (const [index, id] of ids.slice(0, 200).entries()) {
    const real = d.entries[id];
    const allocation = d.allocations[id];
    const rank = d.ranked ? index + 1 : null;
    rows.push(
      JSON.stringify({
        user_public_id: id,
        entry_id: real?.entry_id ?? randomId(),
        entered_at: real?.entered_at ?? d.reg_opens_at,
        risk_score: real?.risk ?? Math.floor(noise(index, 11) * 70),
        risk_flags: noise(index, 12) > 0.8 ? ["device_shared"] : [],
        rank,
        run_no: d.run_no,
        status: real?.status ?? (rank !== null && rank <= d.capacity ? "ALLOCATED" : "WAITLISTED"),
        ...(allocation ? { seat_no: allocation.seat_no } : {}),
      }),
    );
  }
  return rows.join("\n");
}

// ---------------------------------------------------------------------------------------------
// Request handling
// ---------------------------------------------------------------------------------------------

function normalisePhone(input: string): string | null {
  let digits = input.replace(/[\s-]/g, "");
  if (digits.startsWith("+91")) digits = digits.slice(3);
  else if (digits.length === 12 && digits.startsWith("91")) digits = digits.slice(2);
  else if (digits.length === 11 && digits.startsWith("0")) digits = digits.slice(1);
  return /^[6-9]\d{9}$/.test(digits) ? `+91${digits}` : null;
}

function requireUser(state: MockState): string {
  const user = state.cookie ? state.sessions[state.cookie] : undefined;
  if (!user) throw new MockHttpError(401, "UNAUTHENTICATED", "Sign in to continue");
  return user;
}

function findDrop(state: MockState, id: string): MockDrop {
  const drop = state.drops.find((d) => d.id === id);
  if (!drop) throw new MockHttpError(404, "NOT_FOUND", "Drop not found");
  return drop;
}

function requireKey(headers: Headers): string {
  const key = headers.get("Idempotency-Key");
  if (!key) throw new MockHttpError(400, "IDEMPOTENCY_KEY_MISSING", "Idempotency-Key is required");
  return key;
}

/** One key belongs to one action; reusing it for something else is a client bug (422). */
function bindKey(d: MockDrop, key: string, fingerprint: string): void {
  const seen = d.idem[key];
  if (seen !== undefined && seen !== fingerprint) {
    throw new MockHttpError(422, "IDEMPOTENCY_KEY_REUSED", "Idempotency key reused");
  }
  d.idem[key] = fingerprint;
}

function freeSeatNo(d: MockDrop): number {
  const taken = new Set(Object.values(d.allocations).map((a) => a.seat_no));
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const seat = 1 + Math.floor(Math.random() * d.capacity);
    if (!taken.has(seat)) return seat;
  }
  return taken.size + 1;
}

interface Reply {
  status: number;
  body: unknown;
  text?: string;
}

async function route(
  state: MockState,
  now: number,
  method: string,
  path: string,
  query: URLSearchParams,
  headers: Headers,
  body: Record<string, unknown>,
): Promise<Reply> {
  const seg = path.split("/").filter(Boolean);
  const ok = (payload: unknown, status = 200): Reply => ({ status, body: payload });

  if (path === "/healthz") return ok({ status: "ok" });
  if (path === "/readyz") return ok({ status: "ready", postgres: "up", redis: "up" });

  if (path === "/auth/otp/request" && method === "POST") {
    const phone = normalisePhone(String(body.phone ?? ""));
    if (!phone) throw new MockHttpError(400, "INVALID_PHONE", "Enter a valid Indian mobile number");
    const requestId = randomId();
    const otp = String(100000 + Math.floor(Math.random() * 900000));
    state.otps = {
      [requestId]: { phone, otp, device: String(body.device_id ?? ""), expires: now + 300_000 },
    };
    return ok({ request_id: requestId, expires_in_s: 300, dev_otp: otp });
  }

  if (path === "/auth/otp/verify" && method === "POST") {
    const pending = state.otps[String(body.request_id ?? "")];
    if (!pending || now > pending.expires) {
      throw new MockHttpError(410, "OTP_EXPIRED", "Code expired, request a new one");
    }
    if (pending.otp !== String(body.otp ?? "")) {
      throw new MockHttpError(401, "OTP_INVALID", "Wrong code");
    }
    state.otps = {};
    const user = state.users[pending.phone] ?? randomPublicId();
    state.users[pending.phone] = user;
    const token = `mock-session.${randomId()}`;
    state.sessions[token] = user;
    state.cookie = token;
    return ok({ session_token: token, user_public_id: user });
  }

  if (seg[0] === "drops" && seg[1]) {
    const d = findDrop(state, seg[1]);
    await advance(state, d, now);
    const action = seg[2];

    if (!action && method === "GET") return ok(dropOut(d, now));

    if (action === "draw-proof" && method === "GET") return ok(proofOut(d, true));

    if (action === "me" && method === "GET") return ok(meOut(d, requireUser(state), now));

    if (action === "entries" && method === "POST") {
      const user = requireUser(state);
      const existing = d.entries[user];
      if (existing) return ok({ entry_id: existing.entry_id, status: "REGISTERED" });
      if (d.phase === "SCHEDULED") {
        throw new MockHttpError(403, "WINDOW_NOT_OPEN", "Registration has not opened yet");
      }
      if (d.phase !== "OPEN") {
        throw new MockHttpError(403, "WINDOW_CLOSED", "Registration is closed");
      }
      const entry: MockEntry = {
        entry_id: randomId(),
        status: "REGISTERED",
        rank: null,
        offer_expires_at: null,
        risk: Math.floor(Math.random() * 40),
        entered_at: iso(now),
        step_up_otp: null,
      };
      d.entries[user] = entry;
      return ok({ entry_id: entry.entry_id, status: "REGISTERED" }, 201);
    }

    if (action === "claim" && method === "POST") {
      const user = requireUser(state);
      const key = requireKey(headers);
      const entry = d.entries[user];
      bindKey(d, key, `claim:${entry?.entry_id ?? user}`);
      const done = d.allocations[user];
      if (done) return ok(done);
      if (!entry) throw new MockHttpError(403, "NOT_OFFERED", "No entry for this drop");
      if (state.faults.tokenInvalidOnce) {
        state.faults.tokenInvalidOnce = false;
        throw new MockHttpError(401, "TOKEN_INVALID", "Admission token is not valid");
      }
      const [, tokenEntry, tokenExp] = String(body.admission_token ?? "").split(".");
      if (tokenEntry !== entry.entry_id || Number(tokenExp) < now) {
        throw new MockHttpError(401, "TOKEN_INVALID", "Admission token is not valid");
      }
      if (entry.status === "STEP_UP_REQUIRED") {
        throw new MockHttpError(423, "STEP_UP_REQUIRED", "Verify the code first");
      }
      if (entry.status === "OFFER_EXPIRED") {
        throw new MockHttpError(409, "OFFER_EXPIRED", "The offer has expired");
      }
      const fifoOpen = d.mode === "fifo" && d.phase === "OPEN" && entry.status === "REGISTERED";
      if (entry.status !== "OFFERED" && !fifoOpen) {
        throw new MockHttpError(403, "NOT_OFFERED", "This entry has no offer");
      }
      if (d.capacity - sold(d, now) <= 0) {
        throw new MockHttpError(409, "SOLD_OUT", "All seats are taken");
      }
      const allocation = {
        allocation_id: randomId(),
        seat_no: freeSeatNo(d),
        confirmed_at: iso(now),
      };
      d.allocations[user] = allocation;
      entry.status = "ALLOCATED";
      return ok(allocation);
    }

    if (action === "step-up" && method === "POST") {
      const user = requireUser(state);
      const key = requireKey(headers);
      const entry = d.entries[user];
      bindKey(d, key, `step-up:${entry?.entry_id ?? user}`);
      if (entry?.status === "OFFERED" || entry?.status === "ALLOCATED") {
        return ok({ status: "OFFERED" });
      }
      if (entry?.status !== "STEP_UP_REQUIRED") {
        throw new MockHttpError(409, "OFFER_EXPIRED", "The offer has expired");
      }
      if (String(body.otp ?? "") !== entry.step_up_otp) {
        throw new MockHttpError(401, "OTP_INVALID", "Wrong code");
      }
      entry.status = "OFFERED";
      entry.step_up_otp = null;
      return ok({ status: "OFFERED" });
    }
  }

  if (seg[0] === "admin") {
    if (!headers.get("X-Admin-Key")) {
      throw new MockHttpError(401, "UNAUTHENTICATED", "Admin key required");
    }
    if (path === "/admin/abuse/config") {
      if (method === "PUT") {
        state.abuse = {
          layers: { ...state.abuse.layers, ...(body.layers as Record<string, boolean>) },
          thresholds: {
            ...state.abuse.thresholds,
            ...((body.thresholds as Record<string, number> | undefined) ?? {}),
          },
        };
      }
      return ok(state.abuse satisfies Omit<AbuseConfig, "server_time">);
    }
    if (path === "/admin/drops" && method === "GET") {
      for (const d of state.drops) await advance(state, d, now);
      return ok({
        drops: state.drops.map((d) => ({
          id: d.id,
          name: d.name,
          mode: d.mode,
          phase: d.phase,
          run_no: d.run_no,
          capacity: d.capacity,
        })),
      });
    }
    if (path === "/admin/drops" && method === "POST") {
      const name = String(body.name ?? "").trim();
      const windowS = Number(body.window_s);
      if (!name || !(windowS >= 1) || (body.mode !== "fair" && body.mode !== "fifo")) {
        throw new MockHttpError(400, "VALIDATION_ERROR", "name, mode and window_s are required");
      }
      const d = await newDrop({
        name,
        mode: body.mode,
        capacity: Number(body.capacity ?? 500),
        window_s: windowS,
        claim_window_s: Number(body.claim_window_s ?? 120),
      });
      state.drops.unshift(d);
      return ok({ drop_id: d.id, seed_commit: d.seed_commit }, 201);
    }
    if (seg[1] === "drops" && seg[2]) {
      const d = findDrop(state, seg[2]);
      await advance(state, d, now);
      switch (seg[3]) {
        case "metrics":
          return ok(
            metricsOut(
              d,
              now,
              Math.min(3600, Number(query.get("window_s") ?? 60)),
              Object.keys(state.sessions).length,
            ),
          );
        case "integrity":
          return ok(integrityOut(d, now));
        case "sim":
          return ok({ latest: simLatest(d, now) });
        case "draw-proof":
          return ok(proofOut(d, false));
        case "export":
          return { status: 200, body: null, text: exportNdjson(d) };
        case "phase":
          await applyPhase(state, d, body.action as PhaseAction, body.mode, now);
          return ok({ phase: d.phase });
      }
    }
  }

  throw new MockHttpError(404, "NOT_FOUND", "Not found");
}

function proofOut(d: MockDrop, withLists: boolean): Omit<DrawProof, "server_time"> {
  if (!d.ranked || !d.entry_set_hash) {
    throw new MockHttpError(409, "INVALID_TRANSITION", "The draw has not run yet");
  }
  return {
    seed_commit: d.seed_commit,
    seed: d.seed,
    entry_set_hash: d.entry_set_hash,
    algorithm: "HMAC_SHA256(seed, drop_id|user_public_id) asc; ties by user_public_id",
    drop_id: d.id,
    run_no: d.run_no,
    eligible_public_ids: withLists ? [...d.ranked].sort(byteOrder) : null,
    ranked_public_ids: withLists ? d.ranked : null,
  };
}

async function applyPhase(
  state: MockState,
  d: MockDrop,
  action: PhaseAction,
  mode: unknown,
  now: number,
): Promise<void> {
  const invalid = () =>
    new MockHttpError(409, "INVALID_TRANSITION", `Cannot ${action} a drop that is ${d.phase}`);
  switch (action) {
    case "open":
      if (d.phase !== "SCHEDULED") throw invalid();
      openDrop(d, now);
      return;
    case "close":
      if (d.phase !== "OPEN") throw invalid();
      closeDrop(d, now);
      return;
    case "draw":
      if (d.phase !== "CLOSED") throw invalid();
      await drawDrop(state, d, now);
      return;
    case "reset": {
      const seed = randomSeed();
      Object.assign(d, {
        phase: "SCHEDULED",
        run_no: d.run_no + 1,
        mode: mode === "fair" || mode === "fifo" ? mode : d.mode,
        reg_opens_at: null,
        reg_closes_at: null,
        auto_open_at: null,
        seed,
        seed_commit: await seedCommit(seed),
        entry_set_hash: null,
        ranked: null,
        closed_at: null,
        drawn_at: null,
        promoted: null,
        entries: {},
        allocations: {},
        idem: {},
      } satisfies Partial<MockDrop>);
      return;
    }
    default:
      throw new MockHttpError(400, "VALIDATION_ERROR", "Unknown action");
  }
}

function respond(status: number, payload: unknown, text?: string): Response {
  const serverTime = new Date().toISOString();
  const headers = new Headers({ "X-Request-ID": randomId(), "X-Server-Time": serverTime });
  if (text !== undefined) {
    headers.set("Content-Type", "application/x-ndjson");
    return new Response(text, { status, headers });
  }
  headers.set("Content-Type", "application/json");
  return new Response(JSON.stringify({ ...(payload as object), server_time: serverTime }), {
    status,
    headers,
  });
}

function errorResponse(err: MockHttpError): Response {
  const res = respond(err.status, {
    error: {
      code: err.code,
      message: err.message,
      ...(err.retryAfterMs !== null ? { retry_after_ms: err.retryAfterMs } : {}),
    },
  });
  if (err.retryAfterMs !== null) {
    res.headers.set("Retry-After", String(Math.ceil(err.retryAfterMs / 1000)));
  }
  return res;
}

/** Drop-in replacement for `fetch` that answers `/api/*` from the mock. */
export async function mockFetch(url: string, init: RequestInit): Promise<Response> {
  const parsed = new URL(url, "http://mock.local");
  const path = parsed.pathname.replace(/^\/api/, "");
  const method = init.method ?? "GET";
  const headers = new Headers(init.headers);
  const body =
    typeof init.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : {};

  const [min, max] = latencyMs;
  if (max > 0)
    await new Promise((resolve) => setTimeout(resolve, min + Math.random() * (max - min)));
  if (init.signal?.aborted) throw new DOMException("Aborted", "AbortError");

  let loseResponse = false;
  const response = await withState(async (state, now) => {
    if (state.faults.offline) throw new TypeError("Failed to fetch");
    const isAdmin = path.startsWith("/admin");
    try {
      if (state.faults.rateLimit > 0 && !isAdmin && path !== "/healthz") {
        state.faults.rateLimit -= 1;
        throw new MockHttpError(429, "RATE_LIMITED", "Slow down", 1500);
      }
      const isClaim = path.endsWith("/claim");
      const reply = await route(state, now, method, path, parsed.searchParams, headers, body);
      if (isClaim && state.faults.lostResponseOnce) {
        state.faults.lostResponseOnce = false;
        loseResponse = true;
      }
      return respond(reply.status, reply.body, reply.text);
    } catch (err) {
      if (err instanceof MockHttpError) return errorResponse(err);
      throw err;
    }
  });
  // The claim above is saved. The caller never hears about it, as if the connection dropped.
  if (loseResponse) throw new TypeError("Failed to fetch");
  return response;
}

// ---------------------------------------------------------------------------------------------
// Demo controls (used by the demo panel and by tests)
// ---------------------------------------------------------------------------------------------

export interface MockSettings {
  faults: Faults;
  outcome: ForcedOutcome;
  signedIn: boolean;
  dropIds: string[];
}

export const mockControls = {
  setLatency(min: number, max: number): void {
    latencyMs = [min, max];
  },
  get(): Promise<MockSettings> {
    return withState((state) => ({
      faults: { ...state.faults },
      outcome: state.outcome,
      signedIn: state.cookie !== null,
      dropIds: state.drops.map((d) => d.id),
    }));
  },
  setFaults(faults: Partial<Faults>): Promise<void> {
    return withState((state) => {
      state.faults = { ...state.faults, ...faults };
    });
  },
  setOutcome(outcome: ForcedOutcome): Promise<void> {
    return withState((state) => {
      state.outcome = outcome;
    });
  },
  signOut(): Promise<void> {
    return withState((state) => {
      state.cookie = null;
    });
  },
  /** Throws away all demo data; the next request re-seeds it. */
  async reset(): Promise<void> {
    await queue;
    clearState();
  },
};
