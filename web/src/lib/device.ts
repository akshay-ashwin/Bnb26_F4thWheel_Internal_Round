import { randomId, readItem, writeItem } from "./storage";

const KEY = "fd:device";

/** Random per-browser id sent only in the OTP request/verify bodies. Clearing storage changes it. */
export function deviceId(): string {
  const existing = readItem("local", KEY);
  if (existing) return existing;
  const fresh = randomId();
  writeItem("local", KEY, fresh);
  return fresh;
}
