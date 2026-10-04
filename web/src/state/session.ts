import { create } from "zustand";

import { readItem, removeItem, writeItem } from "../lib/storage";

const USER_KEY = "fd:user";

interface SessionState {
  /**
   * Who we last signed in as, for display only. The real session is an httpOnly cookie the page
   * cannot read; the server (a 401 from `/me`) is what decides whether we are signed in.
   */
  userPublicId: string | null;
  /** Bumped on every sign-in or sign-out so open screens refetch `/me`. */
  version: number;
  signInOpen: boolean;
  openSignIn: () => void;
  closeSignIn: () => void;
  signedIn: (userPublicId: string) => void;
  signedOut: () => void;
}

export const useSession = create<SessionState>((set) => ({
  userPublicId: readItem("local", USER_KEY),
  version: 0,
  signInOpen: false,
  openSignIn: () => set({ signInOpen: true }),
  closeSignIn: () => set({ signInOpen: false }),
  signedIn: (userPublicId) => {
    writeItem("local", USER_KEY, userPublicId);
    set((s) => ({ userPublicId, signInOpen: false, version: s.version + 1 }));
  },
  signedOut: () => {
    removeItem("local", USER_KEY);
    set((s) => (s.userPublicId === null ? s : { userPublicId: null, version: s.version + 1 }));
  },
}));
