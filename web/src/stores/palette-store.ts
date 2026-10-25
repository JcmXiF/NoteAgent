import { create } from "zustand";

/**
 * Whether the global search is open.
 *
 * It is one palette for the whole window rather than one per view: what a reader
 * is looking for is a name they half-remember, not a place they have already
 * decided to look.
 */
type PaletteState = {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
};

export const usePaletteStore = create<PaletteState>((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
  toggle: () => set((state) => ({ open: !state.open })),
}));
