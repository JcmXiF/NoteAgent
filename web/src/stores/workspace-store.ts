import { create } from "zustand";
import {
  createProject,
  deleteProject,
  fetchProjects,
  openFolder,
  switchToProject,
  type FolderOpenMode,
  type FolderOpenResult,
} from "@/lib/api/projects";
import type { ProjectInfo } from "@/lib/api/types";
import { ApiError, UNREACHED_STATUS, errorMessage } from "@/lib/api/client";
import { forgetProjectIndex } from "@/stores/acquire-store";
import { forgetProjectReading } from "@/stores/docs-store";
import i18n from "@/i18n";

/** The project dialogs the window can have in front, wherever they were asked for. */
export type ProjectDialog = "create" | "open";

/**
 * The active project. It is global rather than per-view, because every other
 * store reads its data in the context of exactly one project.
 */
type WorkspaceState = {
  projects: ProjectInfo[];
  current: string | null;
  loading: boolean;
  busy: boolean;
  error: string | null;

  refresh: () => Promise<void>;
  select: (name: string) => Promise<void>;
  create: (name: string) => Promise<void>;
  /**
   * Opens a folder that already exists, in place or as a copy.
   *
   * Answers with what was opened, so the dialog that asked can say what came in
   * and what was left out; null when it did not open, with the reason in `error`.
   */
  openExisting: (
    path: string,
    mode: FolderOpenMode,
    name?: string,
  ) => Promise<FolderOpenResult | null>;
  remove: (name: string, confirmName: string) => Promise<void>;
  clearError: () => void;

  /**
   * Which project dialog is in front.
   *
   * Held here rather than in the bar that used to own it, because a reader with
   * no projects is asked to make one from the workspace itself — where the bar may
   * not even be on screen.
   */
  dialog: ProjectDialog | null;
  setDialog: (dialog: ProjectDialog | null) => void;
};

/**
 * How often, and how far apart, to look again when the service is not up yet.
 *
 * Starting the app before the service is an ordinary thing to do, and without a
 * few quiet attempts the window would say it could not reach the service and stay
 * that way even after the service came back. The reader has a button for
 * everything else.
 */
const RETRY_ATTEMPTS = 5;
const RETRY_DELAY_MS = 2_000;
let retriesLeft = RETRY_ATTEMPTS;
let retryTimer: ReturnType<typeof setTimeout> | undefined;

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  projects: [],
  current: null,
  loading: false,
  busy: false,
  error: null,
  dialog: null,

  refresh: async () => {
    set({ loading: true });
    try {
      const projects = await fetchProjects();
      const active = projects.find((project) => project.active)?.name ?? null;
      // A list that answered is the whole answer: the project that was open is
      // kept only while it is still in it. The last project being removed leaves
      // nothing selected, which is what lets the workspace offer to begin again
      // instead of asking a project that is gone.
      const kept = projects.some((project) => project.name === get().current)
        ? get().current
        : null;
      retriesLeft = RETRY_ATTEMPTS;
      if (retryTimer !== undefined) {
        clearTimeout(retryTimer);
        retryTimer = undefined;
      }
      set({ projects, current: active ?? kept, loading: false, error: null });
    } catch (err) {
      set({
        loading: false,
        error: errorMessage(err, i18n.t("error.loadProjects")),
      });
      // Only a service that is not there: a refusal is an answer, and repeating the
      // question would not change it.
      if (err instanceof ApiError && err.status === UNREACHED_STATUS && retriesLeft > 0) {
        retriesLeft -= 1;
        if (retryTimer !== undefined) clearTimeout(retryTimer);
        retryTimer = setTimeout(() => {
          retryTimer = undefined;
          void get().refresh();
        }, RETRY_DELAY_MS);
      }
    }
  },

  select: async (name) => {
    if (!name || name === get().current) return;
    set({ busy: true, error: null });
    try {
      await switchToProject(name);
      set({ busy: false });
      await get().refresh();
    } catch (err) {
      set({ busy: false, error: errorMessage(err, i18n.t("error.switchProject")) });
    }
  },

  create: async (name) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    set({ busy: true, error: null });
    try {
      await createProject(trimmed);
      await switchToProject(trimmed);
      set({ busy: false });
      await get().refresh();
    } catch (err) {
      set({ busy: false, error: errorMessage(err, i18n.t("error.createProject")) });
    }
  },

  openExisting: async (path, mode, name = "") => {
    const trimmed = path.trim();
    if (!trimmed) return null;
    set({ busy: true, error: null });
    try {
      const opened = await openFolder(trimmed, mode, name.trim());
      set({ busy: false });
      await get().refresh();
      return opened;
    } catch (err) {
      set({ busy: false, error: errorMessage(err, i18n.t("error.openFolder")) });
      return null;
    }
  },

  remove: async (name, confirmName) => {
    set({ busy: true, error: null });
    try {
      await deleteProject(name, confirmName);
      // The project is gone, so what was remembered about its sources and about
      // the documents it had open is meaningless and would otherwise stay in the
      // browser forever.
      forgetProjectIndex(name);
      forgetProjectReading(name);
      set({ busy: false });
      await get().refresh();
    } catch (err) {
      set({ busy: false, error: errorMessage(err, i18n.t("error.deleteProject")) });
    }
  },

  clearError: () => set({ error: null }),

  setDialog: (dialog) => set({ dialog }),
}));
