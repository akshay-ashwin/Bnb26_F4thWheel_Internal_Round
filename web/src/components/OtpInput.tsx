import { useRef, useState, type ClipboardEvent, type KeyboardEvent } from "react";

interface Props {
  label: string;
  disabled?: boolean;
  /** Called once all 6 digits are present (typed or pasted). */
  onComplete: (code: string) => void;
}

const LENGTH = 6;

/** Six single-digit boxes; paste-friendly; submits automatically on the 6th digit. */
export function OtpInput({ label, disabled = false, onComplete }: Props) {
  const [digits, setDigits] = useState<string[]>(() => Array(LENGTH).fill(""));
  const refs = useRef<(HTMLInputElement | null)[]>([]);

  function commit(next: string[], focus: number) {
    setDigits(next);
    refs.current[Math.min(focus, LENGTH - 1)]?.focus();
    if (next.every((d) => d !== "")) {
      onComplete(next.join(""));
      setDigits(Array(LENGTH).fill(""));
      refs.current[0]?.focus();
    }
  }

  function fill(from: number, text: string) {
    const clean = text.replace(/\D/g, "").slice(0, LENGTH - from);
    if (!clean) return;
    const next = [...digits];
    [...clean].forEach((d, i) => (next[from + i] = d));
    commit(next, from + clean.length);
  }

  function onKeyDown(i: number, e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Backspace" && !digits[i] && i > 0) {
      const next = [...digits];
      next[i - 1] = "";
      setDigits(next);
      refs.current[i - 1]?.focus();
    }
  }

  function onPaste(i: number, e: ClipboardEvent<HTMLInputElement>) {
    e.preventDefault();
    fill(i, e.clipboardData.getData("text"));
  }

  return (
    <fieldset disabled={disabled}>
      <legend className="mb-2 text-sm font-medium text-slate-700">{label}</legend>
      <div className="flex gap-2">
        {digits.map((d, i) => (
          <input
            key={i}
            ref={(el) => {
              refs.current[i] = el;
            }}
            aria-label={`Digit ${i + 1}`}
            inputMode="numeric"
            autoComplete={i === 0 ? "one-time-code" : "off"}
            maxLength={LENGTH}
            value={d}
            onChange={(e) => {
              const value = e.target.value;
              if (value === "") {
                const next = [...digits];
                next[i] = "";
                setDigits(next);
                return;
              }
              // Typing into a filled box: keep only the new character(s).
              fill(i, d && value.length > 1 && value.startsWith(d) ? value.slice(d.length) : value);
            }}
            onKeyDown={(e) => onKeyDown(i, e)}
            onPaste={(e) => onPaste(i, e)}
            className="h-12 w-full min-w-0 rounded-lg border border-slate-300 text-center font-mono text-xl focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200"
          />
        ))}
      </div>
    </fieldset>
  );
}
