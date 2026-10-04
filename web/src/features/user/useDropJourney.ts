import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../../api/endpoints";
import { ApiError, NetworkError, isApiError } from "../../api/errors";
import {
  clearIdempotencyKey,
  idempotencyKey,
  isPending,
  markPending,
  type Action,
} from "../../api/idempotency";
import { createPoller, type Poller } from "../../api/poller";
import { withRetry, type RetryOptions } from "../../api/retry";
import type { Drop, Me } from "../../api/types";
import { useSession } from "../../state/session";

export interface Journey {
  drop: Drop | null;
  dropError: unknown;
  me: Me | null;
  /** null until the first `/me` answer tells us whether a session exists. */
  authed: boolean | null;
  busy: Action | null;
  /** Calm progress text while a write is being retried ("Reconnecting…"). */
  notice: string | null;
  actionError: unknown;
  enter: () => Promise<void>;
  claim: () => Promise<void>;
  stepUp: (otp: string) => Promise<void>;
  dismissError: () => void;
}

const DROP_POLL_MS = 3000;

/** A failure that says nothing about the action itself: keep the key so the retry is the same action. */
function isTransient(err: unknown): boolean {
  if (err instanceof NetworkError) return true;
  return err instanceof ApiError && (err.status >= 500 || err.status === 429);
}

/**
 * Everything one drop page needs: the public drop, the user's `/me`, and the three writes.
 * All writes go through the retry engine with a stable idempotency key; all state shown is the
 * last thing the server said (no optimistic seat, ever).
 */
export function useDropJourney(dropId: string): Journey {
  const sessionVersion = useSession((s) => s.version);
  const [drop, setDrop] = useState<Drop | null>(null);
  const [dropError, setDropError] = useState<unknown>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [busy, setBusy] = useState<Action | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);

  const meRef = useRef<Me | null>(null);
  const busyRef = useRef(false);
  const pollers = useRef<{ drop: Poller; me: Poller } | null>(null);
  const resumeClaim = useRef<() => void>(() => undefined);

  const applyMe = useCallback((next: Me) => {
    meRef.current = next;
    setMe(next);
    setAuthed(true);
  }, []);

  useEffect(() => {
    let lastPhase: string | null = null;
    const mePoller = createPoller<Me>({
      fetch: () => api.getMe(dropId),
      nextDelayMs: (m) => m.poll_after_ms,
      onData: (m) => {
        applyMe(m);
        if (!isPending(dropId, "claim")) return;
        // A claim was interrupted (refresh, crash, lost response). Finish it with the same key.
        if (m.allocation) clearIdempotencyKey(dropId, "claim");
        else if (m.entry?.admission_token) resumeClaim.current();
        else if (m.entry && m.entry.status !== "STEP_UP_REQUIRED") {
          clearIdempotencyKey(dropId, "claim");
        }
      },
      onError: (err) => {
        if (isApiError(err, "UNAUTHENTICATED")) {
          meRef.current = null;
          setMe(null);
          setAuthed(false);
          useSession.getState().signedOut();
          return false;
        }
        return !isApiError(err, "NOT_FOUND");
      },
    });
    const dropPoller = createPoller<Drop>({
      fetch: () => api.getDrop(dropId),
      nextDelayMs: (d) => (d.phase === "DONE" ? 15_000 : DROP_POLL_MS),
      onData: (d) => {
        setDrop(d);
        setDropError(null);
        // A phase change is the moment `/me` changes too: don't wait for its next poll.
        if (lastPhase !== null && lastPhase !== d.phase) void mePoller.refresh();
        lastPhase = d.phase;
      },
      onError: (err) => {
        setDropError(err);
        return !isApiError(err, "NOT_FOUND");
      },
      fallbackMs: DROP_POLL_MS,
    });
    pollers.current = { drop: dropPoller, me: mePoller };
    dropPoller.start();
    mePoller.start();
    return () => {
      dropPoller.stop();
      mePoller.stop();
      pollers.current = null;
    };
  }, [dropId, sessionVersion, applyMe]);

  const run = useCallback(
    async (action: Action, work: (retry: RetryOptions) => Promise<void>) => {
      if (busyRef.current) return;
      busyRef.current = true;
      setBusy(action);
      setActionError(null);
      const retry: RetryOptions = {
        onRetry: ({ reason }) => {
          if (reason === "rate_limited") setNotice("Slow down a moment…");
          else if (reason !== "token") setNotice("Reconnecting… your place is safe");
        },
      };
      try {
        await work(retry);
        clearIdempotencyKey(dropId, action);
      } catch (err) {
        if (isApiError(err, "UNAUTHENTICATED")) {
          // Keep the key and the pending marker: the claim continues after signing in again.
          setAuthed(false);
          useSession.getState().signedOut();
          useSession.getState().openSignIn();
        } else if (isApiError(err, "STEP_UP_REQUIRED")) {
          // Not a failure: `/me` now shows the step-up screen; the claim key stays for afterwards.
        } else {
          if (!isTransient(err)) clearIdempotencyKey(dropId, action);
          setActionError(err);
        }
      } finally {
        busyRef.current = false;
        setBusy(null);
        setNotice(null);
        void pollers.current?.drop.refresh();
        await pollers.current?.me.refresh();
      }
    },
    [dropId],
  );

  const enter = useCallback(
    () =>
      run("enter", async (retry) => {
        await withRetry(() => api.createEntry(dropId, idempotencyKey(dropId, "enter")), retry);
      }),
    [dropId, run],
  );

  const claim = useCallback(
    () =>
      run("claim", async (retry) => {
        markPending(dropId, "claim");
        // First-come drops have no separate entry step: entering and claiming are one tap.
        if (!meRef.current?.entry) {
          await withRetry(() => api.createEntry(dropId, idempotencyKey(dropId, "enter")), retry);
          clearIdempotencyKey(dropId, "enter");
        }
        let token = meRef.current?.entry?.admission_token ?? null;
        const refreshToken = async () => {
          const fresh = await api.getMe(dropId);
          applyMe(fresh);
          token = fresh.entry?.admission_token ?? null;
        };
        if (!token) await refreshToken();
        const key = idempotencyKey(dropId, "claim");
        await withRetry(
          () => {
            if (!token) {
              throw new ApiError({
                code: "NOT_OFFERED",
                status: 403,
                message: "No offer to claim",
              });
            }
            return api.claim(dropId, { admission_token: token }, key);
          },
          { ...retry, refreshToken },
        );
      }),
    [dropId, run, applyMe],
  );

  const stepUp = useCallback(
    (otp: string) =>
      run("step-up", async (retry) => {
        await withRetry(
          () => api.stepUp(dropId, { otp }, idempotencyKey(dropId, "step-up")),
          retry,
        );
      }),
    [dropId, run],
  );

  useEffect(() => {
    // At most one automatic resume per page load: a retry loop would look like "faster wins".
    let resumed = false;
    resumeClaim.current = () => {
      if (resumed || busyRef.current) return;
      resumed = true;
      void claim();
    };
  }, [claim]);

  const dismissError = useCallback(() => setActionError(null), []);

  return {
    drop,
    dropError,
    me,
    authed,
    busy,
    notice,
    actionError,
    enter,
    claim,
    stepUp,
    dismissError,
  };
}
