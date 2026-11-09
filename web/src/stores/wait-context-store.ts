import { create } from "zustand";

/**
 * The wait a job button is about to start.
 *
 * A chat wait is entered by pressing the original button, so the deep link
 * leaves the wait here and the plan or compile action picks it up. Only an
 * action for the same job may take it: a plan press must not spend a compile
 * wait that is still proposed.
 */
export type WaitContext = {
  sessionId: string;
  waitId: string;
  job: string;
  interface: string;
};

type WaitContextState = {
  pending: WaitContext | null;
  /** Leaves the wait for the next button press in that interface. */
  arm: (context: WaitContext) => void;
  /** Takes the pending wait for *job*, if it belongs to that job. */
  take: (job: string) => WaitContext | null;
  clear: () => void;
};

export const useWaitContextStore = create<WaitContextState>((set, get) => ({
  pending: null,

  arm: (context) => set({ pending: context }),

  take: (job) => {
    const pending = get().pending;
    if (!pending || pending.job !== job) return null;
    set({ pending: null });
    return pending;
  },

  clear: () => set({ pending: null }),
}));
