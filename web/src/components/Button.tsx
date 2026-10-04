import type { ButtonHTMLAttributes } from "react";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  busy?: boolean;
  variant?: "primary" | "secondary";
}

/** Disabled while `busy`, so a second tap during a request does nothing. */
export function Button({ busy = false, variant = "primary", disabled, className, ...rest }: Props) {
  const base =
    "w-full rounded-xl px-5 py-4 text-lg font-semibold focus:outline-none focus-visible:ring-4 disabled:cursor-not-allowed disabled:opacity-60";
  const look =
    variant === "primary"
      ? "bg-indigo-600 text-white hover:bg-indigo-700 focus-visible:ring-indigo-300"
      : "bg-white text-slate-800 ring-1 ring-slate-300 hover:bg-slate-50 focus-visible:ring-slate-300";
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={`${base} ${look} ${className ?? ""}`}
    />
  );
}
