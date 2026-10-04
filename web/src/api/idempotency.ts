// One Idempotency-Key per (drop, user action), kept in sessionStorage so a retry or a refresh
// in the same tab reuses it. Another tab gets its own key; the server collapses them by entry.
import { safeStorage } from "../lib/store";
import { uuid4 } from "../lib/uuid";

export type Action = "enter" | "claim" | "step-up";

const memory = new Map<string, string>();

function keyName(dropId: string, action: Action): string {
  return `idem:${dropId}:${action}`;
}

function pendingName(dropId: string, action: Action): string {
  return `pending:${dropId}:${action}`;
}

// sessionStorage when the browser allows it; an in-memory map only when it does not.
function read(name: string): string | null {
  const ss = safeStorage("session");
  if (!ss) return memory.get(name) ?? null;
  try {
    return ss.getItem(name);
  } catch {
    return memory.get(name) ?? null;
  }
}

function write(name: string, value: string | null): void {
  const ss = safeStorage("session");
  try {
    if (!ss) throw new Error("no sessionStorage");
    if (value === null) ss.removeItem(name);
    else ss.setItem(name, value);
  } catch {
    if (value === null) memory.delete(name);
    else memory.set(name, value);
  }
}

/** The key for this action, created on first use and stable until `finishAction`. */
export function actionKey(dropId: string, action: Action): string {
  const name = keyName(dropId, action);
  const existing = read(name);
  if (existing) return existing;
  const key = uuid4();
  write(name, key);
  return key;
}

/** Marks an action as started, so a refresh can resume it with the same key. */
export function markPending(dropId: string, action: Action): void {
  write(pendingName(dropId, action), "1");
}

export function isPending(dropId: string, action: Action): boolean {
  return read(pendingName(dropId, action)) !== null;
}

/** Terminal answer (success or a final business error): forget the key and the marker. */
export function finishAction(dropId: string, action: Action): void {
  write(keyName(dropId, action), null);
  write(pendingName(dropId, action), null);
}
