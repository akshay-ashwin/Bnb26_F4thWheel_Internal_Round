import { create } from "zustand";

export type Connectivity = "online" | "reconnecting" | "offline";

interface ConnectivityState {
  status: Connectivity;
  set: (status: Connectivity) => void;
}

export const useConnectivity = create<ConnectivityState>((set) => ({
  status: "online",
  set: (status) => set((prev) => (prev.status === status ? prev : { status })),
}));

/** Called by the HTTP layer after every request. */
export function reportReachable(reachable: boolean): void {
  const offline = typeof navigator !== "undefined" && navigator.onLine === false;
  useConnectivity.getState().set(reachable ? "online" : offline ? "offline" : "reconnecting");
}

/** Wire browser online/offline events. Returns the cleanup. */
export function watchBrowserConnectivity(): () => void {
  const onOffline = () => useConnectivity.getState().set("offline");
  const onOnline = () => useConnectivity.getState().set("reconnecting");
  window.addEventListener("offline", onOffline);
  window.addEventListener("online", onOnline);
  return () => {
    window.removeEventListener("offline", onOffline);
    window.removeEventListener("online", onOnline);
  };
}
