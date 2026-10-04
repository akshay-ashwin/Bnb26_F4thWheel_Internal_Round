import { useState, type FormEvent } from "react";
import { Outlet } from "react-router";

import { adminKey, mocksEnabled, setAdminKey } from "../../api/config";
import { api } from "../../api/endpoints";
import { describeError } from "../../api/errors";
import { Banner, Button, Card } from "../../components/ui";

/**
 * Asks for the admin key once per tab. The key is kept in sessionStorage only (never
 * localStorage, never the URL) and is checked against the API before the console opens.
 */
export function AdminGate() {
  const [unlocked, setUnlocked] = useState(() => adminKey() !== null);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setAdminKey(value.trim());
    try {
      await api.admin.listDrops();
      setUnlocked(true);
    } catch (err) {
      setAdminKey(null);
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  if (unlocked) {
    return (
      <Outlet
        context={{
          lock: () => {
            setAdminKey(null);
            setUnlocked(false);
          },
        }}
      />
    );
  }

  return (
    <div className="mx-auto max-w-md px-4 py-16">
      <Card className="p-6">
        <h1 className="text-2xl font-black tracking-tight">Organiser console</h1>
        <p className="text-ink-400 mt-1 text-sm">
          Enter the admin key to run drops and watch them live.
        </p>
        <form onSubmit={(e) => void submit(e)} className="mt-5 space-y-3">
          <input
            type="password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoComplete="off"
            autoFocus
            aria-label="Admin key"
            placeholder="Admin key"
            className="border-ink-600 bg-ink-800 focus:border-brand-500 h-12 w-full rounded-xl border px-4 font-mono outline-none"
          />
          {mocksEnabled() && <p className="text-ink-400 text-xs">Demo data: any key works here.</p>}
          {error !== null && <Banner tone="danger">{describeError(error)}</Banner>}
          <Button type="submit" size="lg" className="w-full" busy={busy} disabled={!value.trim()}>
            Unlock
          </Button>
        </form>
      </Card>
    </div>
  );
}

export interface AdminContext {
  lock: () => void;
}
