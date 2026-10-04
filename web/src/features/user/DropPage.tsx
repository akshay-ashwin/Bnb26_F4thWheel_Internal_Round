import { useEffect } from "react";
import { Link, useParams } from "react-router";

import { describeError, isApiError } from "../../api/errors";
import type { Drop, Phase } from "../../api/types";
import { Poster } from "../../components/Poster";
import { Badge, Banner, Card, Skeleton } from "../../components/ui";
import { cx, formatNumber, shortId } from "../../lib/format";
import { presentationOf } from "./catalog";
import { TicketPanel } from "./TicketPanel";
import { useDropJourney } from "./useDropJourney";
import { isDropId, rememberDrop } from "./useDropList";

const FAIR_STEPS: { phases: Phase[]; title: string; text: string }[] = [
  {
    phases: ["SCHEDULED"],
    title: "Seed locked",
    text: "A secret number is chosen and its fingerprint is published before anyone can enter.",
  },
  {
    phases: ["OPEN"],
    title: "Entry window",
    text: "Every verified person may enter once. When you enter does not matter.",
  },
  {
    phases: ["CLOSED", "DRAWN"],
    title: "Draw",
    text: "The entrant list is frozen, then ranked using the secret number.",
  },
  {
    phases: ["CLAIMING"],
    title: "Claim",
    text: "The first ranks get a held seat. Unclaimed seats go down the waitlist.",
  },
  {
    phases: ["DONE"],
    title: "Proof",
    text: "The secret is revealed so anyone can re-run the draw and check it.",
  },
];

function Timeline({ phase }: { phase: Phase }) {
  const current = FAIR_STEPS.findIndex((s) => s.phases.includes(phase));
  return (
    <ol className="space-y-0">
      {FAIR_STEPS.map((step, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <li key={step.title} className="relative flex gap-4 pb-5 last:pb-0">
            {i < FAIR_STEPS.length - 1 && (
              <span
                className={cx(
                  "absolute top-7 left-[13px] h-[calc(100%-1.75rem)] w-px",
                  done ? "bg-brand-500" : "bg-ink-700",
                )}
              />
            )}
            <span
              className={cx(
                "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-bold",
                done && "border-brand-500 bg-brand-500 text-white",
                active && "border-brand-400 text-brand-300 bg-brand-500/20",
                !done && !active && "border-ink-600 text-ink-500",
              )}
            >
              {done ? "✓" : i + 1}
            </span>
            <div>
              <p className={cx("text-sm font-semibold", !done && !active && "text-ink-400")}>
                {step.title}
                {active && <span className="text-brand-400 ml-2 text-xs font-medium">now</span>}
              </p>
              <p className="text-ink-400 text-sm">{step.text}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function PhaseBadge({ drop }: { drop: Drop }) {
  switch (drop.phase) {
    case "SCHEDULED":
      return <Badge tone="info">Opening soon</Badge>;
    case "OPEN":
      return (
        <Badge tone="success" dot>
          Live now
        </Badge>
      );
    case "CLOSED":
    case "DRAWN":
      return <Badge tone="brand">Drawing</Badge>;
    case "CLAIMING":
      return (
        <Badge tone="warning" dot>
          Claiming
        </Badge>
      );
    case "DONE":
      return <Badge>Finished</Badge>;
  }
}

function DropView({ dropId }: { dropId: string }) {
  const journey = useDropJourney(dropId);
  const { drop, dropError } = journey;

  useEffect(() => {
    if (drop) rememberDrop(drop.id);
  }, [drop]);

  if (!drop) {
    if (dropError !== null) {
      return (
        <div className="mx-auto max-w-xl px-4 py-20 text-center">
          <h1 className="text-3xl font-black tracking-tight">
            {isApiError(dropError, "NOT_FOUND") ? "Drop not found" : "We can't load this drop"}
          </h1>
          <p className="text-ink-400 mt-2">{describeError(dropError)}</p>
          <Link to="/" className="text-brand-400 mt-6 inline-block font-medium hover:underline">
            ← All drops
          </Link>
        </div>
      );
    }
    return (
      <div className="mx-auto max-w-6xl space-y-6 px-4 py-6">
        <Skeleton className="h-72 w-full rounded-3xl" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  const presentation = presentationOf(drop.name);
  const fair = drop.mode === "fair";
  const drawn = drop.seed !== null;

  return (
    <div>
      <div className="relative">
        <Poster presentation={presentation} size="lg" className="h-72 sm:h-96" />
        <div className="from-ink-950 absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t to-transparent" />
        <Link
          to="/"
          className="absolute top-4 left-4 rounded-full bg-black/40 px-3 py-1.5 text-sm font-medium text-white backdrop-blur hover:bg-black/60"
        >
          ← Drops
        </Link>
      </div>

      <div className="mx-auto grid max-w-6xl gap-8 px-4 py-6 lg:grid-cols-[1fr_24rem]">
        <div className="order-2 space-y-8 lg:order-1">
          <section>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <PhaseBadge drop={drop} />
              <Badge tone={fair ? "brand" : "warning"}>
                {fair ? "Fair draw" : "First come, first served"}
              </Badge>
            </div>
            <h1 className="text-3xl font-black tracking-tight sm:text-4xl">{drop.name}</h1>
            <dl className="text-ink-300 mt-4 grid gap-3 text-sm sm:grid-cols-3">
              <div className="border-ink-700 bg-ink-850 rounded-2xl border p-3">
                <dt className="text-ink-500 text-xs">When</dt>
                <dd className="font-medium">{presentation.when}</dd>
              </div>
              <div className="border-ink-700 bg-ink-850 rounded-2xl border p-3">
                <dt className="text-ink-500 text-xs">Where</dt>
                <dd className="font-medium">
                  {presentation.venue}, {presentation.city}
                </dd>
              </div>
              <div className="border-ink-700 bg-ink-850 rounded-2xl border p-3">
                <dt className="text-ink-500 text-xs">Seats</dt>
                <dd className="font-medium">
                  {formatNumber(drop.seats_remaining)} of {formatNumber(drop.capacity)} left ·{" "}
                  {presentation.price}
                </dd>
              </div>
            </dl>
          </section>

          <section>
            <h2 className="mb-2 text-lg font-bold">About</h2>
            <p className="text-ink-300 leading-relaxed">{presentation.about}</p>
          </section>

          {fair ? (
            <section>
              <h2 className="mb-4 text-lg font-bold">How seats are given out</h2>
              <Card className="p-5">
                <Timeline phase={drop.phase} />
              </Card>
            </section>
          ) : (
            <Banner tone="warning" title="This drop uses the old first-come model">
              Whoever's request lands first gets the seat, so automated clients usually win. It is
              here to compare against a fair draw.
            </Banner>
          )}

          <section>
            <h2 className="mb-2 text-lg font-bold">Draw commitment</h2>
            <Card className="space-y-3 p-5 text-sm">
              <p className="text-ink-300">
                This fingerprint was published before entries opened. After the draw, the secret
                behind it is revealed, so the result can't be changed after the fact.
              </p>
              <div>
                <p className="text-ink-500 text-xs">Seed commitment (SHA-256)</p>
                <p className="text-ink-100 font-mono text-xs break-all">{drop.seed_commit}</p>
              </div>
              {drop.entry_set_hash && (
                <div>
                  <p className="text-ink-500 text-xs">Entrant list hash</p>
                  <p className="text-ink-100 font-mono text-xs break-all">{drop.entry_set_hash}</p>
                </div>
              )}
              {drawn ? (
                <Link
                  to={`/drop/${drop.id}/proof`}
                  className="bg-brand-600 hover:bg-brand-500 inline-flex h-10 items-center rounded-xl px-4 font-semibold text-white"
                >
                  Verify this draw yourself →
                </Link>
              ) : (
                <p className="text-ink-500 text-xs">
                  The proof becomes available here once the draw has run.
                </p>
              )}
            </Card>
            <p className="text-ink-600 mt-3 font-mono text-[11px]">Drop {shortId(drop.id, 8, 4)}</p>
          </section>
        </div>

        <aside className="order-1 lg:order-2">
          <Card className="p-5 lg:sticky lg:top-20">
            <TicketPanel drop={drop} journey={journey} presentation={presentation} />
          </Card>
        </aside>
      </div>
    </div>
  );
}

export function DropPage() {
  const { dropId = "" } = useParams();
  if (!isDropId(dropId)) {
    return (
      <div className="mx-auto max-w-xl px-4 py-20 text-center">
        <h1 className="text-3xl font-black tracking-tight">Drop not found</h1>
        <p className="text-ink-400 mt-2">That link doesn't point to a drop.</p>
        <Link to="/" className="text-brand-400 mt-6 inline-block font-medium hover:underline">
          ← All drops
        </Link>
      </div>
    );
  }
  // Keyed so every piece of journey state resets when moving between drops.
  return <DropView key={dropId} dropId={dropId} />;
}
