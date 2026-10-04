import { createStore } from "../lib/store";

export type Connectivity = "online" | "reconnecting";

/** Drives the "Reconnecting… your place is safe" banner. */
export const connectivity = createStore<Connectivity>("online");

/** Set while the server keeps answering 429 so the "Slow down a moment" notice can show. */
export const rateLimitedUntil = createStore<number>(0);

export function browserOffline(): boolean {
  return typeof navigator !== "undefined" && navigator.onLine === false;
}

/** Resolves on the browser `online` event (or immediately if already online). */
export function waitForOnline(): Promise<void> {
  if (!browserOffline()) return Promise.resolve();
  return new Promise((resolve) => {
    window.addEventListener("online", () => resolve(), { once: true });
  });
}
