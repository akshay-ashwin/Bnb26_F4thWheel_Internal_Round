// Three routes do not need a router library: /, /drop/:dropId, /how-it-works.
import { useSyncExternalStore } from "react";

function subscribe(cb: () => void): () => void {
  window.addEventListener("popstate", cb);
  return () => window.removeEventListener("popstate", cb);
}

const snapshot = () => window.location.pathname + window.location.search;

export function useLocation(): URL {
  const path = useSyncExternalStore(subscribe, snapshot, snapshot);
  return new URL(path, window.location.origin);
}

export function navigate(to: string): void {
  window.history.pushState(null, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export type Route =
  { name: "home" } | { name: "drop"; dropId: string } | { name: "how"; dropId: string | null };

export function matchRoute(url: URL): Route {
  const drop = /^\/drop\/([^/]+)\/?$/.exec(url.pathname);
  if (drop?.[1]) return { name: "drop", dropId: decodeURIComponent(drop[1]) };
  if (url.pathname === "/how-it-works")
    return { name: "how", dropId: url.searchParams.get("drop") };
  return { name: "home" };
}
