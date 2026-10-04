import { safeStorage } from "./store";
import { uuid4 } from "./uuid";

const KEY = "fd:device_id";
let fallback: string | null = null;

/** Random per-browser id sent only in the OTP request/verify bodies (L6/L7 input). */
export function deviceId(): string {
  const ls = safeStorage("local");
  try {
    const existing = ls?.getItem(KEY);
    if (existing) return existing;
    const id = uuid4();
    ls?.setItem(KEY, id);
    return id;
  } catch {
    fallback ??= uuid4();
    return fallback;
  }
}
