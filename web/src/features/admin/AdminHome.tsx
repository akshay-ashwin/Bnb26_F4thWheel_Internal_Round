import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useOutletContext } from "react-router";

import { api } from "../../api/endpoints";
import { describeError } from "../../api/errors";
import type { DropListItem, Mode } from "../../api/types";
import { Badge, Banner, Button, Card } from "../../components/ui";
import { cx } from "../../lib/format";
import type { AdminContext } from "./AdminGate";

const FIELD =
  "border-ink-600 bg-ink-800 focus:border-brand-500 h-10 w-full rounded-xl border px-3 text-sm outline-none";

function CreateDrop({ onCreated }: { onCreated: (id: string) => void }) {
  const [name, setName] = useState("");
  const [mode, setMode] = useState<Mode>("fair");
  const [windowS, setWindowS] = useState(60);
  const [claimWindowS, setClaimWindowS] = useState(60);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api.admin.createDrop({
        name: name.trim(),
        mode,
        capacity: 500,
        window_s: windowS,
        claim_window_s: claimWindowS,
      });
      onCreated(res.drop_id);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="p-5">
      <h2 className="text-lg font-bold">New drop</h2>
      <form onSubmit={(e) => void submit(e)} className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="sm:col-span-2">
          <span className="text-ink-400 text-xs">Name</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={120}
            placeholder="Artist: Tour name"
            className={FIELD}
          />
        </label>
        <fieldset className="sm:col-span-2">
          <legend className="text-ink-400 text-xs">How seats are given out</legend>
          <div className="mt-1 grid grid-cols-2 gap-2">
            {(
              [
                ["fair", "Fair draw", "Entry window, then a provable draw"],
                ["fifo", "First come", "Fastest request wins (the old way)"],
              ] as const
            ).map(([value, label, hint]) => (
              <button
                key={value}
                type="button"
                aria-pressed={mode === value}
                onClick={() => setMode(value)}
                className={cx(
                  "rounded-xl border p-3 text-left",
                  mode === value ? "border-brand-500 bg-brand-500/10" : "border-ink-600",
                )}
              >
                <span className="block text-sm font-semibold">{label}</span>
                <span className="text-ink-400 block text-xs">{hint}</span>
              </button>
            ))}
          </div>
        </fieldset>
        <label>
          <span className="text-ink-400 text-xs">Entry window (seconds)</span>
          <input
            type="number"
            min={1}
            max={86400}
            value={windowS}
            onChange={(e) => setWindowS(Number(e.target.value))}
            className={FIELD}
          />
        </label>
        <label>
          <span className="text-ink-400 text-xs">Claim window (seconds)</span>
          <input
            type="number"
            min={1}
            max={3600}
            value={claimWindowS}
            onChange={(e) => setClaimWindowS(Number(e.target.value))}
            className={FIELD}
          />
        </label>
        {error !== null && (
          <Banner tone="danger" className="sm:col-span-2">
            {describeError(error)}
          </Banner>
        )}
        <Button type="submit" busy={busy} disabled={!name.trim()} className="sm:col-span-2">
          Create drop (500 seats)
        </Button>
      </form>
    </Card>
  );
}

export function AdminHome() {
  const { lock } = useOutletContext<AdminContext>();
  const navigate = useNavigate();
  const [drops, setDrops] = useState<DropListItem[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      setDrops((await api.admin.listDrops()).drops);
      setError(null);
    } catch (err) {
      setError(err);
    }
  }, []);

  useEffect(() => {
    void load();
    const id = setInterval(() => {
      if (!document.hidden) void load();
    }, 3000);
    return () => clearInterval(id);
  }, [load]);

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-8">
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-black tracking-tight">Organiser console</h1>
        <Button variant="ghost" size="sm" onClick={lock}>
          Lock
        </Button>
      </div>
      {error !== null && <Banner tone="danger">{describeError(error)}</Banner>}
      <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
        <section className="space-y-3">
          <h2 className="text-lg font-bold">Drops</h2>
          {drops?.length === 0 && <p className="text-ink-400 text-sm">No drops yet. Create one.</p>}
          {drops?.map((d) => (
            <Link
              key={d.id}
              to={`/admin/drop/${d.id}`}
              className="border-ink-700 bg-ink-850 hover:border-ink-500 flex items-center justify-between gap-4 rounded-2xl border p-4"
            >
              <div className="min-w-0">
                <p className="truncate font-semibold">{d.name}</p>
                <p className="text-ink-400 text-xs">
                  {d.capacity} seats · run {d.run_no}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <Badge tone={d.mode === "fair" ? "brand" : "warning"}>
                  {d.mode === "fair" ? "Fair" : "FIFO"}
                </Badge>
                <Badge tone={d.phase === "OPEN" || d.phase === "CLAIMING" ? "success" : "neutral"}>
                  {d.phase}
                </Badge>
              </div>
            </Link>
          ))}
        </section>
        <CreateDrop onCreated={(id) => void navigate(`/admin/drop/${id}`)} />
      </div>
    </div>
  );
}
