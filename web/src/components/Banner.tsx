import type { ReactNode } from "react";

const TONES = {
  info: "bg-sky-50 text-sky-900 ring-sky-200",
  warning: "bg-amber-50 text-amber-900 ring-amber-200",
  danger: "bg-red-50 text-red-900 ring-red-200",
} as const;

export function Banner({
  tone = "info",
  children,
  testId,
}: {
  tone?: keyof typeof TONES;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <div
      role="status"
      data-testid={testId}
      className={`rounded-lg px-4 py-3 text-sm ring-1 ${TONES[tone]}`}
    >
      {children}
    </div>
  );
}

export function Spinner() {
  return (
    <span
      aria-hidden="true"
      className="mr-2 inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent align-[-2px]"
    />
  );
}
