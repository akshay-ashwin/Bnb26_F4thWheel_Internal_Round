import { lazy, Suspense } from "react";
import { NavLink, Link, Outlet, ScrollRestoration } from "react-router";

import { mocksEnabled, setMocksEnabled } from "../api/config";
import { SignInModal } from "../features/user/SignInModal";
import { cx, shortId } from "../lib/format";
import { useConnectivity } from "../state/connectivity";
import { useSession } from "../state/session";
import { Badge, Button } from "./ui";

// Demo-only code (and the mock API behind it) stays out of the bundle the live site loads.
const DemoPanel = lazy(() => import("./DemoPanel").then((m) => ({ default: m.DemoPanel })));

const NAV = [
  { to: "/", label: "Drops", icon: "◉", end: true },
  { to: "/tickets", label: "My tickets", icon: "▤", end: false },
  { to: "/how-it-works", label: "How it works", icon: "✦", end: false },
  { to: "/admin", label: "Console", icon: "▦", end: false },
];

export function Logo() {
  return (
    <Link to="/" className="flex items-center gap-2 text-xl font-black tracking-tight">
      <span className="bg-brand-600 flex h-8 w-8 items-center justify-center rounded-xl text-base text-white">
        f
      </span>
      <span>
        fair<span className="text-brand-400">drop</span>
      </span>
    </Link>
  );
}

function switchToDemo() {
  setMocksEnabled(true);
  window.location.reload();
}

/** Global "where is the server" strip. Screen readers hear changes (aria-live). */
function ConnectivityBanner() {
  const status = useConnectivity((s) => s.status);
  if (status === "online") return null;
  return (
    <div
      role="status"
      aria-live="polite"
      className="bg-warning text-ink-950 flex flex-wrap items-center justify-center gap-x-3 gap-y-1 px-4 py-2 text-center text-sm font-semibold"
    >
      <span>
        {status === "offline"
          ? "You're offline. Your place is safe. We'll reconnect by ourselves."
          : "Reconnecting… your place is safe."}
      </span>
      {!mocksEnabled() && (
        <button type="button" className="underline" onClick={switchToDemo}>
          Server not running? Use demo data
        </button>
      )}
    </div>
  );
}

function Profile() {
  const user = useSession((s) => s.userPublicId);
  const openSignIn = useSession((s) => s.openSignIn);
  if (!user) {
    return (
      <Button size="sm" variant="secondary" onClick={openSignIn}>
        Sign in
      </Button>
    );
  }
  return (
    <button
      type="button"
      onClick={openSignIn}
      title="Use a different number"
      className="border-ink-700 bg-ink-850 hover:border-ink-600 flex items-center gap-2 rounded-full border py-1 pr-3 pl-1 text-xs"
    >
      <span className="bg-brand-600 flex h-6 w-6 items-center justify-center rounded-full text-[10px] font-bold text-white uppercase">
        {user.slice(0, 2)}
      </span>
      <span className="text-ink-300 font-mono">{shortId(user, 4, 3)}</span>
    </button>
  );
}

export function Layout() {
  const demo = mocksEnabled();
  return (
    <div className="flex min-h-screen flex-col">
      <ConnectivityBanner />
      <header className="border-ink-800 bg-ink-950/85 sticky top-0 z-30 border-b backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-6xl items-center gap-6 px-4">
          <Logo />
          <span className="border-ink-700 text-ink-300 hidden items-center gap-1.5 rounded-full border px-3 py-1 text-xs sm:flex">
            <span className="text-brand-400">⌖</span> Pune
          </span>
          <nav className="ml-auto hidden items-center gap-1 md:flex" aria-label="Main">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  cx(
                    "rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors",
                    isActive ? "bg-ink-800 text-ink-100" : "text-ink-400 hover:text-ink-100",
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 md:ml-0">
            {demo && <Badge tone="brand">Demo data</Badge>}
            <Profile />
          </div>
        </div>
      </header>

      <main className="flex-1 pb-24 md:pb-0">
        <Outlet />
      </main>

      <footer className="border-ink-800 text-ink-500 mb-16 border-t px-4 py-8 text-center text-xs md:mb-0">
        <p>
          Fair Drop · one verified person, one entry, one equal chance.{" "}
          <Link to="/how-it-works" className="text-ink-300 hover:underline">
            How it works
          </Link>
        </p>
        <button
          type="button"
          className="hover:text-ink-300 mt-2 underline"
          onClick={() => {
            setMocksEnabled(!demo);
            window.location.reload();
          }}
        >
          {demo ? "Switch to the live API" : "Switch to demo data"}
        </button>
      </footer>

      <nav
        aria-label="Main"
        className="border-ink-800 bg-ink-950/95 fixed inset-x-0 bottom-0 z-30 grid grid-cols-4 border-t backdrop-blur-xl md:hidden"
      >
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              cx(
                "flex flex-col items-center gap-0.5 py-2.5 text-[11px] font-medium",
                isActive ? "text-brand-400" : "text-ink-400",
              )
            }
          >
            <span aria-hidden="true" className="text-lg leading-none">
              {item.icon}
            </span>
            {item.label}
          </NavLink>
        ))}
      </nav>

      <SignInModal />
      {demo && (
        <Suspense fallback={null}>
          <DemoPanel />
        </Suspense>
      )}
      <ScrollRestoration />
    </div>
  );
}
