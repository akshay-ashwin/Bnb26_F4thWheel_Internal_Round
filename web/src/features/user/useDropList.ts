import { useEffect, useState } from "react";

import { adminKey, mocksEnabled } from "../../api/config";
import { api } from "../../api/endpoints";
import { createPoller } from "../../api/poller";
import type { Drop } from "../../api/types";
import { readItem, writeItem } from "../../lib/storage";

const RECENT_KEY = "fd:recentDrops";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isDropId(value: string): boolean {
  return UUID.test(value);
}

function recentDropIds(): string[] {
  try {
    const parsed: unknown = JSON.parse(readItem("local", RECENT_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

/** Remember a drop this browser opened, so it shows on the home page next time. */
export function rememberDrop(dropId: string): void {
  const next = [dropId, ...recentDropIds().filter((id) => id !== dropId)].slice(0, 12);
  writeItem("local", RECENT_KEY, JSON.stringify(next));
}

/**
 * The public API has no "list drops" endpoint, so the home page collects ids from: the demo data
 * (demo mode), the admin list (when an admin key is in this tab), `VITE_DROP_IDS`, and drops this
 * browser has opened before.
 */
async function knownDropIds(): Promise<string[]> {
  const ids: string[] = [];
  if (mocksEnabled()) {
    const { mockControls } = await import("../../mocks/server");
    ids.push(...(await mockControls.get()).dropIds);
  } else if (adminKey()) {
    try {
      ids.push(...(await api.admin.listDrops()).drops.map((d) => d.id));
    } catch {
      // A wrong admin key must not break the public page.
    }
  }
  const configured: string = import.meta.env.VITE_DROP_IDS ?? "";
  ids.push(...configured.split(",").map((s) => s.trim()), ...recentDropIds());
  return [...new Set(ids.filter(isDropId))];
}

export interface DropList {
  drops: Drop[];
  loading: boolean;
  error: unknown;
}

export function useDropList(): DropList {
  const [state, setState] = useState<DropList>({ drops: [], loading: true, error: null });

  useEffect(() => {
    const poller = createPoller<Drop[]>({
      fetch: async () => {
        const ids = await knownDropIds();
        const results = await Promise.allSettled(ids.map((id) => api.getDrop(id)));
        const drops = results.flatMap((r) => (r.status === "fulfilled" ? [r.value] : []));
        const failure = results.find((r) => r.status === "rejected");
        if (drops.length === 0 && failure) throw failure.reason;
        return drops;
      },
      nextDelayMs: () => 5000,
      onData: (drops) => setState({ drops, loading: false, error: null }),
      onError: (error) => {
        setState((prev) => ({ ...prev, loading: false, error }));
        return undefined;
      },
      fallbackMs: 5000,
    });
    poller.start();
    return () => poller.stop();
  }, []);

  return state;
}
