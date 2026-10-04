// Plan 16 e2e (lean mode, D-004): 1 happy path, 2 refresh at every state, 4 lost response
// during confirm, 3 three tabs. Real stack, Fair mode, admin API for phase changes.
import { expect, test, type Page, type Request } from "@playwright/test";

import { adminApi, expectState, screenOf, shot, signIn, type Admin } from "./helpers";

let admin: Admin;

test.beforeAll(async () => {
  admin = await adminApi();
});

test.afterAll(async () => {
  await admin.dispose();
});

async function enter(page: Page): Promise<void> {
  await expectState(page, "CAN_ENTER");
  await page.getByRole("button", { name: "Enter the draw" }).click();
  await expectState(page, "ENTERED_WAITING_CLOSE");
}

async function closeAndDraw(dropId: string, pages: Page[]): Promise<void> {
  await admin.phase(dropId, "close");
  for (const p of pages) await expectState(p, "WAITING_DRAW");
  await admin.phase(dropId, "draw");
}

async function reloadKeeps(page: Page, state: string): Promise<void> {
  await page.reload();
  await expectState(page, state);
}

test("1. happy path: verify, enter, close + draw, offered, confirm, allocated", async ({
  page,
}) => {
  const dropId = await admin.createDrop({ mode: "fair", capacity: 2 });
  await signIn(page, dropId);
  await expectState(page, "BEFORE_WINDOW");
  await expect(page.getByTestId("seed-commit")).toContainText("Draw commitment:");
  await shot(page, "01-before-window");

  await admin.phase(dropId, "open");
  await expectState(page, "CAN_ENTER");
  await expect(screenOf(page, "CAN_ENTER")).toContainText("Arriving early doesn't help.");
  await shot(page, "02-can-enter");
  await enter(page);
  await shot(page, "03-entered");

  await admin.phase(dropId, "close");
  await expectState(page, "WAITING_DRAW");
  await shot(page, "04-waiting-draw");
  await admin.phase(dropId, "draw");
  await expectState(page, "OFFERED");
  await expect(page.getByTestId("countdown")).toContainText("Confirm within");
  await shot(page, "05-offered");

  await page.getByRole("button", { name: "Confirm my seat" }).click();
  await expectState(page, "ALLOCATED");
  const seat = await page.getByTestId("seat-no").textContent();
  await shot(page, "06-allocated");

  const allocations = await admin.allocations(dropId);
  expect(allocations).toHaveLength(1);
  expect(String(allocations[0]?.seat_no)).toBe(seat);
  await admin.integrityOk(dropId);
});

test("2. refresh at every state shows the same screen", async ({ page, browser }) => {
  // capacity 1 and two people: one is offered, the other is waitlisted.
  const dropId = await admin.createDrop({ mode: "fair", capacity: 1 });
  const otherCtx = await browser.newContext();
  const other = await otherCtx.newPage();
  try {
    await signIn(page, dropId);
    await signIn(other, dropId);
    await expectState(page, "BEFORE_WINDOW");
    await reloadKeeps(page, "BEFORE_WINDOW");

    await admin.phase(dropId, "open");
    await expectState(page, "CAN_ENTER");
    await reloadKeeps(page, "CAN_ENTER");
    await enter(page);
    await reloadKeeps(page, "ENTERED_WAITING_CLOSE");
    await expectState(other, "CAN_ENTER");
    await enter(other);

    await admin.phase(dropId, "close");
    await expectState(page, "WAITING_DRAW");
    await reloadKeeps(page, "WAITING_DRAW");
    await admin.phase(dropId, "draw");

    // Whoever won is OFFERED; the other is #1 on the waitlist.
    const winner =
      (await screenOf(page, "OFFERED")
        .or(screenOf(page, "WAITLISTED"))
        .getAttribute("data-state")) === "OFFERED"
        ? page
        : other;
    const waiting = winner === page ? other : page;
    await expectState(winner, "OFFERED");
    await reloadKeeps(winner, "OFFERED");
    await expectState(waiting, "WAITLISTED");
    await expect(screenOf(waiting, "WAITLISTED")).toContainText("You're #1 on the waitlist.");
    await shot(waiting, "07-waitlisted");
    await reloadKeeps(waiting, "WAITLISTED");

    await winner.getByRole("button", { name: "Confirm my seat" }).click();
    await expectState(winner, "ALLOCATED");
    const seat = await winner.getByTestId("seat-no").textContent();
    await reloadKeeps(winner, "ALLOCATED");
    await expect(winner.getByTestId("seat-no")).toHaveText(seat ?? "");
    expect(await admin.allocations(dropId)).toHaveLength(1);
  } finally {
    await otherCtx.close();
  }
});

test("4. offline during confirm: response lost, back online, one allocation", async ({
  page,
  context,
}) => {
  const dropId = await admin.createDrop({ mode: "fair", capacity: 2 });
  await admin.phase(dropId, "open");
  await signIn(page, dropId);
  await enter(page);
  await closeAndDraw(dropId, [page]);
  await expectState(page, "OFFERED");

  const claimKeys: string[] = [];
  page.on("request", (r: Request) => {
    if (r.method() === "POST" && r.url().endsWith("/claim"))
      claimKeys.push(r.headers()["idempotency-key"] ?? "");
  });
  let firstStatus = 0;
  // The claim reaches the server and commits; the network dies before the answer arrives.
  await page.route(
    "**/api/drops/*/claim",
    async (route) => {
      const res = await route.fetch();
      firstStatus = res.status();
      await context.setOffline(true);
      await route.abort("internetdisconnected");
    },
    { times: 1 },
  );

  await page.getByRole("button", { name: "Confirm my seat" }).click();
  await expect(page.getByTestId("reconnecting")).toBeVisible();
  await expect(page.getByTestId("reconnecting")).toContainText("your place is safe");
  await shot(page, "08-reconnecting");
  expect(firstStatus).toBe(200);
  // The server already holds the seat although the browser never saw the answer.
  expect(await admin.allocations(dropId)).toHaveLength(1);
  await expect(screenOf(page, "ALLOCATED")).toHaveCount(0);

  await context.setOffline(false);
  await expectState(page, "ALLOCATED");
  await expect(page.getByTestId("reconnecting")).toHaveCount(0);

  const allocations = await admin.allocations(dropId);
  expect(allocations).toHaveLength(1);
  await expect(page.getByTestId("seat-no")).toHaveText(String(allocations[0]?.seat_no));
  expect(claimKeys.length).toBeGreaterThanOrEqual(2);
  expect(new Set(claimKeys).size).toBe(1);
  await admin.integrityOk(dropId);
});

test("3. three tabs: confirm in tab 2, every tab shows the same seat", async ({ context }) => {
  const dropId = await admin.createDrop({ mode: "fair", capacity: 2 });
  await admin.phase(dropId, "open");
  const tab1 = await context.newPage();
  await signIn(tab1, dropId);
  await enter(tab1);
  const tab2 = await context.newPage();
  const tab3 = await context.newPage();
  await tab2.goto(`/drop/${dropId}`);
  await tab3.goto(`/drop/${dropId}`);
  const tabs = [tab1, tab2, tab3];
  for (const t of tabs) await expectState(t, "ENTERED_WAITING_CLOSE");

  await closeAndDraw(dropId, tabs);
  for (const t of tabs) await expectState(t, "OFFERED");

  await tab2.getByRole("button", { name: "Confirm my seat" }).click();
  await expectState(tab2, "ALLOCATED");
  const seat = await tab2.getByTestId("seat-no").textContent();
  // "Within one poll": OFFERED polls every ~1 s; allow a few polls of slack.
  for (const t of [tab1, tab3]) {
    await expect(screenOf(t, "ALLOCATED")).toBeVisible({ timeout: 4_000 });
    await expect(t.getByTestId("seat-no")).toHaveText(seat ?? "");
  }
  expect(await admin.allocations(dropId)).toHaveLength(1);
  await admin.integrityOk(dropId);
});
