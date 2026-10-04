import { describe, expect, it } from "vitest";

import type { EntryStatus, Mode, Phase } from "../../api/types";
import { ALLOCATION, drop, entry, me } from "../../test/fixtures";
import { resolveUiState, type ResolverInput, type UiState } from "./resolveUiState";

function input(p: Partial<ResolverInput> = {}): ResolverInput {
  return {
    drop: drop(),
    dropMissing: false,
    authenticated: true,
    me: me(),
    localAllocation: null,
    ...p,
  };
}

function resolve(mode: Mode, phase: Phase, status: EntryStatus | null, seatsRemaining = 10) {
  return resolveUiState(
    input({
      drop: drop({ mode, phase, seats_remaining: seatsRemaining }),
      me: me({ phase, entry: status ? entry({ status }) : null }),
    }),
  );
}

const PHASES: Phase[] = ["SCHEDULED", "OPEN", "CLOSED", "DRAWN", "CLAIMING", "DONE"];
const STATUSES: EntryStatus[] = [
  "REGISTERED",
  "OFFERED",
  "STEP_UP_REQUIRED",
  "WAITLISTED",
  "NOT_SELECTED",
  "OFFER_EXPIRED",
  "ALLOCATED",
  "DISQUALIFIED",
];

describe("resolveUiState: before /me", () => {
  it("drop not found wins over everything", () => {
    expect(resolveUiState(input({ dropMissing: true, authenticated: false }))).toBe(
      "DROP_NOT_FOUND",
    );
  });
  it("loading until the drop is known", () => {
    expect(resolveUiState(input({ drop: null }))).toBe("LOADING");
  });
  it("401 from /me means VERIFY", () => {
    expect(resolveUiState(input({ authenticated: false, me: null }))).toBe("VERIFY");
  });
  it("loading until /me answers", () => {
    expect(resolveUiState(input({ authenticated: null, me: null }))).toBe("LOADING");
  });
});

describe("resolveUiState: precedence allocation > entry status > phase", () => {
  it.each(PHASES.flatMap((p) => STATUSES.map((s) => [p, s] as const)))(
    "allocation wins in phase %s with status %s",
    (phase, status) => {
      for (const mode of ["fair", "fifo"] as const) {
        const st = resolveUiState(
          input({
            drop: drop({ mode, phase, seats_remaining: 0 }),
            me: me({ phase, entry: entry({ status }), allocation: ALLOCATION }),
          }),
        );
        expect(st).toBe("ALLOCATED");
      }
    },
  );

  it("a local claim result wins before /me reflects it", () => {
    const st = resolveUiState(
      input({
        me: me({ phase: "CLAIMING", entry: entry({ status: "OFFERED" }) }),
        localAllocation: ALLOCATION,
      }),
    );
    expect(st).toBe("ALLOCATED");
  });

  const byStatus: [EntryStatus, UiState][] = [
    ["OFFERED", "OFFERED"],
    ["STEP_UP_REQUIRED", "STEP_UP"],
    ["WAITLISTED", "WAITLISTED"],
    ["OFFER_EXPIRED", "OFFER_EXPIRED"],
    ["NOT_SELECTED", "NOT_SELECTED"],
    ["DISQUALIFIED", "NOT_SELECTED"],
    ["ALLOCATED", "ALLOCATED"],
  ];
  it.each(byStatus)("status %s shows %s in every phase (fair)", (status, expected) => {
    for (const phase of PHASES) expect(resolve("fair", phase, status)).toBe(expected);
  });
});

describe("resolveUiState: fair mode by phase", () => {
  const table: [Phase, EntryStatus | null, UiState][] = [
    ["SCHEDULED", null, "BEFORE_WINDOW"],
    ["OPEN", null, "CAN_ENTER"],
    ["CLOSED", null, "REGISTRATION_CLOSED"],
    ["DRAWN", null, "REGISTRATION_CLOSED"],
    ["CLAIMING", null, "REGISTRATION_CLOSED"],
    ["DONE", null, "REGISTRATION_CLOSED"],
    ["SCHEDULED", "REGISTERED", "BEFORE_WINDOW"],
    ["OPEN", "REGISTERED", "ENTERED_WAITING_CLOSE"],
    ["CLOSED", "REGISTERED", "WAITING_DRAW"],
    ["DRAWN", "REGISTERED", "WAITING_DRAW"],
    ["CLAIMING", "REGISTERED", "WAITING_DRAW"],
    ["DONE", "REGISTERED", "NOT_SELECTED"],
  ];
  it.each(table)("phase %s, entry %s -> %s", (phase, status, expected) => {
    expect(resolve("fair", phase, status)).toBe(expected);
  });

  it("the FIFO sold-out rule never applies to a fair drop", () => {
    expect(resolve("fair", "OPEN", null, 0)).toBe("CAN_ENTER");
    expect(resolve("fair", "OPEN", "REGISTERED", 0)).toBe("ENTERED_WAITING_CLOSE");
    expect(resolve("fair", "CLAIMING", "REGISTERED", 0)).toBe("WAITING_DRAW");
  });

  it("uses the /me phase over the cached drop phase", () => {
    const st = resolveUiState(
      input({ drop: drop({ phase: "OPEN" }), me: me({ phase: "CLOSED", entry: entry() }) }),
    );
    expect(st).toBe("WAITING_DRAW");
  });
});

describe("resolveUiState: fifo mode", () => {
  const table: [Phase, EntryStatus | null, number, UiState][] = [
    ["SCHEDULED", null, 10, "BEFORE_WINDOW"],
    ["OPEN", null, 10, "FIFO_RACE"],
    ["OPEN", "REGISTERED", 10, "FIFO_RACE"],
    ["OPEN", null, 0, "SOLD_OUT"],
    ["OPEN", "REGISTERED", 0, "SOLD_OUT"],
    ["DONE", null, 10, "SOLD_OUT"],
    ["DONE", "REGISTERED", 10, "SOLD_OUT"],
    ["CLOSED", null, 10, "REGISTRATION_CLOSED"],
    ["CLOSED", "REGISTERED", 10, "REGISTRATION_CLOSED"],
  ];
  it.each(table)("phase %s, entry %s, %i left -> %s", (phase, status, left, expected) => {
    expect(resolve("fifo", phase, status, left)).toBe(expected);
  });
});

describe("resolveUiState: totality", () => {
  it("returns a state for every mode x phase x status x seats combination", () => {
    for (const mode of ["fair", "fifo"] as const)
      for (const phase of PHASES)
        for (const status of [null, ...STATUSES])
          for (const left of [0, 5])
            expect(resolve(mode, phase, status, left)).toBeTypeOf("string");
  });
});
