import { readItem, removeItem, writeItem } from "../lib/storage";

const MOCK_KEY = "fd:mocks";
const ADMIN_KEY = "fd:adminKey";

/**
 * Demo mode serves every endpoint from an in-browser mock of the contract (src/mocks).
 * `VITE_USE_MOCKS=true` turns it on at build/dev time; the runtime switch overrides either way.
 */
export function mocksEnabled(): boolean {
  const override = readItem("local", MOCK_KEY);
  if (override === "1") return true;
  if (override === "0") return false;
  return import.meta.env.VITE_USE_MOCKS === "true";
}

export function setMocksEnabled(on: boolean): void {
  writeItem("local", MOCK_KEY, on ? "1" : "0");
}

/** Admin key lives in sessionStorage only: never localStorage, never the URL. */
export function adminKey(): string | null {
  return readItem("session", ADMIN_KEY);
}

export function setAdminKey(key: string | null): void {
  if (key) writeItem("session", ADMIN_KEY, key);
  else removeItem("session", ADMIN_KEY);
}
