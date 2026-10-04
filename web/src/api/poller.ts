export interface PollerOptions<T> {
  fetch: () => Promise<T>;
  /** Delay before the next poll, taken from the response (the server paces us). */
  nextDelayMs: (data: T) => number | null | undefined;
  onData: (data: T) => void;
  /** Return `false` to stop polling (for example when the session is gone). */
  onError?: (err: unknown) => boolean | undefined;
  fallbackMs?: number;
}

export interface Poller {
  start: () => void;
  stop: () => void;
  /** Fetch now (after a write, on reconnect). Resolves when that fetch settles. */
  refresh: () => Promise<void>;
}

const MAX_ERROR_DELAY_MS = 15_000;

/**
 * One poller per resource per tab. Pauses while the tab is hidden, fetches immediately when it
 * becomes visible or the browser comes back online, and never overlaps two requests.
 */
export function createPoller<T>(options: PollerOptions<T>): Poller {
  const fallbackMs = options.fallbackMs ?? 3000;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let running = false;
  let inFlight: Promise<void> | null = null;
  let errorStreak = 0;

  const clear = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };

  const schedule = (delayMs: number) => {
    clear();
    if (!running || document.hidden) return;
    timer = setTimeout(() => void tick(), delayMs);
  };

  const tick = (): Promise<void> => {
    if (inFlight) return inFlight;
    clear();
    inFlight = (async () => {
      try {
        const data = await options.fetch();
        if (!running) return;
        errorStreak = 0;
        options.onData(data);
        schedule(Math.max(250, options.nextDelayMs(data) ?? fallbackMs));
      } catch (err) {
        if (!running) return;
        if (options.onError?.(err) === false) {
          running = false;
          return;
        }
        errorStreak += 1;
        schedule(Math.min(MAX_ERROR_DELAY_MS, fallbackMs * 2 ** (errorStreak - 1)));
      } finally {
        inFlight = null;
      }
    })();
    return inFlight;
  };

  const onVisibility = () => {
    if (!running) return;
    if (document.hidden) clear();
    else void tick();
  };
  const onOnline = () => {
    if (running) void tick();
  };

  return {
    start() {
      if (running) return;
      running = true;
      document.addEventListener("visibilitychange", onVisibility);
      window.addEventListener("online", onOnline);
      void tick();
    },
    stop() {
      running = false;
      clear();
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("online", onOnline);
    },
    refresh() {
      if (!running) return Promise.resolve();
      return tick();
    },
  };
}
