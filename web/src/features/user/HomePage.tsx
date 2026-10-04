import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";

import { mocksEnabled, setMocksEnabled } from "../../api/config";
import { describeError } from "../../api/errors";
import type { Drop } from "../../api/types";
import { Countdown } from "../../components/Countdown";
import { Poster } from "../../components/Poster";
import { Badge, Banner, Button, Card, Skeleton } from "../../components/ui";
import { cx, formatNumber } from "../../lib/format";
import { CATEGORIES, presentationOf, type Category } from "./catalog";
import { isDropId, useDropList } from "./useDropList";

function statusOf(drop: Drop): {
  label: string;
  tone: "success" | "info" | "warning" | "neutral" | "brand";
} {
  switch (drop.phase) {
    case "OPEN":
      return { label: drop.mode === "fair" ? "Entries open" : "On sale", tone: "success" };
    case "SCHEDULED":
      return { label: "Opening soon", tone: "info" };
    case "CLOSED":
    case "DRAWN":
      return { label: "Drawing", tone: "brand" };
    case "CLAIMING":
      return { label: "Claiming", tone: "warning" };
    case "DONE":
      return { label: "Finished", tone: "neutral" };
  }
}

function DropCard({ drop }: { drop: Drop }) {
  const p = presentationOf(drop.name);
  const status = statusOf(drop);
  const live = drop.phase === "OPEN";
  return (
    <Link
      to={`/drop/${drop.id}`}
      className="group border-ink-700 bg-ink-850 hover:border-ink-500 block overflow-hidden rounded-3xl border transition-all hover:-translate-y-1"
    >
      <div className="relative">
        <Poster presentation={p} className="aspect-[4/5] w-full" />
        <div className="absolute top-3 left-3 flex gap-1.5">
          <Badge tone={status.tone} dot={live} className="bg-black/50 backdrop-blur">
            {status.label}
          </Badge>
        </div>
      </div>
      <div className="space-y-1 p-4">
        <p className="text-ink-400 text-xs">{p.when}</p>
        <p className="truncate font-bold">{drop.name}</p>
        <p className="text-ink-400 truncate text-sm">
          {p.venue}, {p.city}
        </p>
        <div className="flex items-center justify-between pt-2 text-sm">
          <span className="text-ink-300">{p.price}</span>
          {drop.phase === "SCHEDULED" && drop.reg_opens_at ? (
            <span className="text-info text-xs font-semibold">
              Opens in <Countdown to={drop.reg_opens_at} />
            </span>
          ) : live && drop.reg_closes_at && drop.mode === "fair" ? (
            <span className="text-success text-xs font-semibold">
              Closes in <Countdown to={drop.reg_closes_at} />
            </span>
          ) : (
            <span className="text-ink-500 text-xs">
              {formatNumber(drop.seats_remaining)} seats left
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}

function Hero({ drop }: { drop: Drop }) {
  const p = presentationOf(drop.name);
  const status = statusOf(drop);
  return (
    <Link
      to={`/drop/${drop.id}`}
      className="group border-ink-700 relative block overflow-hidden rounded-[2rem] border"
    >
      <Poster presentation={p} size="lg" className="h-64 sm:h-80" />
      <div className="absolute top-5 left-5 flex gap-2">
        <Badge tone={status.tone} dot={drop.phase === "OPEN"} className="bg-black/50 backdrop-blur">
          {status.label}
        </Badge>
        {drop.mode === "fair" && (
          <Badge tone="brand" className="bg-black/50 backdrop-blur">
            Fair draw
          </Badge>
        )}
      </div>
      <div className="absolute right-5 bottom-5 hidden items-center gap-3 sm:flex">
        <span className="text-sm text-white/80">
          {p.when} · {p.venue}
        </span>
        <span className="text-ink-950 rounded-full bg-white px-5 py-2.5 text-sm font-bold transition-transform group-hover:scale-105">
          {drop.phase === "OPEN" ? "Enter now" : "View drop"}
        </span>
      </div>
    </Link>
  );
}

function Section({ title, hint, drops }: { title: string; hint?: string; drops: Drop[] }) {
  if (drops.length === 0) return null;
  return (
    <section>
      <div className="mb-4 flex items-baseline justify-between">
        <h2 className="text-xl font-bold tracking-tight sm:text-2xl">{title}</h2>
        {hint && <span className="text-ink-500 text-sm">{hint}</span>}
      </div>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4">
        {drops.map((d) => (
          <DropCard key={d.id} drop={d} />
        ))}
      </div>
    </section>
  );
}

/** Shown when the real API is in use and this browser knows no drops yet. */
function EmptyState({ error }: { error: unknown }) {
  const navigate = useNavigate();
  const [value, setValue] = useState("");
  const id = value.trim().split("/").pop() ?? "";
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (isDropId(id)) void navigate(`/drop/${id}`);
  };
  return (
    <Card className="mx-auto max-w-xl space-y-4 p-8 text-center">
      <h2 className="text-2xl font-bold tracking-tight">No drops to show yet</h2>
      <p className="text-ink-400 text-sm">
        Open a drop link, or create one in the console. Drops you open are remembered here.
      </p>
      {error !== null && <Banner tone="danger">{describeError(error)}</Banner>}
      <form onSubmit={submit} className="flex gap-2">
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Paste a drop link or ID"
          aria-label="Drop link or ID"
          className="border-ink-600 bg-ink-800 focus:border-brand-500 h-10 min-w-0 flex-1 rounded-xl border px-3 text-sm outline-none"
        />
        <Button type="submit" disabled={!isDropId(id)}>
          Open
        </Button>
      </form>
      <div className="flex justify-center gap-2">
        <Link
          to="/admin"
          className="border-ink-600 hover:bg-ink-800 inline-flex h-10 items-center rounded-xl border px-4 text-sm font-semibold"
        >
          Open console
        </Link>
        {!mocksEnabled() && (
          <Button
            variant="ghost"
            onClick={() => {
              setMocksEnabled(true);
              window.location.reload();
            }}
          >
            Explore with demo data
          </Button>
        )}
      </div>
    </Card>
  );
}

export function HomePage() {
  const { drops, loading, error } = useDropList();
  const [category, setCategory] = useState<Category | "All">("All");
  const [query, setQuery] = useState("");

  const visible = drops.filter((d) => {
    const p = presentationOf(d.name);
    const matchesCategory = category === "All" || p.category === category;
    const text = `${d.name} ${p.venue} ${p.city}`.toLowerCase();
    return matchesCategory && text.includes(query.trim().toLowerCase());
  });
  const live = visible.filter((d) => d.phase === "OPEN");
  const inProgress = visible.filter((d) => ["CLOSED", "DRAWN", "CLAIMING"].includes(d.phase));
  const upcoming = visible.filter((d) => d.phase === "SCHEDULED");
  const past = visible.filter((d) => d.phase === "DONE");
  const featured = drops.find((d) => d.phase === "OPEN" && d.mode === "fair") ?? drops[0];

  if (loading) {
    return (
      <div className="mx-auto max-w-6xl space-y-6 px-4 py-6">
        <Skeleton className="h-[22rem] w-full rounded-[2rem]" />
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="aspect-[4/6]" />
          ))}
        </div>
      </div>
    );
  }

  if (drops.length === 0) {
    return (
      <div className="px-4 py-16">
        <EmptyState error={error} />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-10 px-4 py-6">
      {featured && <Hero drop={featured} />}

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="no-scrollbar -mx-4 flex gap-2 overflow-x-auto px-4 sm:mx-0 sm:px-0">
          {(["All", ...CATEGORIES] as const).map((c) => (
            <button
              key={c}
              type="button"
              aria-pressed={category === c}
              onClick={() => setCategory(c)}
              className={cx(
                "shrink-0 rounded-full border px-4 py-1.5 text-sm font-medium transition-colors",
                category === c
                  ? "text-ink-950 border-white bg-white"
                  : "border-ink-700 text-ink-300 hover:border-ink-500",
              )}
            >
              {c}
            </button>
          ))}
        </div>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search artists, venues…"
          aria-label="Search drops"
          className="border-ink-700 bg-ink-850 placeholder:text-ink-500 focus:border-brand-500 h-10 rounded-full border px-4 text-sm outline-none sm:ml-auto sm:w-64"
        />
      </div>

      <Section title="Dropping now" hint="Enter any time before the window closes" drops={live} />
      <Section title="Draw in progress" drops={inProgress} />
      <Section title="Coming up" drops={upcoming} />
      <Section title="Past drops" hint="Every draw can be re-checked" drops={past} />
      {visible.length === 0 && (
        <p className="text-ink-400 py-10 text-center">Nothing matches that search.</p>
      )}

      <section className="border-ink-700 from-brand-700/30 to-ink-850 grid gap-6 rounded-[2rem] border bg-gradient-to-br p-6 sm:grid-cols-3 sm:p-8">
        {[
          [
            "One person, one entry",
            "Entries are tied to a verified phone, not to a browser tab or a script.",
          ],
          [
            "Speed is worth nothing",
            "Entering in the first second or the last gives exactly the same chance.",
          ],
          [
            "A draw you can check",
            "The secret behind the draw is locked in before entries open and revealed after.",
          ],
        ].map(([title, text]) => (
          <div key={title}>
            <p className="font-bold">{title}</p>
            <p className="text-ink-300 mt-1 text-sm">{text}</p>
          </div>
        ))}
        <Link to="/how-it-works" className="text-brand-300 text-sm font-semibold hover:underline">
          How Fair Drop works →
        </Link>
      </section>
    </div>
  );
}
