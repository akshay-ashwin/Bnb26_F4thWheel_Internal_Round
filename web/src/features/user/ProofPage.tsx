import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { api } from "../../api/endpoints";
import { describeError, isApiError } from "../../api/errors";
import type { DrawProof } from "../../api/types";
import { Badge, Banner, Card, Spinner } from "../../components/ui";
import { verifyProof, type ProofCheck } from "../../lib/draw";
import { cx, formatNumber } from "../../lib/format";
import { useSession } from "../../state/session";

type State =
  | { status: "loading" }
  | { status: "error"; error: unknown }
  | { status: "done"; proof: DrawProof; checks: ProofCheck[]; capacity: number };

/**
 * Re-runs the draw in the visitor's own browser from the published proof (docs/contract/draw.md).
 * Nothing is taken on trust: the three checks are computed here with WebCrypto.
 */
export function ProofPage() {
  const { dropId = "" } = useParams();
  const me = useSession((s) => s.userPublicId);
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [proof, drop] = await Promise.all([api.drawProof(dropId), api.getDrop(dropId)]);
        const checks = await verifyProof(proof);
        if (!cancelled) setState({ status: "done", proof, checks, capacity: drop.capacity });
      } catch (error) {
        if (!cancelled) setState({ status: "error", error });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [dropId]);

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-8">
      <Link to={`/drop/${dropId}`} className="text-ink-400 hover:text-ink-100 text-sm">
        ← Back to the drop
      </Link>
      <h1 className="text-3xl font-black tracking-tight">Verify the draw</h1>
      <p className="text-ink-300">
        Your browser just downloaded the published proof and re-ran the whole draw by itself. If the
        organiser had changed the secret, the entrant list or the order, a check below would fail.
      </p>

      {state.status === "loading" && (
        <div className="text-ink-300 flex items-center gap-3">
          <Spinner className="text-brand-400 h-5 w-5" /> Re-running the draw…
        </div>
      )}

      {state.status === "error" && (
        <Banner tone={isApiError(state.error, "INVALID_TRANSITION") ? "info" : "danger"}>
          {isApiError(state.error, "INVALID_TRANSITION")
            ? "The draw hasn't run yet. The proof is published the moment it does."
            : describeError(state.error)}
        </Banner>
      )}

      {state.status === "done" && (
        <>
          <Banner
            tone={state.checks.every((c) => c.ok) ? "success" : "danger"}
            title={
              state.checks.every((c) => c.ok)
                ? "All checks passed: this draw is exactly what was promised"
                : "A check failed: this draw does not match its commitment"
            }
          />
          <ol className="space-y-3">
            {state.checks.map((check, i) => (
              <li key={check.id}>
                <Card className="flex gap-4 p-4">
                  <span
                    className={cx(
                      "flex h-8 w-8 shrink-0 items-center justify-center rounded-full font-bold",
                      check.ok ? "bg-success/15 text-success" : "bg-danger/15 text-danger",
                    )}
                  >
                    {check.ok ? "✓" : "✕"}
                  </span>
                  <div className="min-w-0">
                    <p className="font-semibold">
                      {i + 1}. {check.label}
                    </p>
                    <p className="text-ink-400 mt-1 font-mono text-xs break-all">{check.detail}</p>
                  </div>
                </Card>
              </li>
            ))}
          </ol>

          <Card className="space-y-3 p-5 text-sm">
            <h2 className="text-base font-bold">The published values</h2>
            {(
              [
                ["Seed commitment (published before entries opened)", state.proof.seed_commit],
                ["Seed (revealed after the draw)", state.proof.seed],
                ["Entrant list hash", state.proof.entry_set_hash],
                ["Rule", state.proof.algorithm],
              ] as const
            ).map(([label, value]) => (
              <div key={label}>
                <p className="text-ink-500 text-xs">{label}</p>
                <p className="font-mono text-xs break-all">{value}</p>
              </div>
            ))}
          </Card>

          <Card className="p-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-bold">Draw order</h2>
              <span className="text-ink-400 text-xs">
                {formatNumber(state.proof.ranked_public_ids?.length ?? 0)} entrants ·{" "}
                {formatNumber(state.capacity)} seats
              </span>
            </div>
            <RankList
              ranked={state.proof.ranked_public_ids ?? []}
              capacity={state.capacity}
              me={me}
            />
          </Card>
        </>
      )}
    </div>
  );
}

function RankList({
  ranked,
  capacity,
  me,
}: {
  ranked: readonly string[];
  capacity: number;
  me: string | null;
}) {
  const myIndex = me ? ranked.indexOf(me) : -1;
  const rows = ranked.slice(0, 8).map((id, i) => ({ id, rank: i + 1 }));
  const mine = myIndex >= 8 ? ranked[myIndex] : undefined;
  if (mine) rows.push({ id: mine, rank: myIndex + 1 });
  return (
    <ul className="divide-ink-700 divide-y text-sm">
      {rows.map(({ id, rank }) => (
        <li key={id} className="flex items-center gap-3 py-2">
          <span className="text-ink-400 tabular w-14">#{formatNumber(rank)}</span>
          <span className="min-w-0 flex-1 truncate font-mono text-xs">{id}</span>
          {id === me && <Badge tone="brand">You</Badge>}
          <Badge tone={rank <= capacity ? "success" : "neutral"}>
            {rank <= capacity ? "Seat offered" : "Waitlist"}
          </Badge>
        </li>
      ))}
      {ranked.length > rows.length && (
        <li className="text-ink-500 py-2 text-xs">
          …and {formatNumber(ranked.length - rows.length)} more, all checked above.
        </li>
      )}
    </ul>
  );
}
