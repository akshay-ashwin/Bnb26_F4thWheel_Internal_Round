import { useEffect, useState } from "react";
import { Link } from "react-router";

import { api } from "../../api/endpoints";
import { isApiError } from "../../api/errors";
import type { Drop, Me } from "../../api/types";
import { Badge, Button, Card, Skeleton } from "../../components/ui";
import { useSession } from "../../state/session";
import { presentationOf } from "./catalog";
import { journeyView } from "./journeyView";
import { Ticket } from "./TicketPanel";
import { useDropList } from "./useDropList";

type Row = { drop: Drop; me: Me };

const ACTIVE_LABEL: Record<string, string> = {
  entered: "Entered, waiting for the draw",
  drawing: "Draw in progress",
  offered: "Seat offered: claim it now",
  step_up: "Seat offered: one more check needed",
  waitlisted: "On the waitlist",
  fifo_grab: "Entered",
};

/** Seats and live entries across every drop this browser knows about. Read-only: `/me` per drop. */
export function TicketsPage() {
  const { drops, loading } = useDropList();
  const version = useSession((s) => s.version);
  const openSignIn = useSession((s) => s.openSignIn);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [signedOut, setSignedOut] = useState(false);
  const ids = drops.map((d) => `${d.id}:${d.phase}`).join(",");

  useEffect(() => {
    if (loading) return;
    let cancelled = false;
    void (async () => {
      const results = await Promise.allSettled(drops.map((d) => api.getMe(d.id)));
      if (cancelled) return;
      setSignedOut(
        results.some((r) => r.status === "rejected" && isApiError(r.reason, "UNAUTHENTICATED")),
      );
      setRows(
        results.flatMap((r, i) => {
          const drop = drops[i];
          return r.status === "fulfilled" && drop && r.value.entry ? [{ drop, me: r.value }] : [];
        }),
      );
    })();
    return () => {
      cancelled = true;
    };
    // `ids` stands for `drops`: refetch when the set of drops or a phase changes, not every poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids, loading, version]);

  if (rows === null) {
    return (
      <div className="mx-auto max-w-3xl space-y-4 px-4 py-8">
        <Skeleton className="h-10 w-48" />
        <Skeleton className="h-56 w-full" />
      </div>
    );
  }

  const tickets = rows.filter((r) => r.me.allocation);
  const active = rows.filter((r) => !r.me.allocation);

  return (
    <div className="mx-auto max-w-3xl space-y-8 px-4 py-8">
      <h1 className="text-3xl font-black tracking-tight">My tickets</h1>

      {signedOut && (
        <Card className="space-y-3 p-6 text-center">
          <p className="text-ink-300">Verify your phone to see your seats and entries.</p>
          <Button onClick={openSignIn}>Verify phone</Button>
        </Card>
      )}

      {!signedOut && rows.length === 0 && (
        <Card className="space-y-3 p-8 text-center">
          <p className="text-xl font-bold">Nothing here yet</p>
          <p className="text-ink-400 text-sm">Enter a drop and your seat will show up here.</p>
          <Link to="/" className="text-brand-400 inline-block font-medium hover:underline">
            Browse drops →
          </Link>
        </Card>
      )}

      {tickets.length > 0 && (
        <div className="grid gap-5 sm:grid-cols-2">
          {tickets.map(({ drop, me }) =>
            me.allocation ? (
              <Ticket
                key={drop.id}
                presentation={presentationOf(drop.name)}
                allocation={me.allocation}
                dropId={drop.id}
              />
            ) : null,
          )}
        </div>
      )}

      {active.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-lg font-bold">Entries</h2>
          {active.map(({ drop, me }) => {
            const view = journeyView(drop, me, true);
            const label = ACTIVE_LABEL[view.kind];
            return (
              <Link
                key={drop.id}
                to={`/drop/${drop.id}`}
                className="border-ink-700 bg-ink-850 hover:border-ink-500 flex items-center justify-between gap-4 rounded-2xl border p-4"
              >
                <div className="min-w-0">
                  <p className="truncate font-semibold">{drop.name}</p>
                  <p className="text-ink-400 text-sm">{presentationOf(drop.name).when}</p>
                </div>
                <Badge tone={label ? "brand" : "neutral"}>{label ?? "No seat"}</Badge>
              </Link>
            );
          })}
        </section>
      )}
    </div>
  );
}
