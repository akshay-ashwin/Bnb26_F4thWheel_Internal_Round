import type { ErrorCode } from "./types";

/** A response that carried the contract's error envelope. */
export class ApiError extends Error {
  readonly code: ErrorCode | "UNKNOWN";
  readonly status: number;
  readonly retryAfterMs: number | null;
  readonly details: Record<string, unknown> | null;

  constructor(args: {
    code: ErrorCode | "UNKNOWN";
    status: number;
    message: string;
    retryAfterMs?: number | null;
    details?: Record<string, unknown> | null;
  }) {
    super(args.message);
    this.name = "ApiError";
    this.code = args.code;
    this.status = args.status;
    this.retryAfterMs = args.retryAfterMs ?? null;
    this.details = args.details ?? null;
  }
}

/** No response reached us: offline, DNS, dropped connection, or a timeout. */
export class NetworkError extends Error {
  readonly timedOut: boolean;

  constructor(message: string, timedOut = false, cause?: unknown) {
    super(message, { cause });
    this.name = "NetworkError";
    this.timedOut = timedOut;
  }
}

export function isApiError(err: unknown, code?: ErrorCode): err is ApiError {
  return err instanceof ApiError && (code === undefined || err.code === code);
}

/** Plain-language copy for each error code (docs/contract/error-codes.md, "UI state" column). */
export function describeError(err: unknown): string {
  if (err instanceof NetworkError) {
    return "We can't reach the server right now. Your place is safe. Try again in a moment.";
  }
  if (!(err instanceof ApiError)) return "Something went wrong. Please try again.";
  switch (err.code) {
    case "RATE_LIMITED":
      return "Slow down a moment. Sending more requests never improves your chance.";
    case "OTP_THROTTLED":
      return "Too many codes requested. Wait a little before asking for another.";
    case "INVALID_PHONE":
      return "That doesn't look like a valid Indian mobile number.";
    case "OTP_INVALID":
      return "That code isn't right. Check it and try again.";
    case "OTP_EXPIRED":
      return "That code expired. Send a new one.";
    case "UNAUTHENTICATED":
      return "Please verify your phone to continue.";
    case "WINDOW_CLOSED":
      return "Registration has closed for this drop.";
    case "WINDOW_NOT_OPEN":
      return "Registration hasn't opened yet.";
    case "TOKEN_INVALID":
      return "Your claim pass expired. Tap claim again to use a fresh one.";
    case "NOT_OFFERED":
      return "You don't have an offer for this drop right now.";
    case "OFFER_EXPIRED":
      return "Your offer expired before the seat was claimed.";
    case "SOLD_OUT":
      return "All seats are taken.";
    case "STEP_UP_REQUIRED":
      return "One more check: enter the code we sent to confirm it's you.";
    case "NOT_FOUND":
      return "We couldn't find that drop.";
    case "INVALID_TRANSITION":
      return "That action isn't allowed in the drop's current phase.";
    case "SERVICE_UNAVAILABLE":
      return "The server is busy. Your place is safe. Try again in a moment.";
    case "INTERNAL":
      return `Unexpected server error. ${err.message}`;
    default:
      return err.message || "Something went wrong. Please try again.";
  }
}
