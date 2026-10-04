import { defineConfig } from "@playwright/test";

// Runs in the `e2e` compose service against the real stack (uv run fd test-web --e2e).
export default defineConfig({
  testDir: "e2e",
  // One worker: the tests share the api's OTP limits and use the admin API.
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  outputDir: "test-results",
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://web:5173",
    // Plan 16: the user journey must work on a 360 px phone.
    viewport: { width: 360, height: 740 },
    trace: "retain-on-failure",
  },
});
