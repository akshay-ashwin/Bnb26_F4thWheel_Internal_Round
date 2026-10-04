import { useEffect, useState } from "react";

import { serverNow } from "../api/clock";

/** Re-renders every `intervalMs` with the server-clock time. */
export function useServerNow(intervalMs = 250): number {
  const [now, setNow] = useState(serverNow);
  useEffect(() => {
    const id = setInterval(() => setNow(serverNow()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function formatRemaining(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

interface Props {
  until: string;
  label: string;
  /** Below this many ms the countdown turns urgent (colour + assertive announcement). */
  urgentBelowMs?: number;
}

export function Countdown({ until, label, urgentBelowMs }: Props) {
  const now = useServerNow();
  const left = Date.parse(until) - now;
  const urgent = urgentBelowMs !== undefined && left < urgentBelowMs;
  return (
    <p
      className={`text-base ${urgent ? "font-semibold text-red-700" : "text-slate-600"}`}
      aria-live={urgent ? "assertive" : "off"}
      data-testid="countdown"
    >
      {label} <span className="font-mono tabular-nums">{formatRemaining(left)}</span>
    </p>
  );
}
