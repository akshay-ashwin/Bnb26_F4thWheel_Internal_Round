// Every user-facing sentence of the user journey lives here, so one test can enforce the copy
// principles (copy.test.ts):
//   1. Never accuse: no "bot", "suspicious", "fraud", "flagged".
//   2. Never imply that retrying, refreshing or speed helps: no "try again", "retry", "refresh",
//      "hurry", "be quick"; terminal screens have no retry button.
//   3. Show the fairness promise: the seed commitment and "arriving early doesn't help".

export const copy = {
  appName: "Fair Drop",
  loading: "Loading…",
  seats: (n: number) => `${n} ${n === 1 ? "seat" : "seats"}`,
  howItWorksLink: "How the draw works",
  commitment: (prefix: string) => `Draw commitment: ${prefix}…`,
  earlyDoesntHelp: "Arriving early doesn't help.",

  dropNotFound: {
    title: "Drop not found",
    body: "Check the link you were given.",
  },

  verify: {
    title: "Verify your phone",
    body: "One verified phone gets one entry. We send a 6-digit code.",
    phoneLabel: "Mobile number",
    phoneHint: "Indian mobile, e.g. 98765 43210",
    send: "Send code",
    codeLabel: "Enter the 6-digit code",
    codeSentTo: (phone: string) => `Code sent to ${phone}.`,
    changeNumber: "Use a different number",
    verifying: "Checking…",
    devHint: (otp: string) => `Dev mode code: ${otp}`,
    invalidPhone: "That doesn't look like an Indian mobile number.",
    throttled: (wait: string) => `Too many codes requested. You can ask for a new one in ${wait}.`,
    otpInvalid: "That code didn't match. Check it and enter it again.",
    otpExpired: "That code has expired. Send a new one.",
  },

  beforeWindow: {
    title: "Entry opens soon",
    opensIn: "Entry opens in",
    opensSoon: "Opens soon",
  },

  canEnter: {
    button: "Enter the draw",
    sameChance: "Entering at any time in the window gives the same chance.",
    closesIn: "Entry closes in",
  },

  entered: {
    title: "You're in.",
    saved: "You can close this tab — your entry is saved.",
    drawIn: "The draw starts in",
  },

  waitingDraw: {
    title: "The draw is happening…",
    body: "Everyone who entered in the window has the same chance. Results appear here in a few seconds.",
  },

  fifo: {
    label: "First come, first served",
    button: "Get a seat",
    left: (n: number) => `${n} left`,
  },

  offered: {
    title: "You won a seat.",
    confirmWithin: "Confirm within",
    button: "Confirm my seat",
    confirming: "Confirming…",
  },

  stepUp: {
    title: "Quick check",
    body: "Enter the code we just sent to your phone to confirm it's you.",
    timeLeft: "Time left to confirm",
    button: "Check code",
    otpInvalid: "That code didn't match. Check it and enter it again.",
  },

  allocated: {
    title: "Your seat is confirmed",
    seat: "Seat",
    confirmation: "Confirmation",
    tied: "This seat is tied to your verified phone and can't be transferred.",
  },

  waitlisted: {
    position: (n: number) => `You're #${n} on the waitlist.`,
    body: "Seats free up as offers expire.",
    keep: "We keep your place. Check back before the claim window ends.",
  },

  offerExpired: {
    title: "Your confirmation window ended",
    body: "Your confirmation window ended, so the seat went to the next person.",
  },

  notSelected: {
    title: "Not selected this time",
    body: "The draw didn't pick your entry. Every entry had the same chance.",
  },

  soldOut: {
    title: "Sold out",
    body: "All seats in this drop are taken.",
  },

  registrationClosed: {
    title: "Entry is closed",
    body: "This drop is no longer taking entries.",
  },

  proofLink: "Check the draw yourself",

  overlays: {
    reconnecting: "Reconnecting… your place is safe",
    rateLimited: "Slow down a moment",
    generic: "Something went wrong. Your place is safe; we'll keep trying.",
    details: "Details",
    requestId: (id: string) => `Request id: ${id}`,
    signInAgain: "Please verify your phone again.",
  },

  howItWorks: {
    title: "How the draw works",
    steps: [
      "Commit: before entry opens, we publish the SHA-256 hash of a secret seed. That fixes the seed without revealing it.",
      "Enter: during the window, each verified phone can enter once. When you enter doesn't matter.",
      "Reveal: after entry closes, we publish the seed and a hash of the full entry list. Anyone can check that the seed matches the commitment.",
      "Rank: every entry gets a rank key from the seed and its public id. The smallest keys win seats; the next ones form the waitlist in order.",
    ],
    formula: "rank key = HMAC-SHA256(seed, drop_id | user_public_id), sorted ascending",
    sameChance: "Your chance is the same no matter when you entered in the window.",
    proof: "Once the seed is revealed, the public proof lists every entry and its rank:",
    notRevealed: "The seed is revealed after the draw. Until then, only the commitment is public:",
    back: "Back to the drop",
  },
} as const;
