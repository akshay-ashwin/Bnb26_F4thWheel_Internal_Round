import { useEffect, useState } from "react";

import { serverNow } from "../api/clock";
import { formatDuration } from "../lib/format";

/** Milliseconds left until `target`, measured on the server's clock, re-rendered a few times a second. */
export function useRemaining(target: string | number | null | undefined): number | null {
  const targetMs = typeof target === "string" ? Date.parse(target) : (target ?? null);
  const [, setTick] = useState(0);

  useEffect(() => {
    if (targetMs === null) return;
    const id = setInterval(() => setTick((t) => t + 1), 250);
    return () => clearInterval(id);
  }, [targetMs]);

  if (targetMs === null || Number.isNaN(targetMs)) return null;
  return Math.max(0, targetMs - serverNow());
}

/**
 * Countdown driven by the server clock (`serverNow()`), never the device clock, so a phone with
 * the wrong time still sees the same deadline as everyone else.
 */
export function Countdown({
  to,
  className,
  doneLabel = "0:00",
}: {
  to: string | number | null | undefined;
  className?: string;
  doneLabel?: string;
}) {
  const remaining = useRemaining(to);
  if (remaining === null) return null;
  return (
    <span className={className} role="timer">
      <span className="tabular">{remaining > 0 ? formatDuration(remaining) : doneLabel}</span>
    </span>
  );
}
