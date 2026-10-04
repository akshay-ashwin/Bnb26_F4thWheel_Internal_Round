import type { Presentation } from "../features/user/catalog";
import { cx } from "../lib/format";

/** Generated event artwork: no image files, so it works offline and for any drop name. */
export function Poster({
  presentation,
  className,
  size = "md",
}: {
  presentation: Presentation;
  className?: string;
  size?: "sm" | "md" | "lg";
}) {
  const [from, to, glow] = presentation.palette;
  return (
    <div
      className={cx("relative isolate overflow-hidden", className)}
      style={{ background: `linear-gradient(150deg, ${from}, ${to})` }}
      aria-hidden="true"
    >
      <div
        className="absolute -top-1/4 -right-1/4 h-3/4 w-3/4 rounded-full opacity-70 blur-3xl"
        style={{ background: glow }}
      />
      <div
        className="absolute -bottom-1/3 -left-1/4 h-2/3 w-2/3 rounded-full opacity-40 blur-3xl"
        style={{ background: from }}
      />
      {/* Concentric rings: a stage seen from above. */}
      <svg
        className="absolute inset-0 h-full w-full opacity-25 mix-blend-overlay"
        viewBox="0 0 200 200"
        preserveAspectRatio="xMidYMid slice"
      >
        {[30, 55, 80, 105, 130, 155].map((r) => (
          <circle key={r} cx="150" cy="40" r={r} fill="none" stroke="#fff" strokeWidth="0.6" />
        ))}
      </svg>
      <div className="absolute inset-0 bg-gradient-to-t from-black/75 via-black/10 to-transparent" />
      <div
        className={cx(
          "absolute inset-x-0 bottom-0",
          size === "sm" && "p-3",
          size === "md" && "p-4",
          size === "lg" && "p-6 sm:p-8",
        )}
      >
        <p
          className={cx(
            "font-semibold tracking-[0.2em] text-white/70 uppercase",
            size === "lg" ? "text-xs" : "text-[10px]",
          )}
        >
          {presentation.category}
        </p>
        <p
          className={cx(
            "leading-[0.95] font-black tracking-tight text-white uppercase",
            size === "sm" && "text-lg",
            size === "md" && "text-2xl",
            size === "lg" && "text-4xl sm:text-6xl",
          )}
        >
          {presentation.title}
        </p>
        <p
          className={cx(
            "mt-1 font-medium text-white/80",
            size === "lg" ? "text-base sm:text-lg" : "text-xs",
          )}
        >
          {presentation.subtitle}
        </p>
      </div>
    </div>
  );
}
