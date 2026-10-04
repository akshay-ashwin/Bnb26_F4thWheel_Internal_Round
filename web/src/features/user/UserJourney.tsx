import { useEffect, useMemo, type ReactNode } from "react";

import { useStore } from "../../lib/store";
import { copy } from "./copy";
import { DropController } from "./dropController";
import { Overlays } from "./Overlays";
import { resolveUiState, type UiState } from "./resolveUiState";
import {
  Allocated,
  BeforeWindow,
  CanEnter,
  EnteredWaitingClose,
  FifoRace,
  NotSelected,
  OfferExpired,
  Offered,
  RegistrationClosed,
  SoldOut,
  StepUp,
  WaitingDraw,
  Waitlisted,
} from "./screens/states";
import { VerifyScreen } from "./screens/VerifyScreen";

export function UserJourney({ dropId }: { dropId: string }) {
  const controller = useMemo(() => new DropController(dropId), [dropId]);
  useEffect(() => {
    controller.start();
    return () => controller.stop();
  }, [controller]);

  const view = useStore(controller.view);
  const state: UiState = resolveUiState(view);
  const { drop, me, busy, actionError } = view;
  const entry = me?.entry ?? null;

  let screen: ReactNode;
  switch (state) {
    case "LOADING":
      screen = <p className="text-slate-500">{copy.loading}</p>;
      break;
    case "DROP_NOT_FOUND":
      screen = (
        <section className="space-y-2" data-state="DROP_NOT_FOUND">
          <h1 className="text-2xl font-semibold">{copy.dropNotFound.title}</h1>
          <p className="text-slate-600">{copy.dropNotFound.body}</p>
        </section>
      );
      break;
    case "VERIFY":
      screen = <VerifyScreen onVerified={() => controller.signedIn()} />;
      break;
    default:
      // Every remaining state has a drop (the resolver returns LOADING without one).
      if (!drop) return null;
      screen = renderDropState(state, drop);
  }

  function renderDropState(s: UiState, d: NonNullable<typeof drop>): ReactNode {
    switch (s) {
      case "BEFORE_WINDOW":
        return <BeforeWindow drop={d} />;
      case "CAN_ENTER":
        return (
          <CanEnter drop={d} busy={busy === "enter"} onEnter={() => void controller.enter()} />
        );
      case "ENTERED_WAITING_CLOSE":
        return <EnteredWaitingClose drop={d} />;
      case "WAITING_DRAW":
        return <WaitingDraw drop={d} />;
      case "FIFO_RACE":
        return (
          <FifoRace drop={d} busy={busy !== null} onGetSeat={() => void controller.getSeat()} />
        );
      case "OFFERED":
        return (
          entry && (
            <Offered
              entry={entry}
              busy={busy === "claim"}
              onConfirm={() => void controller.claim()}
            />
          )
        );
      case "STEP_UP":
        return (
          entry && (
            <StepUp
              entry={entry}
              busy={busy === "step-up"}
              wrongCode={actionError?.action === "step-up" && actionError.code === "OTP_INVALID"}
              onCode={(code) => void controller.stepUp(code)}
            />
          )
        );
      case "ALLOCATED":
        return <Allocated allocation={me?.allocation ?? view.localAllocation} />;
      case "WAITLISTED":
        return entry && <Waitlisted entry={entry} />;
      case "OFFER_EXPIRED":
        return <OfferExpired drop={d} />;
      case "NOT_SELECTED":
        return <NotSelected drop={d} />;
      case "SOLD_OUT":
        return <SoldOut drop={d} />;
      default:
        return <RegistrationClosed drop={d} />;
    }
  }

  return (
    <div className="space-y-5">
      <Overlays actionError={actionError} />
      {drop && <p className="text-sm font-medium text-slate-500">{drop.name}</p>}
      {screen}
    </div>
  );
}
