// Owns everything the user journey knows about one drop in one tab: the polled /drops/{id} and
// /me snapshots, the local claim result, and the three write actions. Screens never fetch.
import { ApiError, NetworkError, api } from "../../api/client";
import { connectivity } from "../../api/connectivity";
import {
  actionKey,
  finishAction,
  isPending,
  markPending,
  type Action,
} from "../../api/idempotency";
import { withRetry } from "../../api/retry";
import type { Allocation, Drop, Me } from "../../api/types";
import { createStore, type Store } from "../../lib/store";

export interface ActionError {
  action: Action;
  code: string;
  requestId: string | null;
}

export interface DropView {
  drop: Drop | null;
  dropMissing: boolean;
  authenticated: boolean | null;
  me: Me | null;
  localAllocation: Allocation | null;
  busy: Action | null;
  actionError: ActionError | null;
}

const INITIAL: DropView = {
  drop: null,
  dropMissing: false,
  authenticated: null,
  me: null,
  localAllocation: null,
  busy: null,
  actionError: null,
};

const FALLBACK_POLL_MS = 3000;
const SIGNED_OUT_POLL_MS = 5000;
const MAX_ERROR_POLL_MS = 5000;

/** Business answers that end an action: forget its key and show what /me says. */
const TERMINAL = new Set([
  "NOT_OFFERED",
  "OFFER_EXPIRED",
  "SOLD_OUT",
  "STEP_UP_REQUIRED",
  "WINDOW_CLOSED",
  "WINDOW_NOT_OPEN",
  "TOKEN_INVALID",
]);

export class DropController {
  readonly view: Store<DropView>;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = true;
  private lastMeTime = 0;
  private errorStreak = 0;
  private inflight: Promise<void> | null = null;

  constructor(readonly dropId: string) {
    this.view = createStore<DropView>(INITIAL);
  }

  private patch(p: Partial<DropView>): void {
    this.view.set({ ...this.view.get(), ...p });
  }

  // ---------------------------------------------------------------- polling

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    document.addEventListener("visibilitychange", this.onWake);
    window.addEventListener("online", this.onWake);
    void this.refresh();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    document.removeEventListener("visibilitychange", this.onWake);
    window.removeEventListener("online", this.onWake);
  }

  private onWake = (): void => {
    if (!document.hidden) void this.refresh();
  };

  /** Fetch /drops/{id} and /me now (deduplicated) and schedule the next poll. */
  refresh(): Promise<void> {
    this.inflight ??= this.poll().finally(() => {
      this.inflight = null;
    });
    return this.inflight;
  }

  private schedule(ms: number): void {
    if (this.timer) clearTimeout(this.timer);
    if (this.stopped) return;
    this.timer = setTimeout(() => {
      if (document.hidden) return; // resumes on visibilitychange
      void this.refresh();
    }, ms);
  }

  private async poll(): Promise<void> {
    const [dropRes, meRes] = await Promise.allSettled([
      api.drop(this.dropId),
      this.view.get().authenticated === false ? Promise.resolve(null) : api.me(this.dropId),
    ]);
    let failed = false;

    if (dropRes.status === "fulfilled") {
      this.patch({ drop: dropRes.value, dropMissing: false });
    } else if (dropRes.reason instanceof ApiError && dropRes.reason.code === "NOT_FOUND") {
      this.patch({ dropMissing: true });
    } else {
      failed = true;
    }

    if (meRes.status === "fulfilled") {
      if (meRes.value) this.applyMe(meRes.value);
    } else if (meRes.reason instanceof ApiError && meRes.reason.code === "UNAUTHENTICATED") {
      this.patch({ authenticated: false, me: null });
    } else if (meRes.reason instanceof NetworkError || meRes.reason instanceof ApiError) {
      failed = failed || meRes.reason instanceof NetworkError || meRes.reason.status >= 500;
    }

    if (failed) {
      connectivity.set("reconnecting");
      this.errorStreak += 1;
      this.schedule(Math.min(MAX_ERROR_POLL_MS, 500 * 2 ** this.errorStreak));
      return;
    }
    connectivity.set("online");
    this.errorStreak = 0;
    const view = this.view.get();
    if (view.authenticated === false) this.schedule(SIGNED_OUT_POLL_MS);
    else this.schedule(view.me?.poll_after_ms ?? FALLBACK_POLL_MS);
    this.resumePending();
  }

  /** Ignores a /me answer older than the one already shown (out-of-order polls). */
  private applyMe(me: Me): void {
    const t = Date.parse(me.server_time);
    if (t < this.lastMeTime) return;
    this.lastMeTime = t;
    this.patch({ me, authenticated: true });
  }

  /** After a refresh or reconnect, finish a claim that was started with the same key. */
  private resumePending(): void {
    const { me, busy } = this.view.get();
    if (busy || !me?.entry?.admission_token || me.allocation) return;
    if (me.entry.status === "OFFERED" && isPending(this.dropId, "claim")) void this.claim();
  }

  // ---------------------------------------------------------------- actions

  signedIn(): void {
    this.patch({ authenticated: null, actionError: null });
    void this.refresh();
  }

  private fail(action: Action, err: unknown): void {
    if (err instanceof ApiError && err.code === "UNAUTHENTICATED") {
      this.patch({ authenticated: false, me: null });
      return;
    }
    const code = err instanceof ApiError ? err.code : "NETWORK";
    const requestId = err instanceof ApiError ? err.requestId : null;
    this.patch({ actionError: { action, code, requestId } });
  }

  async enter(): Promise<void> {
    if (this.view.get().busy) return;
    this.patch({ busy: "enter", actionError: null });
    try {
      const key = actionKey(this.dropId, "enter");
      await withRetry(() => api.enter(this.dropId, key));
      finishAction(this.dropId, "enter");
    } catch (err) {
      if (err instanceof ApiError && TERMINAL.has(err.code)) finishAction(this.dropId, "enter");
      else this.fail("enter", err);
    } finally {
      this.patch({ busy: null });
      await this.refresh();
    }
  }

  /** FIFO "Get a seat": enter (idempotent) then claim with the token /me hands out. */
  async getSeat(): Promise<void> {
    if (!this.view.get().me?.entry) {
      await this.enter();
      if (!this.view.get().me?.entry) return;
    }
    await this.claim();
  }

  async claim(): Promise<void> {
    if (this.view.get().busy) return;
    this.patch({ busy: "claim", actionError: null });
    markPending(this.dropId, "claim");
    const key = actionKey(this.dropId, "claim");
    let token = this.view.get().me?.entry?.admission_token ?? null;
    let refreshedToken = false;
    try {
      for (;;) {
        if (!token) {
          await this.refresh();
          token = this.view.get().me?.entry?.admission_token ?? null;
          if (!token) {
            finishAction(this.dropId, "claim");
            return;
          }
        }
        const t = token;
        try {
          const seat = await withRetry(() => api.claim(this.dropId, t, key));
          finishAction(this.dropId, "claim");
          this.patch({
            localAllocation: {
              allocation_id: seat.allocation_id,
              seat_no: seat.seat_no,
              confirmed_at: seat.confirmed_at,
            },
          });
          return;
        } catch (err) {
          // Expired or session-bound token: one fresh token from /me, same key.
          if (err instanceof ApiError && err.code === "TOKEN_INVALID" && !refreshedToken) {
            refreshedToken = true;
            token = null;
            continue;
          }
          throw err;
        }
      }
    } catch (err) {
      if (err instanceof ApiError && TERMINAL.has(err.code)) {
        finishAction(this.dropId, "claim");
        if (err.code === "TOKEN_INVALID") this.fail("claim", err);
      } else {
        this.fail("claim", err); // pending marker kept: the next good poll resumes it
      }
    } finally {
      this.patch({ busy: null });
      void this.refresh();
    }
  }

  async stepUp(otp: string): Promise<boolean> {
    if (this.view.get().busy) return false;
    this.patch({ busy: "step-up", actionError: null });
    try {
      // Only successes are stored server-side, so one key covers several code attempts.
      const key = actionKey(this.dropId, "step-up");
      await withRetry(() => api.stepUp(this.dropId, otp, key));
      finishAction(this.dropId, "step-up");
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.code === "OFFER_EXPIRED") {
        finishAction(this.dropId, "step-up");
      } else {
        this.fail("step-up", err);
      }
      return false;
    } finally {
      this.patch({ busy: null });
      await this.refresh();
    }
  }
}
