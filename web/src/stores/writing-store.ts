import i18n from "@/i18n";
import { create } from "zustand";
import { errorMessage } from "@/lib/api/client";
import {
  fetchWritingState,
  type WritingStatePayload,
} from "@/lib/api/writing";

/**
 * The project's writing state, read once and shared by the panel and its
 * sidebar. Nothing edits it yet: the panel is a reader until the save slice.
 */
type WritingStore = {
  project: string | null;
  state: WritingStatePayload | null;
  loading: boolean;
  error: string | null;
  /** Read a project's state unless it is already read or on its way. */
  load: (project: string, force?: boolean) => Promise<void>;
};

export const useWritingStore = create<WritingStore>((set, get) => ({
  project: null,
  state: null,
  loading: false,
  error: null,
  load: async (project, force = false) => {
    const current = get();
    if (
      !force &&
      current.project === project &&
      (current.state !== null || current.loading)
    ) {
      return;
    }
    set({ project, loading: true, error: null });
    try {
      const state = await fetchWritingState(project);
      // A slow answer for the project we just left must not replace the one
      // the reader is looking at now.
      if (get().project !== project) return;
      set({ state, loading: false });
    } catch (err) {
      if (get().project !== project) return;
      set({
        error: errorMessage(err, i18n.t("writing.error")),
        loading: false,
      });
    }
  },
}));
