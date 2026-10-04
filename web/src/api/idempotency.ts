import { randomId, readItem, removeItem, writeItem } from "../lib/storage";

/** One user action that needs an Idempotency-Key. One key per (drop, action), never per request. */
export type Action = "enter" | "claim" | "step-up";

const keyName = (dropId: string, action: Action) => `idem:${dropId}:${action}`;
const pendingName = (dropId: string, action: Action) => `pending:${dropId}:${action}`;

/**
 * The key for this action in this tab. Created on the first attempt and reused on every retry and
 * after a refresh (sessionStorage survives reloads). Another tab gets its own key, which is fine:
 * the server collapses both onto the same entry.
 */
export function idempotencyKey(dropId: string, action: Action): string {
  const name = keyName(dropId, action);
  const existing = readItem("session", name);
  if (existing) return existing;
  const fresh = randomId();
  writeItem("session", name, fresh);
  return fresh;
}

/** Call only on a terminal outcome: success, or a final business error. */
export function clearIdempotencyKey(dropId: string, action: Action): void {
  removeItem("session", keyName(dropId, action));
  removeItem("session", pendingName(dropId, action));
}

/** Marks an action as started so a refresh in the middle can resume it with the same key. */
export function markPending(dropId: string, action: Action): void {
  writeItem("session", pendingName(dropId, action), "1");
}

export function isPending(dropId: string, action: Action): boolean {
  return readItem("session", pendingName(dropId, action)) === "1";
}
