import { useState, type FormEvent } from "react";

import { api } from "../../api/endpoints";
import { describeError } from "../../api/errors";
import { Banner, Button, Modal, OtpInput } from "../../components/ui";
import { deviceId } from "../../lib/device";
import { useSession } from "../../state/session";

interface OtpRequest {
  requestId: string;
  devOtp: string | null;
}

/** Phone → one-time code → session. One verified phone is one identity, which is one entry. */
export function SignInModal() {
  const open = useSession((s) => s.signInOpen);
  const close = useSession((s) => s.closeSignIn);
  const signedIn = useSession((s) => s.signedIn);

  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [sent, setSent] = useState<OtpRequest | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const reset = () => {
    setOtp("");
    setSent(null);
    setError(null);
  };

  const sendCode = async (event?: FormEvent) => {
    event?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api.requestOtp({ phone, device_id: deviceId() });
      setSent({ requestId: res.request_id, devOtp: res.dev_otp });
      setOtp("");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const verify = async (code: string) => {
    if (!sent || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.verifyOtp({
        request_id: sent.requestId,
        otp: code,
        device_id: deviceId(),
      });
      reset();
      setPhone("");
      signedIn(res.user_public_id);
    } catch (err) {
      setError(err);
      setOtp("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={close} title={sent ? "Enter the code" : "Verify your phone"}>
      {!sent ? (
        <form onSubmit={(e) => void sendCode(e)} className="space-y-4">
          <p className="text-ink-300 text-sm">
            One phone number is one entry. That is what keeps a drop fair: nobody gets extra chances
            by sending more requests.
          </p>
          <label className="block">
            <span className="text-ink-400 text-xs font-medium">Mobile number</span>
            <div className="border-ink-600 bg-ink-800 focus-within:border-brand-500 mt-1 flex h-13 items-center rounded-2xl border px-4">
              <span className="text-ink-400 mr-2 font-medium">+91</span>
              <input
                value={phone}
                onChange={(e) => setPhone(e.target.value.replace(/[^\d\s-]/g, "").slice(0, 12))}
                inputMode="numeric"
                autoComplete="tel-national"
                autoFocus
                placeholder="98765 43210"
                className="placeholder:text-ink-600 tabular w-full bg-transparent text-lg outline-none"
              />
            </div>
          </label>
          {error !== null && <Banner tone="danger">{describeError(error)}</Banner>}
          <Button
            type="submit"
            size="lg"
            className="w-full"
            busy={busy}
            disabled={phone.replace(/\D/g, "").length < 10}
          >
            Send code
          </Button>
        </form>
      ) : (
        <div className="space-y-4">
          <p className="text-ink-300 text-sm">
            We sent a 6-digit code to <span className="text-ink-100 font-medium">+91 {phone}</span>.
          </p>
          <OtpInput value={otp} onChange={setOtp} onComplete={(c) => void verify(c)} autoFocus />
          {sent.devOtp && (
            <Banner tone="brand" title="Test mode">
              No SMS is sent in this environment. Your code is{" "}
              <button
                type="button"
                className="font-mono font-bold underline"
                onClick={() => {
                  setOtp(sent.devOtp ?? "");
                  void verify(sent.devOtp ?? "");
                }}
              >
                {sent.devOtp}
              </button>{" "}
              (tap to fill).
            </Banner>
          )}
          {error !== null && <Banner tone="danger">{describeError(error)}</Banner>}
          <Button
            size="lg"
            className="w-full"
            busy={busy}
            disabled={otp.length < 6}
            onClick={() => verify(otp)}
          >
            Verify
          </Button>
          <div className="flex justify-between text-sm">
            <button type="button" className="text-ink-400 hover:text-ink-100" onClick={reset}>
              Change number
            </button>
            <button
              type="button"
              className="text-brand-400 hover:text-brand-300"
              onClick={() => void sendCode()}
            >
              Resend code
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
