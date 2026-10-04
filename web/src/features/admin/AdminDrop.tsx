import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "../../api/endpoints";
import { describeError } from "../../api/errors";
import { createPoller, type Poller } from "../../api/poller";
import type {
  AbuseConfig,
  Drop,
  Integrity,
  Metrics,
  Mode,
  PhaseAction,
  SimLatest,
} from "../../api/types";
import { Badge, Banner, Button, Card, Skeleton, Stat } from "../../components/ui";
import { cx, formatCompact, formatNumber } from "../../lib/format";

interface Snapshot {
  drop: Drop;
  metrics: Metrics;
  integrity: Integrity;
  sim: SimLatest;
}

const ADMIN_POLL_MS = 1000;

/**
 * Request outcomes, in fixed order. Colours are the validated dark categorical set
 * (blue, orange, aqua, yellow); identity never relies on colour alone: the legend names each.
 */
const OUTCOMES = [
  { key: "accepted", label: "Accepted", color: "#3987e5" },
  { key: "rate_limited", label: "Rate limited", color: "#d95926" },
  { key: "token_rejected", label: "Bad token", color: "#199e70" },
  { key: "duplicate", label: "Duplicate", color: "#c98500" },
] as const;

const LAYER_NAMES: Record<string, string> = {
  L1: "Global buckets",
  L2: "Per IP and network",
  L3: "Per session and user",
  L4: "Admission token",
  L5: "Duplicate collapse",
  L6: "OTP abuse controls",
  L7: "Cluster scoring",
  L8: "Step-up at claim",
};

const sum = (xs: readonly number[] | undefined) => (xs ?? []).reduce((a, b) => a + b, 0);

function TrafficChart({ metrics }: { metrics: Metrics }) {
  const data = metrics.rps_series.map((point, i) => {
    const row: Record<string, number> = { t: point.t };
    for (const o of OUTCOMES) row[o.key] = metrics.outcomes_series[o.key]?.[i] ?? 0;
    return row;
  });
  const clock = (t: number) =>
    new Date(t * 1000).toLocaleTimeString("en-IN", { minute: "2-digit", second: "2-digit" });

  return (
    <Card className="p-5">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-bold">Requests per second, by what happened to them</h2>
        <span className="text-ink-400 text-xs">last {metrics.window_s}s</span>
      </div>
      <ul className="text-ink-300 mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {OUTCOMES.map((o) => (
          <li key={o.key} className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: o.color }} />
            {o.label}
            <span className="text-ink-500 tabular">
              {formatCompact(sum(metrics.outcomes_series[o.key]))}
            </span>
          </li>
        ))}
      </ul>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="#262635" vertical={false} />
            <XAxis
              dataKey="t"
              tickFormatter={clock}
              tick={{ fill: "#8b8ba1", fontSize: 11 }}
              stroke="#3a3a4d"
              minTickGap={48}
            />
            <YAxis
              tickFormatter={formatCompact}
              allowDecimals={false}
              tick={{ fill: "#8b8ba1", fontSize: 11 }}
              stroke="transparent"
              width={44}
            />
            <Tooltip
              isAnimationActive={false}
              labelFormatter={(t) => clock(Number(t))}
              formatter={(value, name) => [
                formatNumber(Number(value)),
                OUTCOMES.find((o) => o.key === name)?.label ?? String(name),
              ]}
              contentStyle={{
                background: "#0e0e15",
                border: "1px solid #3a3a4d",
                borderRadius: 12,
                fontSize: 12,
              }}
              labelStyle={{ color: "#b5b5c7" }}
              itemStyle={{ color: "#ececf3" }}
              cursor={{ stroke: "#5e5e75" }}
            />
            {OUTCOMES.map((o) => (
              <Area
                key={o.key}
                type="monotone"
                dataKey={o.key}
                stackId="outcomes"
                stroke={o.color}
                strokeWidth={2}
                fill={o.color}
                fillOpacity={0.35}
                isAnimationActive={false}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

function Meter({ label, value, total }: { label: string; value: number; total: number }) {
  const share = total > 0 ? value / total : 0;
  return (
    <div>
      <div className="mb-1 flex justify-between text-sm">
        <span className="text-ink-300">{label}</span>
        <span className="tabular font-semibold">
          {formatNumber(value)}{" "}
          <span className="text-ink-500 font-normal">({(share * 100).toFixed(1)}%)</span>
        </span>
      </div>
      <div className="bg-ink-700 h-2 overflow-hidden rounded-full">
        <div className="bg-brand-500 h-full rounded-full" style={{ width: `${share * 100}%` }} />
      </div>
    </div>
  );
}

function numberMap(value: unknown): Record<string, number> {
  if (typeof value !== "object" || value === null) return {};
  return Object.fromEntries(
    Object.entries(value).filter((e): e is [string, number] => typeof e[1] === "number"),
  );
}

/** Simulator "ground truth": who really is a bot. Shown to judges; the backend never reads it. */
function GroundTruth({ sim, mode }: { sim: SimLatest; mode: Mode }) {
  const latest = sim.latest;
  if (!latest) {
    return (
      <Card className="p-5">
        <h2 className="font-bold">Attack simulator</h2>
        <p className="text-ink-400 mt-2 text-sm">
          No simulator run is reporting for this drop yet. Start one with{" "}
          <code className="text-ink-300">uv run fd sim</code>.
        </p>
      </Card>
    );
  }
  const identities = numberMap(latest.identities_by_label);
  const requests = numberMap(latest.requests_by_label);
  const fairness = numberMap(latest.fairness_live);
  const totalIds = sum(Object.values(identities));
  const totalReqs = sum(Object.values(requests));
  const ratio = fairness.advantage_ratio;
  const fairRatio = ratio !== undefined && ratio > 0.8 && ratio < 1.25;

  return (
    <Card className="space-y-4 p-5">
      <div className="flex items-center justify-between">
        <h2 className="font-bold">Attack simulator (ground truth)</h2>
        <Badge tone="info">{String(latest.attack_phase ?? "running")}</Badge>
      </div>
      {ratio !== undefined && (
        <div
          className={cx(
            "rounded-2xl border p-4",
            fairRatio ? "border-success/30 bg-success/10" : "border-danger/30 bg-danger/10",
          )}
        >
          <p className="text-ink-300 text-xs tracking-wide uppercase">Bot advantage per identity</p>
          <p className="tabular text-4xl font-black tracking-tight">{ratio.toFixed(2)}×</p>
          <p className="text-ink-300 mt-1 text-sm">
            {fairRatio
              ? "✓ About 1×: a bot identity does no better than a human one."
              : mode === "fifo"
                ? "✕ Speed wins: each bot identity is far more likely to get a seat."
                : "! Outside the expected range."}
          </p>
        </div>
      )}
      <div className="space-y-3">
        <p className="text-ink-400 text-xs font-semibold tracking-wide uppercase">
          Verified identities
        </p>
        {Object.entries(identities).map(([label, n]) => (
          <Meter key={label} label={label} value={n} total={totalIds} />
        ))}
        <p className="text-ink-400 pt-2 text-xs font-semibold tracking-wide uppercase">
          Requests sent
        </p>
        {Object.entries(requests).map(([label, n]) => (
          <Meter key={label} label={label} value={n} total={totalReqs} />
        ))}
      </div>
      <p className="text-ink-500 text-xs">
        Labels come from the simulator and are display-only. No backend decision ever reads them.
      </p>
    </Card>
  );
}

function AbuseLayers() {
  const [config, setConfig] = useState<AbuseConfig | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.admin.getAbuseConfig().then(setConfig, setError);
  }, []);

  const toggle = async (layer: string, on: boolean) => {
    if (!config) return;
    try {
      setConfig(
        await api.admin.putAbuseConfig({
          layers: { ...config.layers, [layer]: on },
          thresholds: config.thresholds,
        }),
      );
      setError(null);
    } catch (err) {
      setError(err);
    }
  };

  return (
    <Card className="p-5">
      <h2 className="font-bold">Abuse controls</h2>
      <p className="text-ink-400 mt-1 text-xs">
        These add friction and evidence. None of them changes anyone's odds in the draw.
      </p>
      {error !== null && (
        <Banner tone="danger" className="mt-3">
          {describeError(error)}
        </Banner>
      )}
      <ul className="mt-3 grid gap-2 sm:grid-cols-2">
        {/* A layer the server has not stored yet counts as on, which is the backend default. */}
        {Object.keys(LAYER_NAMES)
          .map((layer) => [layer, config?.layers[layer] ?? true] as const)
          .map(([layer, on]) => (
            <li key={layer}>
              <label className="border-ink-700 flex cursor-pointer items-center justify-between gap-3 rounded-xl border px-3 py-2 text-sm">
                <span>
                  <span className="text-ink-400 mr-2 font-mono text-xs">{layer}</span>
                  {LAYER_NAMES[layer] ?? layer}
                </span>
                <input
                  type="checkbox"
                  checked={on}
                  disabled={!config}
                  onChange={(e) => void toggle(layer, e.target.checked)}
                  className="accent-brand-500 h-4 w-4"
                />
              </label>
            </li>
          ))}
      </ul>
    </Card>
  );
}

const ACTIONS: { action: PhaseAction; label: string; from: Drop["phase"][] }[] = [
  { action: "open", label: "Open entries", from: ["SCHEDULED"] },
  { action: "close", label: "Close window", from: ["OPEN"] },
  { action: "draw", label: "Run the draw", from: ["CLOSED"] },
];

function download(filename: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "application/x-ndjson" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

/** Live view of one drop for the organiser or a judge. Large type: it is meant for a projector. */
export function AdminDrop() {
  const { dropId = "" } = useParams();
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const poller = useRef<Poller | null>(null);

  useEffect(() => {
    const p = createPoller<Snapshot>({
      fetch: async () => {
        const [drop, metrics, integrity, sim] = await Promise.all([
          api.getDrop(dropId),
          api.admin.metrics(dropId, 60),
          api.admin.integrity(dropId),
          api.admin.sim(dropId),
        ]);
        return { drop, metrics, integrity, sim };
      },
      nextDelayMs: () => ADMIN_POLL_MS,
      onData: (s) => {
        setSnap(s);
        setError(null);
      },
      onError: (err) => {
        setError(err);
        return undefined;
      },
      fallbackMs: ADMIN_POLL_MS,
    });
    poller.current = p;
    p.start();
    return () => p.stop();
  }, [dropId]);

  const act = async (action: PhaseAction, mode?: Mode) => {
    setActionError(null);
    try {
      await api.admin.setPhase(dropId, mode ? { action, mode } : { action });
    } catch (err) {
      setActionError(err);
    }
    await poller.current?.refresh();
  };

  if (!snap) {
    return (
      <div className="mx-auto max-w-7xl space-y-4 px-4 py-8">
        {error !== null && <Banner tone="danger">{describeError(error)}</Banner>}
        <Skeleton className="h-12 w-80" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const { drop, metrics, integrity, sim } = snap;
  const layers = Object.entries(metrics.rate_limited_by_layer)
    .map(([layer, series]) => ({ layer, total: sum(series) }))
    .sort((a, b) => a.layer.localeCompare(b.layer));
  const layerTotal = sum(layers.map((l) => l.total));

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-4 py-8">
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/admin" className="text-ink-400 hover:text-ink-100 text-sm">
          ← Console
        </Link>
        <h1 className="mr-auto text-3xl font-black tracking-tight">{drop.name}</h1>
        <Badge tone={drop.mode === "fair" ? "brand" : "warning"}>
          {drop.mode === "fair" ? "Fair draw" : "First come (FIFO)"}
        </Badge>
        <Badge tone="info">{drop.phase}</Badge>
        <Badge>Run {metrics.run_no}</Badge>
      </div>

      {error !== null && (
        <Banner tone="warning" live>
          Live data paused: {describeError(error)}
        </Banner>
      )}

      <Card className="flex flex-wrap items-center gap-2 p-4">
        {ACTIONS.map(({ action, label, from }) => (
          <Button
            key={action}
            variant={from.includes(drop.phase) ? "primary" : "secondary"}
            disabled={!from.includes(drop.phase)}
            onClick={() => act(action)}
          >
            {label}
          </Button>
        ))}
        <span className="bg-ink-700 mx-2 hidden h-6 w-px sm:block" />
        <Button variant="secondary" onClick={() => act("reset", "fair")}>
          Reset as Fair
        </Button>
        <Button variant="secondary" onClick={() => act("reset", "fifo")}>
          Reset as FIFO
        </Button>
        <span className="ml-auto flex gap-2">
          <Link
            to={`/drop/${drop.id}`}
            className="text-ink-300 hover:bg-ink-800 inline-flex h-10 items-center rounded-xl px-4 text-sm font-semibold"
          >
            User view
          </Link>
          <Button
            variant="ghost"
            onClick={async () => {
              try {
                download(`drop-${drop.id}.ndjson`, await api.admin.exportNdjson(drop.id));
              } catch (err) {
                setActionError(err);
              }
            }}
          >
            Export
          </Button>
        </span>
        {actionError !== null && (
          <Banner tone="danger" className="w-full">
            {describeError(actionError)}
          </Banner>
        )}
      </Card>

      <div
        role="status"
        className={cx(
          "flex flex-wrap items-center gap-x-8 gap-y-2 rounded-3xl border p-6",
          integrity.invariant_ok
            ? "border-success/30 bg-success/10"
            : "border-danger/40 bg-danger/15",
        )}
      >
        <p className="text-2xl font-black tracking-tight sm:text-3xl">
          {integrity.invariant_ok ? "✓ Integrity holds" : "✕ Integrity broken"}
        </p>
        <p className="tabular text-lg">
          <span className="font-bold">{formatNumber(integrity.sold)}</span> sold +{" "}
          <span className="font-bold">{formatNumber(integrity.free)}</span> free ={" "}
          <span className="font-bold">{formatNumber(integrity.seats_total)}</span> seats
        </p>
        <p className="tabular text-lg">
          Oversold: <span className="font-bold">{integrity.oversold}</span> · Double seats:{" "}
          <span className="font-bold">{integrity.duplicate_entries_with_seats}</span>
        </p>
        <p className="text-ink-300 w-full text-xs">
          Counted by SQL from the seat rows on every refresh, never from cached counters.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Stat label="Entries" value={formatNumber(metrics.entries)} hint="one per identity" />
        <Stat
          label="Seats claimed"
          value={`${formatNumber(metrics.allocated)} / ${formatNumber(metrics.capacity)}`}
          hint={`${formatNumber(metrics.remaining)} remaining`}
        />
        <Stat
          label="Requests blocked"
          value={formatCompact(metrics.blocked_requests)}
          hint={`${formatCompact(metrics.duplicate_requests)} duplicates collapsed`}
        />
        <Stat
          label="p95 latency"
          value={`${Math.round(metrics.latency.p95)} ms`}
          hint={`p50 ${Math.round(metrics.latency.p50)} · p99 ${Math.round(metrics.latency.p99)} ms`}
          tone={metrics.latency.p95 > 500 ? "warning" : undefined}
        />
        <Stat label="Active sessions" value={formatNumber(metrics.active_sessions)} />
        <Stat label="Offers made" value={formatNumber(metrics.offers)} />
        <Stat
          label="Flagged entries"
          value={formatNumber(metrics.flagged_entries)}
          hint={`step-ups: ${metrics.step_ups.passed} passed, ${metrics.step_ups.failed} failed of ${metrics.step_ups.issued}`}
        />
        <Stat
          label="Error rate"
          value={`${(metrics.error_rate * 100).toFixed(2)}%`}
          tone={metrics.error_rate > 0.01 ? "danger" : undefined}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_24rem]">
        <div className="space-y-6">
          <TrafficChart metrics={metrics} />
          <Card className="space-y-3 p-5">
            <h2 className="font-bold">Where requests were stopped</h2>
            {layers.length === 0 || layerTotal === 0 ? (
              <p className="text-ink-400 text-sm">Nothing rate limited in this window.</p>
            ) : (
              layers.map((l) => (
                <Meter
                  key={l.layer}
                  label={`${l.layer} · ${LAYER_NAMES[l.layer] ?? ""}`}
                  value={l.total}
                  total={layerTotal}
                />
              ))
            )}
          </Card>
          <AbuseLayers />
        </div>
        <div className="space-y-6">
          <GroundTruth sim={sim} mode={drop.mode} />
          <Card className="space-y-2 p-5 text-sm">
            <h2 className="text-base font-bold">Draw commitment</h2>
            <p className="text-ink-500 text-xs">Seed commitment</p>
            <p className="font-mono text-xs break-all">{drop.seed_commit}</p>
            {drop.seed ? (
              <Link
                to={`/drop/${drop.id}/proof`}
                className="text-brand-400 inline-block pt-1 font-semibold hover:underline"
              >
                Verify the draw in this browser →
              </Link>
            ) : (
              <p className="text-ink-400 text-xs">The seed is revealed after the draw.</p>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
