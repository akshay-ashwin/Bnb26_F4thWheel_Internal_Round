import { useCallback, useEffect, useState } from "react";
import { useMatch } from "react-router";

import { setAdminKey } from "../api/config";
import { api } from "../api/endpoints";
import { describeError } from "../api/errors";
import type { PhaseAction } from "../api/types";
import { mockControls, type MockSettings } from "../mocks/server";
import type { ForcedOutcome } from "../mocks/state";
import { cx } from "../lib/format";
import { useSession } from "../state/session";
import { Button } from "./ui";

const OUTCOMES: { value: ForcedOutcome; label: string }[] = [
  { value: "natural", label: "Real odds" },
  { value: "win", label: "I get a seat" },
  { value: "step_up", label: "Seat + extra check" },
  { value: "waitlist", label: "Waitlist #12" },
  { value: "not_selected", label: "Not selected" },
];

function Toggle({
  label,
  hint,
  on,
  onChange,
}: {
  label: string;
  hint: string;
  on: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3">
      <input
        type="checkbox"
        checked={on}
        onChange={(e) => onChange(e.target.checked)}
        className="accent-brand-500 mt-1 h-4 w-4"
      />
      <span>
        <span className="block text-sm font-medium">{label}</span>
        <span className="text-ink-400 block text-xs">{hint}</span>
      </span>
    </label>
  );
}

/**
 * Demo mode only. Lets a presenter reach every screen and failure on purpose: steer the draw
 * result, move the drop through its phases, and break the network in specific ways.
 */
export function DemoPanel() {
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState<MockSettings | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const dropId = useMatch("/drop/:dropId/*")?.params.dropId;

  const reload = useCallback(async () => setSettings(await mockControls.get()), []);
  useEffect(() => {
    if (open) void reload();
  }, [open, reload]);

  const phase = async (action: PhaseAction) => {
    if (!dropId) return;
    setAdminKey("demo-admin-key");
    try {
      const res = await api.admin.setPhase(dropId, { action });
      setMessage(`Drop is now ${res.phase}`);
    } catch (err) {
      setMessage(describeError(err));
    }
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="bg-brand-600 hover:bg-brand-500 fixed right-4 bottom-20 z-40 rounded-full px-4 py-2.5 text-sm font-bold text-white shadow-xl shadow-black/50 md:bottom-4"
      >
        ⚙ Demo controls
      </button>
    );
  }

  const faults = settings?.faults;
  return (
    <aside
      aria-label="Demo controls"
      className="animate-rise border-ink-700 bg-ink-900 fixed right-4 bottom-20 z-40 max-h-[75vh] w-[min(22rem,calc(100vw-2rem))] space-y-4 overflow-y-auto rounded-3xl border p-5 shadow-2xl shadow-black/60 md:bottom-4"
    >
      <div className="flex items-center justify-between">
        <h2 className="font-bold">Demo controls</h2>
        <button
          type="button"
          aria-label="Close demo controls"
          className="text-ink-400 hover:text-ink-100"
          onClick={() => setOpen(false)}
        >
          ✕
        </button>
      </div>
      <p className="text-ink-400 text-xs">
        You are on demo data: a mock of the API running in this browser. Nothing here touches the
        real backend.
      </p>

      <section className="space-y-2">
        <h3 className="text-ink-400 text-xs font-semibold tracking-wide uppercase">
          My result in the next draw
        </h3>
        <div className="flex flex-wrap gap-1.5">
          {OUTCOMES.map((o) => (
            <button
              key={o.value}
              type="button"
              aria-pressed={settings?.outcome === o.value}
              onClick={() => void mockControls.setOutcome(o.value).then(reload)}
              className={cx(
                "rounded-full border px-3 py-1 text-xs font-medium",
                settings?.outcome === o.value
                  ? "border-brand-500 bg-brand-500/20 text-brand-300"
                  : "border-ink-600 text-ink-300 hover:border-ink-500",
              )}
            >
              {o.label}
            </button>
          ))}
        </div>
      </section>

      <section className="space-y-2">
        <h3 className="text-ink-400 text-xs font-semibold tracking-wide uppercase">
          This drop {dropId ? "" : "(open a drop first)"}
        </h3>
        <div className="grid grid-cols-3 gap-1.5">
          {(["open", "close", "draw"] as const).map((action) => (
            <Button
              key={action}
              size="sm"
              variant="secondary"
              disabled={!dropId}
              onClick={() => phase(action)}
              className="capitalize"
            >
              {action}
            </Button>
          ))}
        </div>
        <Button
          size="sm"
          variant="ghost"
          disabled={!dropId}
          onClick={() => phase("reset")}
          className="w-full"
        >
          Reset this drop (new run)
        </Button>
        {message && <p className="text-ink-300 text-xs">{message}</p>}
      </section>

      {faults && (
        <section className="space-y-3">
          <h3 className="text-ink-400 text-xs font-semibold tracking-wide uppercase">
            Break things
          </h3>
          <Toggle
            label="Connection lost"
            hint="Every request fails until you turn this off."
            on={faults.offline}
            onChange={(on) => void mockControls.setFaults({ offline: on }).then(reload)}
          />
          <Toggle
            label="Rate limit me"
            hint="The next 3 requests answer 429 with a wait time."
            on={faults.rateLimit > 0}
            onChange={(on) => void mockControls.setFaults({ rateLimit: on ? 3 : 0 }).then(reload)}
          />
          <Toggle
            label="Expired claim pass"
            hint="The next claim is rejected once (TOKEN_INVALID), then recovers."
            on={faults.tokenInvalidOnce}
            onChange={(on) => void mockControls.setFaults({ tokenInvalidOnce: on }).then(reload)}
          />
          <Toggle
            label="Lost response"
            hint="The next claim is saved by the server but the answer never arrives."
            on={faults.lostResponseOnce}
            onChange={(on) => void mockControls.setFaults({ lostResponseOnce: on }).then(reload)}
          />
        </section>
      )}

      <section className="grid grid-cols-2 gap-1.5">
        <Button
          size="sm"
          variant="secondary"
          disabled={!settings?.signedIn}
          onClick={async () => {
            await mockControls.signOut();
            useSession.getState().signedOut();
            await reload();
          }}
        >
          Sign out
        </Button>
        <Button
          size="sm"
          variant="danger"
          onClick={async () => {
            await mockControls.reset();
            window.sessionStorage.clear();
            useSession.getState().signedOut();
            window.location.assign("/");
          }}
        >
          Reset demo data
        </Button>
      </section>
    </aside>
  );
}
