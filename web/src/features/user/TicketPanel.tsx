import { useState, type ReactNode } from "react";
import { Link } from "react-router";

import { describeError } from "../../api/errors";
import type { Drop, MeAllocation } from "../../api/types";
import { Countdown, useRemaining } from "../../components/Countdown";
import { Badge, Banner, Button, OtpInput, Skeleton, Spinner } from "../../components/ui";
import { formatNumber, formatTime, hashOf, shortId } from "../../lib/format";
import { useSession } from "../../state/session";
import type { Presentation } from "./catalog";
import { journeyView } from "./journeyView";
import type { Journey } from "./useDropJourney";

function PanelShell({
  eyebrow,
  title,
  children,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="animate-rise space-y-4">
      <div>
        {eyebrow && <div className="mb-2">{eyebrow}</div>}
        <h2 className="text-2xl leading-tight font-bold tracking-tight">{title}</h2>
      </div>
      {children}
    </div>
  );
}

const FAIR_NOTE =
  "Tapping faster or refreshing does not change your chance. One person, one entry.";

/** A deterministic QR-like pattern from the allocation id (decorative; the id is the real proof). */
function TicketCode({ value }: { value: string }) {
  const size = 11;
  const cells = Array.from({ length: size * size }, (_, i) => {
    const row = Math.floor(i / size);
    const col = i % size;
    const corner = (r: number, c: number) => r < 3 && c < 3;
    if (corner(row, col) || corner(row, size - 1 - col) || corner(size - 1 - row, col)) return true;
    return hashOf(`${value}:${i}`) % 5 < 2;
  });
  return (
    <div
      className="grid h-24 w-24 gap-px rounded-lg bg-white p-1.5"
      style={{ gridTemplateColumns: `repeat(${size}, 1fr)` }}
      aria-hidden="true"
    >
      {cells.map((on, i) => (
        <span key={i} className={on ? "bg-ink-950 rounded-[1px]" : ""} />
      ))}
    </div>
  );
}

export function Ticket({
  presentation,
  allocation,
  dropId,
}: {
  presentation: Presentation;
  allocation: MeAllocation;
  dropId?: string;
}) {
  const [from, to] = presentation.palette;
  return (
    <div className="animate-pop overflow-hidden rounded-3xl text-white shadow-2xl shadow-black/50">
      <div className="p-5" style={{ background: `linear-gradient(135deg, ${from}, ${to})` }}>
        <p className="text-[10px] font-semibold tracking-[0.2em] text-white/70 uppercase">
          Confirmed seat
        </p>
        <p className="mt-1 text-2xl leading-none font-black tracking-tight uppercase">
          {presentation.title}
        </p>
        <p className="mt-1 text-sm text-white/80">{presentation.subtitle}</p>
        <p className="mt-4 text-xs text-white/70">
          {presentation.when} · {presentation.venue}, {presentation.city}
        </p>
      </div>
      <div className="bg-ink-100 text-ink-950 ticket-notch flex items-center justify-between gap-4 border-t-2 border-dashed border-black/20 p-5">
        <div>
          <p className="text-ink-500 text-[10px] font-semibold tracking-widest uppercase">Seat</p>
          <p className="tabular text-5xl leading-none font-black tracking-tight">
            {String(allocation.seat_no).padStart(3, "0")}
          </p>
          <p className="text-ink-500 mt-2 font-mono text-[11px]">
            {shortId(allocation.allocation_id, 8, 4)}
          </p>
          <p className="text-ink-500 text-[11px]">
            Confirmed {formatTime(allocation.confirmed_at)}
          </p>
        </div>
        <TicketCode value={allocation.allocation_id} />
      </div>
      {dropId && (
        <Link
          to={`/drop/${dropId}`}
          className="bg-ink-800 text-ink-300 hover:text-ink-100 block px-5 py-3 text-center text-xs font-medium"
        >
          View drop
        </Link>
      )}
    </div>
  );
}

function OfferTimer({ expiresAt }: { expiresAt: string | null }) {
  const remaining = useRemaining(expiresAt);
  const urgent = remaining !== null && remaining < 30_000;
  return (
    <div className="border-ink-700 bg-ink-800 flex items-center justify-between rounded-2xl border px-4 py-3">
      <span className="text-ink-300 text-sm">Offer held for you</span>
      <Countdown
        to={expiresAt}
        className={`text-2xl font-bold ${urgent ? "text-warning" : "text-ink-100"}`}
      />
    </div>
  );
}

function StepUpForm({ journey, devOtp }: { journey: Journey; devOtp: string | null }) {
  const [otp, setOtp] = useState("");
  const submit = (code: string) => {
    setOtp("");
    return journey.stepUp(code);
  };
  return (
    <div className="space-y-3">
      <OtpInput
        value={otp}
        onChange={setOtp}
        onComplete={(code) => void submit(code)}
        disabled={journey.busy !== null}
        label="Confirmation code"
      />
      {devOtp && (
        <p className="text-ink-400 text-xs">
          Test mode, no SMS sent. Your code is{" "}
          <button
            type="button"
            className="text-brand-300 font-mono font-bold underline"
            onClick={() => void submit(devOtp)}
          >
            {devOtp}
          </button>
        </p>
      )}
      <Button
        size="lg"
        className="w-full"
        busy={journey.busy === "step-up"}
        disabled={otp.length < 6}
        onClick={() => submit(otp)}
      >
        Confirm it's me
      </Button>
    </div>
  );
}

/** The right-hand (or bottom) panel on a drop page: one screen per state of `/me`. */
export function TicketPanel({
  drop,
  journey,
  presentation,
}: {
  drop: Drop;
  journey: Journey;
  presentation: Presentation;
}) {
  const openSignIn = useSession((s) => s.openSignIn);
  const view = journeyView(drop, journey.me, journey.authed);
  const fifo = drop.mode === "fifo";

  const body = (() => {
    switch (view.kind) {
      case "loading":
        return (
          <div className="space-y-3">
            <Skeleton className="h-8 w-2/3" />
            <Skeleton className="h-13 w-full" />
          </div>
        );

      case "scheduled":
        return (
          <PanelShell eyebrow={<Badge tone="info">Opening soon</Badge>} title="Entries open in">
            <Countdown to={view.opensAt} className="block text-5xl font-black tracking-tight" />
            {!view.opensAt && (
              <p className="text-ink-300 text-sm">The organiser will open it soon.</p>
            )}
            <p className="text-ink-400 text-sm">
              {fifo
                ? "This drop is first come, first served."
                : "No need to queue at the exact second. Everyone who enters during the window gets the same chance."}
            </p>
            {journey.authed === false && (
              <Button variant="secondary" size="lg" className="w-full" onClick={openSignIn}>
                Verify phone to get ready
              </Button>
            )}
            {journey.authed && <Badge tone="success">✓ Phone verified. You're ready.</Badge>}
          </PanelShell>
        );

      case "sign_in":
        return (
          <PanelShell
            eyebrow={
              <Badge tone="success" dot>
                Entries open
              </Badge>
            }
            title={fifo ? "Seats are on sale" : "Enter the draw"}
          >
            <p className="text-ink-300 text-sm">
              Verify your phone first. It takes a few seconds and proves you are one real person.
            </p>
            <Button size="lg" className="w-full" onClick={openSignIn}>
              Verify phone to continue
            </Button>
          </PanelShell>
        );

      case "can_enter":
        return (
          <PanelShell
            eyebrow={
              <Badge tone="success" dot>
                Entries open
              </Badge>
            }
            title="Enter the draw"
          >
            <div className="border-ink-700 bg-ink-800 flex items-center justify-between rounded-2xl border px-4 py-3">
              <span className="text-ink-300 text-sm">Window closes in</span>
              <Countdown to={drop.reg_closes_at} className="text-2xl font-bold" />
            </div>
            <Button
              size="lg"
              className="w-full"
              busy={journey.busy === "enter"}
              busyLabel="Entering…"
              onClick={journey.enter}
            >
              Enter for a seat
            </Button>
            <p className="text-ink-400 text-xs">{FAIR_NOTE}</p>
          </PanelShell>
        );

      case "entered":
        return (
          <PanelShell
            eyebrow={<Badge tone="success">✓ You're in</Badge>}
            title="Your entry is locked in"
          >
            <div className="border-ink-700 bg-ink-800 flex items-center justify-between rounded-2xl border px-4 py-3">
              <span className="text-ink-300 text-sm">Draw happens in</span>
              <Countdown to={view.closesAt} className="text-2xl font-bold" doneLabel="any moment" />
            </div>
            <p className="text-ink-300 text-sm">
              You can close this page. When the window ends, a draw that anyone can re-check picks
              the order. Come back to see your result.
            </p>
            <p className="text-ink-400 text-xs">{FAIR_NOTE}</p>
          </PanelShell>
        );

      case "fifo_grab":
        return (
          <PanelShell
            eyebrow={
              <Badge tone="warning" dot>
                First come, first served
              </Badge>
            }
            title={`${formatNumber(drop.seats_remaining)} seats left`}
          >
            <Button
              size="lg"
              className="w-full"
              busy={journey.busy === "claim"}
              busyLabel="Grabbing…"
              onClick={journey.claim}
            >
              Grab a seat
            </Button>
            <p className="text-ink-400 text-xs">
              In this mode the fastest request wins, which is exactly what scripts are good at.
            </p>
          </PanelShell>
        );

      case "sold_out":
        return (
          <PanelShell eyebrow={<Badge tone="danger">Sold out</Badge>} title="All seats are gone">
            <p className="text-ink-300 text-sm">
              Every seat was taken within seconds of opening. In a first-come drop, people can't
              compete with automated clients.
            </p>
            <Link to="/how-it-works" className="text-brand-400 text-sm font-medium hover:underline">
              See how a fair drop fixes this →
            </Link>
          </PanelShell>
        );

      case "drawing":
        return (
          <PanelShell eyebrow={<Badge tone="brand">Window closed</Badge>} title="Running the draw…">
            <div className="text-ink-300 flex items-center gap-3 text-sm">
              <Spinner className="text-brand-400 h-5 w-5" />
              The list of entrants is frozen and being ranked. This takes a few seconds.
            </div>
          </PanelShell>
        );

      case "offered":
        return (
          <PanelShell
            eyebrow={<Badge tone="success">✦ You got a seat</Badge>}
            title="Claim it before the timer ends"
          >
            <OfferTimer expiresAt={view.expiresAt} />
            <Button
              size="lg"
              className="w-full"
              busy={journey.busy === "claim"}
              busyLabel="Confirming your seat…"
              onClick={journey.claim}
            >
              Claim my seat
            </Button>
            {view.rank !== null && (
              <p className="text-ink-400 text-xs">
                Your draw rank is #{formatNumber(view.rank)} of {formatNumber(drop.capacity)} seats.
                The seat is reserved for you, so there is no rush against other people.
              </p>
            )}
          </PanelShell>
        );

      case "step_up":
        return (
          <PanelShell
            eyebrow={<Badge tone="warning">One more check</Badge>}
            title="Confirm it's really you"
          >
            <OfferTimer expiresAt={view.expiresAt} />
            <p className="text-ink-300 text-sm">
              You got a seat. Before you claim it, enter the fresh code we sent to your phone. This
              extra check never changes anyone's chance in the draw.
            </p>
            <StepUpForm journey={journey} devOtp={view.devOtp} />
          </PanelShell>
        );

      case "allocated":
        return (
          <div className="space-y-4">
            <Ticket presentation={presentation} allocation={view.allocation} />
            <p className="text-ink-400 text-center text-xs">
              This seat is yours. It is saved on the server, so it is safe to close this page.
            </p>
          </div>
        );

      case "waitlisted":
        return (
          <PanelShell eyebrow={<Badge tone="info">Waitlist</Badge>} title="You're on the waitlist">
            <div className="border-ink-700 bg-ink-800 rounded-2xl border px-4 py-4 text-center">
              <p className="text-ink-400 text-xs tracking-wide uppercase">Your position</p>
              <p className="tabular text-5xl font-black tracking-tight">
                #{view.position !== null ? formatNumber(view.position) : "–"}
              </p>
            </div>
            <p className="text-ink-300 text-sm">
              If someone ahead doesn't claim in time, their seat moves down the list in draw order.
              Keep this page open and we'll update it by itself.
            </p>
          </PanelShell>
        );

      case "not_selected":
        return (
          <PanelShell eyebrow={<Badge>Result</Badge>} title="No seat this time">
            <p className="text-ink-300 text-sm">
              {fifo
                ? "The seats were gone before your request arrived."
                : "The draw didn't pick your entry. Everyone had the same chance, and you can verify the draw yourself."}
            </p>
            {view.rank !== null && (
              <p className="text-ink-400 text-xs">Your draw rank was #{formatNumber(view.rank)}.</p>
            )}
          </PanelShell>
        );

      case "offer_expired":
        return (
          <PanelShell
            eyebrow={<Badge tone="danger">Offer expired</Badge>}
            title="The claim window ended"
          >
            <p className="text-ink-300 text-sm">
              Your seat was held until the timer ran out, then passed to the next person on the
              waitlist.
            </p>
          </PanelShell>
        );

      case "disqualified":
        return (
          <PanelShell
            eyebrow={<Badge tone="danger">Entry removed</Badge>}
            title="This entry can't continue"
          >
            <p className="text-ink-300 text-sm">
              This entry was removed from the drop. If you think this is a mistake, contact support.
            </p>
          </PanelShell>
        );

      case "missed":
        return (
          <PanelShell
            eyebrow={<Badge>{drop.phase === "DONE" ? "Drop finished" : "Entries closed"}</Badge>}
            title={drop.phase === "DONE" ? "This drop is over" : "The entry window has closed"}
          >
            <p className="text-ink-300 text-sm">
              {journey.authed === false
                ? "If you entered, verify your phone to see your result."
                : "You didn't enter this drop before the window closed."}
            </p>
            {journey.authed === false && (
              <Button variant="secondary" size="lg" className="w-full" onClick={openSignIn}>
                Verify phone
              </Button>
            )}
          </PanelShell>
        );
    }
  })();

  return (
    <div className="space-y-3" aria-live="polite">
      {body}
      {journey.notice && (
        <Banner tone="warning" live>
          {journey.notice}
        </Banner>
      )}
      {journey.actionError !== null && (
        <Banner
          tone="danger"
          action={
            <button
              type="button"
              aria-label="Dismiss"
              className="opacity-70 hover:opacity-100"
              onClick={journey.dismissError}
            >
              ✕
            </button>
          }
        >
          {describeError(journey.actionError)}
        </Banner>
      )}
    </div>
  );
}
