import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { connectivity } from "../../api/connectivity";
import { ALLOCATION, DROP_ID, drop, entry, me } from "../../test/fixtures";
import { apiError, mockFetch } from "../../test/mockFetch";
import { copy } from "./copy";
import { UserJourney } from "./UserJourney";

const ME = `GET /drops/${DROP_ID}/me`;
const DROP = `GET /drops/${DROP_ID}`;
const STEP_UP = `POST /drops/${DROP_ID}/step-up`;

beforeEach(() => {
  sessionStorage.clear();
  connectivity.set("online");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function typeCode(code: string) {
  fireEvent.change(screen.getByLabelText("Digit 1"), { target: { value: code } });
}

async function shows(state: string) {
  return waitFor(() => {
    const el = document.querySelector(`[data-state="${state}"]`);
    expect(el).not.toBeNull();
    return el as HTMLElement;
  });
}

describe("step-up screen", () => {
  it("neutral copy, wrong code message, then back to OFFERED with the same key", async () => {
    let passed = false;
    const f = mockFetch({
      [DROP]: () => ({ body: drop({ phase: "CLAIMING" }) }),
      [ME]: () => ({
        body: me({
          phase: "CLAIMING",
          entry: passed
            ? entry({
                status: "OFFERED",
                offer_expires_at: "2026-10-04T10:02:00Z",
                admission_token: "t",
              })
            : entry({
                status: "STEP_UP_REQUIRED",
                step_up_required: true,
                offer_expires_at: "2026-10-04T10:02:00Z",
                dev_otp: "654321",
              }),
        }),
      }),
      [STEP_UP]: (c) => {
        if ((c.body as { otp: string }).otp !== "654321") return apiError(401, "OTP_INVALID");
        passed = true;
        return { body: { status: "OFFERED" } };
      },
    });
    render(<UserJourney dropId={DROP_ID} />);
    const el = await shows("STEP_UP");
    expect(el.textContent).toContain(copy.stepUp.title);
    expect(el.textContent).not.toMatch(/\b(bot|suspicious|fraud|flagged)\b/i);
    expect(screen.getByTestId("dev-otp").textContent).toContain("654321");

    typeCode("111111");
    await screen.findByText(copy.stepUp.otpInvalid);
    expect(screen.queryByTestId("action-error")).toBeNull();

    typeCode("654321");
    await shows("OFFERED");
    const keys = f.of(STEP_UP).map((c) => c.idempotencyKey);
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
  });
});

describe("waitlist screen", () => {
  it("shows the live position from /me", async () => {
    mockFetch({
      [DROP]: () => ({ body: drop({ phase: "CLAIMING" }) }),
      [ME]: () => ({
        body: me({ phase: "CLAIMING", entry: entry({ status: "WAITLISTED", waitlist_pos: 37 }) }),
      }),
    });
    render(<UserJourney dropId={DROP_ID} />);
    const el = await shows("WAITLISTED");
    expect(el.textContent).toContain("You're #37 on the waitlist.");
    expect(el.textContent).toContain(copy.waitlisted.body);
  });
});

describe("FIFO screens", () => {
  it("race screen labels the mode and has a Get a seat button", async () => {
    mockFetch({
      [DROP]: () => ({ body: drop({ mode: "fifo", seats_remaining: 12 }) }),
      [ME]: () => ({ body: me() }),
    });
    render(<UserJourney dropId={DROP_ID} />);
    const el = await shows("FIFO_RACE");
    expect(el.textContent).toContain(copy.fifo.label);
    expect(el.textContent).toContain("12 left");
    expect(screen.getByRole("button", { name: copy.fifo.button })).toBeTruthy();
  });

  it("sold out has no button at all", async () => {
    mockFetch({
      [DROP]: () => ({ body: drop({ mode: "fifo", seats_remaining: 0 }) }),
      [ME]: () => ({ body: me() }),
    });
    render(<UserJourney dropId={DROP_ID} />);
    await shows("SOLD_OUT");
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });
});

describe("terminal screens never offer a retry", () => {
  it.each([
    ["OFFER_EXPIRED", "OFFER_EXPIRED"],
    ["NOT_SELECTED", "NOT_SELECTED"],
    ["DISQUALIFIED", "NOT_SELECTED"],
  ] as const)("status %s -> %s screen without buttons", async (status, state) => {
    mockFetch({
      [DROP]: () => ({ body: drop({ phase: "CLAIMING" }) }),
      [ME]: () => ({ body: me({ phase: "CLAIMING", entry: entry({ status }) }) }),
    });
    render(<UserJourney dropId={DROP_ID} />);
    await shows(state);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("allocated shows the seat and confirmation id", async () => {
    mockFetch({
      [DROP]: () => ({ body: drop({ phase: "CLAIMING" }) }),
      [ME]: () => ({
        body: me({
          phase: "CLAIMING",
          entry: entry({ status: "ALLOCATED" }),
          allocation: ALLOCATION,
        }),
      }),
    });
    render(<UserJourney dropId={DROP_ID} />);
    await shows("ALLOCATED");
    expect(screen.getByTestId("seat-no").textContent).toBe("42");
    expect(screen.getByTestId("allocation-id").textContent).toBe("7D1F0C2E");
  });
});

describe("fair entry screens show the promise", () => {
  it("can-enter shows 'arriving early doesn't help' and the commitment", async () => {
    mockFetch({ [DROP]: () => ({ body: drop() }), [ME]: () => ({ body: me() }) });
    render(<UserJourney dropId={DROP_ID} />);
    const el = await shows("CAN_ENTER");
    expect(el.textContent).toContain(copy.earlyDoesntHelp);
    expect(screen.getByTestId("seed-commit").textContent).toContain("b4f6b1aa9fa8…");
  });
});
