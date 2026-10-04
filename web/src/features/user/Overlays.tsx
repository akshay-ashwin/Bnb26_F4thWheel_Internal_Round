import { useEffect, useState } from "react";

import { connectivity, rateLimitedUntil } from "../../api/connectivity";
import { Banner, Spinner } from "../../components/Banner";
import { useStore } from "../../lib/store";
import { copy } from "./copy";
import type { ActionError } from "./dropController";

/** Overlays sit above the state screen and never replace it. */
export function Overlays({ actionError }: { actionError: ActionError | null }) {
  const net = useStore(connectivity);
  const limitedUntil = useStore(rateLimitedUntil);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (limitedUntil <= now) return;
    const id = setTimeout(() => setNow(Date.now()), limitedUntil - now);
    return () => clearTimeout(id);
  }, [limitedUntil, now]);

  // Codes the state screen already explains are not repeated as an error.
  const shown =
    actionError && !["OTP_INVALID", "RATE_LIMITED"].includes(actionError.code) ? actionError : null;

  return (
    <div className="space-y-2" aria-live="polite">
      {net === "reconnecting" && (
        <Banner tone="warning" testId="reconnecting">
          <Spinner />
          {copy.overlays.reconnecting}
        </Banner>
      )}
      {limitedUntil > now && <Banner tone="info">{copy.overlays.rateLimited}</Banner>}
      {shown && (
        <Banner tone="danger" testId="action-error">
          {copy.overlays.generic}
          {shown.requestId && (
            <details className="mt-1 text-xs">
              <summary>{copy.overlays.details}</summary>
              {copy.overlays.requestId(shown.requestId)}
            </details>
          )}
        </Banner>
      )}
    </div>
  );
}
