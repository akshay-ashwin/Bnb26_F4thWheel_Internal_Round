// One component per UI state. Each reads only what the resolver already decided on.
import { Banner } from "../../../components/Banner";
import { Button } from "../../../components/Button";
import { Countdown } from "../../../components/Countdown";
import { OtpInput } from "../../../components/OtpInput";
import { navigate } from "../../../app/router";
import type { Allocation, Drop, MeEntry } from "../../../api/types";
import { copy } from "../copy";

function Title({ children }: { children: string }) {
  return <h1 className="text-2xl font-semibold">{children}</h1>;
}

function Commitment({ drop }: { drop: Drop }) {
  return (
    <p className="text-xs text-slate-500">
      <span className="break-all font-mono" data-testid="seed-commit">
        {copy.commitment(drop.seed_commit.slice(0, 12))}
      </span>{" "}
      <a
        href="/how-it-works"
        onClick={(e) => {
          e.preventDefault();
          navigate(`/how-it-works?drop=${drop.id}`);
        }}
        className="underline"
      >
        {copy.howItWorksLink}
      </a>
    </p>
  );
}

function ProofLink({ drop }: { drop: Drop }) {
  if (drop.mode !== "fair" || !drop.seed) return null;
  return (
    <a
      className="text-sm underline"
      href={`/how-it-works?drop=${drop.id}`}
      onClick={(e) => {
        e.preventDefault();
        navigate(`/how-it-works?drop=${drop.id}`);
      }}
    >
      {copy.proofLink}
    </a>
  );
}

export function BeforeWindow({ drop }: { drop: Drop }) {
  return (
    <section className="space-y-4" data-state="BEFORE_WINDOW">
      <Title>{copy.beforeWindow.title}</Title>
      <p className="text-lg">{copy.seats(drop.capacity)}</p>
      {drop.reg_opens_at ? (
        <Countdown until={drop.reg_opens_at} label={copy.beforeWindow.opensIn} />
      ) : (
        <p className="text-slate-600">{copy.beforeWindow.opensSoon}</p>
      )}
      <Commitment drop={drop} />
    </section>
  );
}

export function CanEnter({
  drop,
  busy,
  onEnter,
}: {
  drop: Drop;
  busy: boolean;
  onEnter: () => void;
}) {
  return (
    <section className="space-y-4" data-state="CAN_ENTER">
      <p className="text-lg">{copy.seats(drop.capacity)}</p>
      <Button busy={busy} onClick={onEnter}>
        {copy.canEnter.button}
      </Button>
      <p className="text-slate-700">
        {copy.canEnter.sameChance} {copy.earlyDoesntHelp}
      </p>
      {drop.reg_closes_at && (
        <Countdown until={drop.reg_closes_at} label={copy.canEnter.closesIn} />
      )}
      <Commitment drop={drop} />
    </section>
  );
}

export function EnteredWaitingClose({ drop }: { drop: Drop }) {
  return (
    <section className="space-y-4" data-state="ENTERED_WAITING_CLOSE">
      <Title>{`${copy.entered.title} ${copy.earlyDoesntHelp}`}</Title>
      <p className="text-slate-700">{copy.entered.saved}</p>
      {drop.reg_closes_at && <Countdown until={drop.reg_closes_at} label={copy.entered.drawIn} />}
      <Commitment drop={drop} />
    </section>
  );
}

export function WaitingDraw({ drop }: { drop: Drop }) {
  return (
    <section className="space-y-4" data-state="WAITING_DRAW">
      <Title>{copy.waitingDraw.title}</Title>
      <p className="text-slate-700">{copy.waitingDraw.body}</p>
      <Commitment drop={drop} />
    </section>
  );
}

export function FifoRace({
  drop,
  busy,
  onGetSeat,
}: {
  drop: Drop;
  busy: boolean;
  onGetSeat: () => void;
}) {
  return (
    <section className="space-y-4" data-state="FIFO_RACE">
      <p className="text-xs uppercase tracking-wide text-slate-500">{copy.fifo.label}</p>
      <p className="text-lg">{copy.fifo.left(drop.seats_remaining)}</p>
      <Button busy={busy} onClick={onGetSeat}>
        {copy.fifo.button}
      </Button>
    </section>
  );
}

export function Offered({
  entry,
  busy,
  onConfirm,
}: {
  entry: MeEntry;
  busy: boolean;
  onConfirm: () => void;
}) {
  return (
    <section className="space-y-4" data-state="OFFERED">
      <Title>{copy.offered.title}</Title>
      {entry.offer_expires_at && (
        <Countdown
          until={entry.offer_expires_at}
          label={copy.offered.confirmWithin}
          urgentBelowMs={20_000}
        />
      )}
      <Button busy={busy} onClick={onConfirm}>
        {busy ? copy.offered.confirming : copy.offered.button}
      </Button>
    </section>
  );
}

export function StepUp({
  entry,
  busy,
  wrongCode,
  onCode,
}: {
  entry: MeEntry;
  busy: boolean;
  wrongCode: boolean;
  onCode: (code: string) => void;
}) {
  return (
    <section className="space-y-4" data-state="STEP_UP">
      <Title>{copy.stepUp.title}</Title>
      <p className="text-slate-700">{copy.stepUp.body}</p>
      {entry.offer_expires_at && (
        <Countdown
          until={entry.offer_expires_at}
          label={copy.stepUp.timeLeft}
          urgentBelowMs={20_000}
        />
      )}
      {entry.dev_otp && (
        <p
          className="rounded-md border border-dashed border-amber-400 bg-amber-50 px-3 py-2 font-mono text-sm text-amber-900"
          data-testid="dev-otp"
        >
          {copy.verify.devHint(entry.dev_otp)}
        </p>
      )}
      {wrongCode && <Banner tone="danger">{copy.stepUp.otpInvalid}</Banner>}
      <OtpInput label={copy.verify.codeLabel} disabled={busy} onComplete={onCode} />
    </section>
  );
}

export function Allocated({ allocation }: { allocation: Allocation | null }) {
  return (
    <section className="space-y-4 text-center" data-state="ALLOCATED">
      <Title>{copy.allocated.title}</Title>
      {allocation && (
        <>
          <p className="text-sm uppercase tracking-wide text-slate-500">{copy.allocated.seat}</p>
          <p className="text-7xl font-bold tabular-nums" data-testid="seat-no">
            {allocation.seat_no}
          </p>
          <p className="text-sm text-slate-600">
            {copy.allocated.confirmation}{" "}
            <span className="font-mono" data-testid="allocation-id">
              {allocation.allocation_id.slice(0, 8).toUpperCase()}
            </span>
          </p>
        </>
      )}
      <p className="text-slate-700">{copy.allocated.tied}</p>
    </section>
  );
}

export function Waitlisted({ entry }: { entry: MeEntry }) {
  return (
    <section className="space-y-4" data-state="WAITLISTED">
      {entry.waitlist_pos !== null && <Title>{copy.waitlisted.position(entry.waitlist_pos)}</Title>}
      <p className="text-slate-700">{copy.waitlisted.body}</p>
      <p className="text-slate-600">{copy.waitlisted.keep}</p>
    </section>
  );
}

function Final({
  state,
  title,
  body,
  drop,
}: {
  state: string;
  title: string;
  body: string;
  drop: Drop;
}) {
  return (
    <section className="space-y-4" data-state={state}>
      <Title>{title}</Title>
      <p className="text-slate-700">{body}</p>
      <ProofLink drop={drop} />
    </section>
  );
}

export const OfferExpired = ({ drop }: { drop: Drop }) => (
  <Final
    state="OFFER_EXPIRED"
    title={copy.offerExpired.title}
    body={copy.offerExpired.body}
    drop={drop}
  />
);
export const NotSelected = ({ drop }: { drop: Drop }) => (
  <Final
    state="NOT_SELECTED"
    title={copy.notSelected.title}
    body={copy.notSelected.body}
    drop={drop}
  />
);
export const SoldOut = ({ drop }: { drop: Drop }) => (
  <Final state="SOLD_OUT" title={copy.soldOut.title} body={copy.soldOut.body} drop={drop} />
);
export const RegistrationClosed = ({ drop }: { drop: Drop }) => (
  <Final
    state="REGISTRATION_CLOSED"
    title={copy.registrationClosed.title}
    body={copy.registrationClosed.body}
    drop={drop}
  />
);
