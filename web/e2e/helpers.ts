import { expect, request, type APIRequestContext, type Page } from "@playwright/test";

const API = process.env.E2E_API_URL ?? "http://api:8000/api";
const SCREENS_DIR = process.env.SCREENS_DIR;

export interface ExportRow {
  user_public_id: string;
  status: string;
  seat_no?: number | null;
}

/** Admin calls go straight to the api (never through the browser), with X-Admin-Key. */
export async function adminApi(): Promise<Admin> {
  const key = process.env.ADMIN_KEY;
  if (!key) throw new Error("ADMIN_KEY is not set for the e2e container");
  const ctx = await request.newContext({
    baseURL: `${API}/`,
    extraHTTPHeaders: { "X-Admin-Key": key },
  });
  return new Admin(ctx);
}

export class Admin {
  constructor(private readonly ctx: APIRequestContext) {}

  async createDrop(opts: {
    mode: "fair" | "fifo";
    capacity: number;
    claimWindowS?: number;
  }): Promise<string> {
    const res = await this.ctx.post("admin/drops", {
      data: {
        name: `e2e ${opts.mode} ${Date.now()}`,
        mode: opts.mode,
        capacity: opts.capacity,
        window_s: 600,
        claim_window_s: opts.claimWindowS ?? 120,
      },
    });
    expect(res.status(), await res.text()).toBe(201);
    return ((await res.json()) as { drop_id: string }).drop_id;
  }

  async phase(dropId: string, action: "open" | "close" | "draw"): Promise<void> {
    const res = await this.ctx.post(`admin/drops/${dropId}/phase`, {
      data: { action },
      timeout: 30_000,
    });
    expect(res.status(), await res.text()).toBe(200);
  }

  /** The privacy-safe NDJSON export: one row per entry. */
  async exportRows(dropId: string): Promise<ExportRow[]> {
    const res = await this.ctx.get(`admin/drops/${dropId}/export`);
    expect(res.status()).toBe(200);
    return (await res.text())
      .split("\n")
      .filter((l) => l.trim() !== "")
      .map((l) => JSON.parse(l) as ExportRow);
  }

  async allocations(dropId: string): Promise<ExportRow[]> {
    return (await this.exportRows(dropId)).filter((r) => typeof r.seat_no === "number");
  }

  async integrityOk(dropId: string): Promise<void> {
    const res = await this.ctx.get(`admin/drops/${dropId}/integrity`);
    const body = (await res.json()) as { invariant_ok: boolean; oversold: number };
    expect(body.invariant_ok).toBe(true);
    expect(body.oversold).toBe(0);
  }

  dispose(): Promise<void> {
    return this.ctx.dispose();
  }
}

/** A fresh Indian mobile per call, so the per-phone OTP limit never trips across runs. */
export function randomPhone(): string {
  let digits = "9";
  for (let i = 0; i < 9; i++) digits += Math.floor(Math.random() * 10);
  return digits;
}

export function screenOf(page: Page, state: string) {
  return page.locator(`[data-state="${state}"]`);
}

export async function expectState(page: Page, state: string): Promise<void> {
  await expect(screenOf(page, state)).toBeVisible();
  await expectNoHorizontalScroll(page);
}

export async function expectNoHorizontalScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow, "page scrolls horizontally").toBeLessThanOrEqual(0);
}

/** Phone + dev OTP through the real UI (the api must run with SIM_MODE=true). */
export async function signIn(page: Page, dropId: string): Promise<void> {
  await page.goto(`/drop/${dropId}`);
  await expectState(page, "VERIFY");
  await page.getByLabel("Mobile number").fill(randomPhone());
  await page.getByRole("button", { name: "Send code" }).click();
  const hint = await page.getByTestId("dev-otp").textContent();
  const code = /(\d{6})/.exec(hint ?? "")?.[1];
  if (!code) throw new Error("no dev_otp shown: is SIM_MODE=true on the api?");
  await page.getByLabel("Digit 1").fill(code);
}

export async function shot(page: Page, name: string): Promise<void> {
  if (SCREENS_DIR) await page.screenshot({ path: `${SCREENS_DIR}/${name}.png`, fullPage: true });
}
