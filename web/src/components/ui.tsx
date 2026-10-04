import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";

import { cx } from "../lib/format";

export function Spinner({ className }: { className?: string }) {
  return (
    <svg
      className={cx("h-4 w-4 animate-spin", className)}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onClick"> {
  variant?: ButtonVariant;
  size?: "sm" | "md" | "lg";
  busy?: boolean;
  busyLabel?: string;
  /** If this returns a promise, the button stays disabled until it settles: one tap, one action. */
  onClick?: () => void | Promise<void>;
}

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-brand-600 text-white hover:bg-brand-500 active:bg-brand-700 shadow-lg shadow-brand-600/25",
  secondary: "bg-ink-700 text-ink-100 hover:bg-ink-600 border border-ink-600",
  ghost: "text-ink-300 hover:bg-ink-800 hover:text-ink-100",
  danger: "bg-danger/15 text-danger hover:bg-danger/25 border border-danger/30",
};

const SIZES = {
  sm: "h-8 px-3 text-xs rounded-lg",
  md: "h-10 px-4 text-sm rounded-xl",
  lg: "h-13 px-6 text-base rounded-2xl",
};

export function Button({
  variant = "primary",
  size = "md",
  busy = false,
  busyLabel,
  onClick,
  disabled,
  className,
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  const [pending, setPending] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const working = busy || pending;
  const handleClick = useCallback(() => {
    if (!onClick || working) return;
    const result = onClick();
    if (result instanceof Promise) {
      setPending(true);
      void result.finally(() => {
        if (mounted.current) setPending(false);
      });
    }
  }, [onClick, working]);

  return (
    <button
      {...rest}
      type={type}
      disabled={disabled || working}
      aria-busy={working}
      onClick={onClick ? handleClick : undefined}
      className={cx(
        "inline-flex items-center justify-center gap-2 font-semibold transition-colors select-none",
        "disabled:cursor-not-allowed disabled:opacity-60",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
    >
      {working && <Spinner />}
      {working && busyLabel ? busyLabel : children}
    </button>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cx("border-ink-700 bg-ink-850 rounded-3xl border", className)}>{children}</div>
  );
}

export type Tone = "neutral" | "brand" | "success" | "warning" | "danger" | "info";

const TONES: Record<Tone, string> = {
  neutral: "bg-ink-700 text-ink-300 border-ink-600",
  brand: "bg-brand-500/15 text-brand-300 border-brand-500/30",
  success: "bg-success/12 text-success border-success/30",
  warning: "bg-warning/12 text-warning border-warning/30",
  danger: "bg-danger/12 text-danger border-danger/30",
  info: "bg-info/12 text-info border-info/30",
};

export function Badge({
  tone = "neutral",
  dot = false,
  className,
  children,
}: {
  tone?: Tone;
  dot?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold",
        TONES[tone],
        className,
      )}
    >
      {dot && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
      {children}
    </span>
  );
}

const BANNER_ICON: Record<Tone, string> = {
  neutral: "i",
  brand: "✦",
  success: "✓",
  warning: "!",
  danger: "✕",
  info: "i",
};

/** Status message. `live` announces changes to screen readers; the icon means colour is not the only signal. */
export function Banner({
  tone = "info",
  title,
  children,
  action,
  live = false,
  className,
}: {
  tone?: Tone;
  title?: string;
  children?: ReactNode;
  action?: ReactNode;
  live?: boolean;
  className?: string;
}) {
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      aria-live={live ? "polite" : undefined}
      className={cx(
        "flex items-start gap-3 rounded-2xl border p-3 text-sm",
        TONES[tone],
        className,
      )}
    >
      <span
        aria-hidden="true"
        className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-current text-[11px] font-bold"
      >
        {BANNER_ICON[tone]}
      </span>
      <div className="min-w-0 flex-1">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className={cx("text-ink-100/85", title && "mt-0.5")}>{children}</div>}
      </div>
      {action}
    </div>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone,
  className,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "success" | "danger" | "warning";
  className?: string;
}) {
  return (
    <div className={cx("border-ink-700 bg-ink-850 rounded-2xl border p-4", className)}>
      <p className="text-ink-400 text-xs font-medium tracking-wide uppercase">{label}</p>
      <p
        className={cx(
          "tabular mt-1 text-3xl font-bold tracking-tight",
          tone === "success" && "text-success",
          tone === "danger" && "text-danger",
          tone === "warning" && "text-warning",
        )}
      >
        {value}
      </p>
      {hint && <p className="text-ink-400 mt-1 text-xs">{hint}</p>}
    </div>
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement;
    panel.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      if (previous instanceof HTMLElement) previous.focus();
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="animate-rise border-ink-700 bg-ink-900 relative w-full max-w-md rounded-t-3xl border p-6 outline-none sm:rounded-3xl"
      >
        <div className="mb-4 flex items-center justify-between gap-4">
          <h2 id={titleId} className="text-xl font-bold tracking-tight">
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="text-ink-400 hover:bg-ink-800 hover:text-ink-100 flex h-8 w-8 items-center justify-center rounded-full"
          >
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** Six-digit code field. One input (works with paste and SMS autofill), styled as boxes. */
export function OtpInput({
  value,
  onChange,
  onComplete,
  disabled,
  autoFocus,
  label = "One-time code",
}: {
  value: string;
  onChange: (value: string) => void;
  onComplete?: (value: string) => void;
  disabled?: boolean;
  autoFocus?: boolean;
  label?: string;
}) {
  return (
    <input
      aria-label={label}
      inputMode="numeric"
      autoComplete="one-time-code"
      autoFocus={autoFocus}
      disabled={disabled}
      maxLength={6}
      value={value}
      placeholder="••••••"
      onChange={(event) => {
        const next = event.target.value.replace(/\D/g, "").slice(0, 6);
        onChange(next);
        if (next.length === 6) onComplete?.(next);
      }}
      className="border-ink-600 bg-ink-800 placeholder:text-ink-600 focus:border-brand-500 tabular h-14 w-full rounded-2xl border text-center font-mono text-2xl tracking-[0.5em] outline-none"
    />
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("bg-ink-800 animate-pulse rounded-2xl", className)} />;
}
