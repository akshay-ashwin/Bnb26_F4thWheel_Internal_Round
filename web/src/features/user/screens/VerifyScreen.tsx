import { useState, type FormEvent } from "react";

import { ApiError, api } from "../../../api/client";
import { withRetry } from "../../../api/retry";
import { Banner } from "../../../components/Banner";
import { Button } from "../../../components/Button";
import { formatRemaining } from "../../../components/Countdown";
import { OtpInput } from "../../../components/OtpInput";
import { deviceId } from "../../../lib/device";
import { copy } from "../copy";

interface Sent {
  requestId: string;
  devOtp: string | null;
}

export function VerifyScreen({ onVerified }: { onVerified: () => void }) {
  const [phone, setPhone] = useState("");
  const [sent, setSent] = useState<Sent | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const t = copy.verify;

  function explain(err: unknown): string {
    if (!(err instanceof ApiError)) return copy.overlays.generic;
    switch (err.code) {
      case "INVALID_PHONE":
      case "VALIDATION_ERROR":
        return t.invalidPhone;
      case "OTP_THROTTLED":
        return t.throttled(formatRemaining(err.retryAfterMs ?? 60_000));
      case "OTP_INVALID":
        return t.otpInvalid;
      case "OTP_EXPIRED":
        setSent(null);
        return t.otpExpired;
      default:
        return copy.overlays.generic;
    }
  }

  async function send(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      // The server returns the same request for the same phone within 30 s, so a retry is safe.
      const out = await withRetry(() => api.requestOtp(phone, deviceId()));
      setSent({ requestId: out.request_id, devOtp: out.dev_otp ?? null });
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  async function verify(code: string) {
    if (!sent) return;
    setBusy(true);
    setError(null);
    try {
      await withRetry(() => api.verifyOtp(sent.requestId, code, deviceId()));
      onVerified();
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-5" data-state="VERIFY">
      <h1 className="text-2xl font-semibold">{t.title}</h1>
      <p className="text-slate-600">{t.body}</p>
      {error && (
        <Banner tone="danger" testId="verify-error">
          {error}
        </Banner>
      )}
      {!sent ? (
        <form onSubmit={send} className="space-y-4">
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-700">{t.phoneLabel}</span>
            <input
              type="tel"
              inputMode="tel"
              autoComplete="tel"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder={t.phoneHint}
              className="w-full rounded-lg border border-slate-300 px-4 py-3 text-lg focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200"
            />
          </label>
          <Button type="submit" busy={busy} disabled={phone.trim() === ""}>
            {t.send}
          </Button>
        </form>
      ) : (
        <div className="space-y-4">
          <p className="text-sm text-slate-600">{t.codeSentTo(phone)}</p>
          {sent.devOtp && (
            <p
              className="rounded-md border border-dashed border-amber-400 bg-amber-50 px-3 py-2 font-mono text-sm text-amber-900"
              data-testid="dev-otp"
            >
              {t.devHint(sent.devOtp)}
            </p>
          )}
          <OtpInput label={t.codeLabel} disabled={busy} onComplete={verify} />
          {busy && <p className="text-sm text-slate-500">{t.verifying}</p>}
          <Button variant="secondary" onClick={() => setSent(null)} disabled={busy}>
            {t.changeNumber}
          </Button>
        </div>
      )}
    </section>
  );
}
