import { create } from "zustand";
import { fetchDeepSearch, streamDeepSearchAct } from "@/lib/api/deep-search";
import {
  currentStepKey,
  progressForAction,
  truncationMessages,
  type DeepSearchActBody,
} from "@/lib/deep-search";
import { errorMessage, ShownError } from "@/lib/api/client";
import { MAX_FETCH_CARDS, postAcquireFetch } from "@/lib/api/acquire";
import { cardFromSearchResult } from "@/lib/capture";
import type { DeepSearchSession } from "@/lib/api/types";
import { useAcquireStore } from "@/stores/acquire-store";
import { useActivityStore } from "@/stores/activity-store";
import { useClipsStore } from "@/stores/clips-store";
import { runAsActivity } from "@/lib/run";
import { useWorkspaceStore } from "@/stores/workspace-store";
import i18n from "@/i18n";

/**
 * One round of deep search.
 *
 * The round's state lives in the core; this holds the copy the views read and the
 * actions that move it on. Every action goes through the jobs panel: a round is a
 * sequence of model calls, so it has to stay visible after the user looks
 * elsewhere, and stopping or retrying it has to be possible from one place.
 *
 * There is deliberately no `busy` flag. Whether a round is being worked on is read
 * from the jobs panel — the same place the user is looking — because a second flag
 * would be a second truth.
 */

type DeepSearchState = {
  session: DeepSearchSession | null;
  status: "idle" | "loading" | "ready" | "error";
  error: string | null;

  /** Reads the round, e.g. on arrival or after another view changed it. */
  load: (project: string) => Promise<void>;
  /**
   * Reads the round again without showing that it is loading.
   *
   * For changes made on the server's side of something else the reader did: a
   * compile advances the round once the pages are written, and the panel has to
   * notice or it keeps offering to end a round the core has already moved on.
   */
  refresh: (project: string) => Promise<void>;
  /** Applies one action, in the jobs panel. */
  act: (project: string, body: DeepSearchActBody, title?: string) => Promise<void>;
  /**
   * Applies an edit that is over as soon as it is asked for: ticking a box.
   *
   * Not a job: a tick is not work a reader waits on, and putting every one of them
   * in the panel would bury the actions that are. The rule is the panel's subject
   * matter, not the action — see the jobs panel's own notes.
   */
  edit: (project: string, body: DeepSearchActBody) => Promise<void>;
  /**
   * Brings in the material the reader ticked: the addresses the round found.
   *
   * One job for the whole round even though a request takes a limited number of
   * addresses at a time, because a reader who ticked twenty of them asked for one
   * thing rather than three.
   */
  fetchCards: (project: string) => Promise<void>;
  /** Clears everything, for a project change. */
  reset: () => void;
};

function roundTitle(mode: string): string {
  return mode === "chew" ? i18n.t("deepSearch.title.chew") : i18n.t("deepSearch.title.explore");
}

/**
 * Reads the round from the core.
 *
 * `quiet` is for a re-read after a job ended badly: the panel is already showing
 * something, and blinking it through a loading state in order to say the same
 * thing again would be worse than leaving it alone.
 */
async function read(
  set: (patch: Partial<DeepSearchState>) => void,
  project: string,
  quiet: boolean,
): Promise<void> {
  if (!project) return;
  if (!quiet) set({ status: "loading" });
  try {
    const session = await fetchDeepSearch(project);
    set({ session, status: "ready", error: null });
  } catch (err) {
    set({ status: "error", error: errorMessage(err, i18n.t("deepSearch.loadFailed")) });
  }
}

export const useDeepSearchStore = create<DeepSearchState>((set, get) => ({
  session: null,
  status: "idle",
  error: null,

  load: async (project) => {
    await read(set, project, false);
  },

  refresh: async (project) => {
    await read(set, project, true);
  },

  act: async (project, body, title) => {
    const before = get().session;
    const mode = String(body.mode ?? before?.mode ?? "").trim();
    // What to say comes from the action rather than from the core's report: the
    // core's wording is written for the log, and this side knows exactly which
    // action is running.
    const progress = progressForAction(body.action);
    const message = progress ? i18n.t(progress.key) : "";

    const work = () =>
      runAsActivity({
        kind: "deep-search",
        title: title || roundTitle(mode),
        module: "graph",
        message,
        // A finished job cannot say where the round now stands — it is the round that
        // carries on — so the sentence it ends with is the round's own step, or that
        // the round is over when the action was the one that ended it.
        doneMessage: (result: DeepSearchSession) => {
          const key = currentStepKey(result);
          return key
            ? i18n.t("deepSearch.atStep", { step: i18n.t(key) })
            : i18n.t("deepSearch.ended");
        },
        retry: () => void get().act(project, body, title),
        run: async (ctx) => {
          ctx.status(message);
          let session: DeepSearchSession;
          try {
            session = await streamDeepSearchAct(project, body, {
              onStatus: (backendMessage, stepId) => {
                // The core's sentence goes to the log, with the step it named: that
                // wording is written for whoever reads the log, not for the screen.
                if (backendMessage) {
                  ctx.note(stepId ? `${backendMessage} [${stepId}]` : backendMessage);
                }
                ctx.status(message);
              },
              signal: ctx.signal,
            });
          } catch (err) {
            // Let a stop come through as a stop rather than as a failure.
            if (ctx.signal.aborted) throw err;
            // The core refuses a step it is not on, which is the case a reader is
            // most likely to meet. When it named the situation, the screen says
            // that; otherwise its words go to the log and this round's own
            // sentence is what the reader gets.
            ctx.note(err instanceof Error ? err.message : String(err));
            throw new ShownError(errorMessage(err, i18n.t("deepSearch.cannotNow")));
          }
          set({ session, status: "ready", error: null });
          // The round's selections live on the server, and the core may have made,
          // dropped or re-filed some of them. Reading them again is what keeps the
          // panel and the selections panel telling the same story — the panel decides
          // what to offer from what is selected, so a stale count would offer an
          // action the core then refuses.
          if (COLLECTION_ACTIONS.has(body.action)) {
            await useClipsStore.getState().reload();
          }
          // What the core wants to say about the step it reached is for the log:
          // it is written for whoever reads the log, and the screen has its own
          // sentences.
          if (session.notice) ctx.note(session.notice);
          // Anything the core left out is written to the job rather than shown as a
          // footnote: a truncated working set has to be visible somewhere.
          for (const told of truncationMessages(session)) {
            ctx.note(i18n.t(told.key, told.params));
          }
          return session;
        },
      });

    try {
      await work();
    } catch {
      // Stopping the reader takes effect before the core's next model round, not in
      // the middle of one, so the action that was running may well have finished.
      // Reading the round again is what keeps the panel showing what the core did
      // rather than what the reader expected it to do.
      await read(set, project, true);
    }
  },

  edit: async (project, body) => {
    const seq = ++edits;
    try {
      const session = await streamDeepSearchAct(project, body);
      // Two ticks in a row can come back in either order, and the older answer
      // would put back what the newer one changed. Only the newest may write.
      if (seq !== edits) return;
      set({ session, status: "ready", error: null });
    } catch (err) {
      if (seq !== edits) return;
      // The round is untouched by a failed tick, so the panel keeps it and says
      // what went wrong beside it.
      set({ error: errorMessage(err, i18n.t("deepSearch.cannotNow")) });
    }
  },

  fetchCards: async (project) => {
    const session = get().session;
    const picked = (session?.cards ?? []).filter((card) => card.picked && card.url);
    if (picked.length === 0) return;
    // A request takes a limited number of addresses; a round can carry more than
    // that, so the addresses go in batches under one job.
    const batches: (typeof picked)[] = [];
    for (let i = 0; i < picked.length; i += MAX_FETCH_CARDS) {
      batches.push(picked.slice(i, i + MAX_FETCH_CARDS));
    }
    const total = picked.length;

    const work = () =>
      runAsActivity({
        kind: "fetch",
        title: i18n.t("deepSearch.cards.jobTitle", { count: total }),
        module: "sources",
        message: i18n.t("deepSearch.cards.fetching", { done: 0, total }),
        retry: () => void get().fetchCards(project),
        doneMessage: (count) => i18n.t("deepSearch.cards.fetched", { count }),
        run: async (ctx) => {
          let done = 0;
          for (const batch of batches) {
            ctx.status(i18n.t("deepSearch.cards.fetching", { done, total }));
            const data = await postAcquireFetch(
              project,
              batch.map((card) => card.url),
              session?.intent ?? "",
              { signal: ctx.signal },
            );
            const count = data.captures?.length ?? 0;
            // Taken in as each batch arrives, so stopping part-way keeps what has
            // already been read rather than throwing it away.
            useAcquireStore.getState().acceptCaptures(data.captures ?? [], {
              cards: batch.map(cardFromSearchResult),
            });
            done += count;
          }
          return done;
        },
      });

    try {
      await work();
    } catch {
      // The job says what happened; what came in before the failure is already in
      // the workbench, which is where it would have gone anyway.
    }
  },

  reset: () => set({ session: null, status: "idle", error: null }),
}));

/**
 * Actions that change the round's selections, which the core keeps rather than the
 * session: making them, dropping them, or re-filing them as ordinary selections.
 */
const COLLECTION_ACTIONS = new Set([
  "acceptBlocks",
  "enterPlan",
  "redo",
  "restart",
  "end",
  "clear",
]);

/**
 * Which edit is the newest.
 *
 * Ticks are sent as they are clicked rather than queued, so their answers can
 * arrive in any order; this is what lets an older answer be recognised and
 * dropped instead of writing over a newer one.
 */
let edits = 0;

/**
 * Whether a round is being worked on, read from the jobs panel rather than kept as
 * a flag of its own.
 */
export function useDeepSearchRunning(): boolean {
  return useActivityStore((state) =>
    state.items.some((item) => item.kind === "deep-search" && item.phase === "running"),
  );
}

/**
 * Follows the active project, the way the other stores do, so a round that is
 * running is shown when the view is first opened rather than after a blank moment.
 */
let started = false;

export function startDeepSearchSync(): void {
  if (started) return;
  started = true;

  useWorkspaceStore.subscribe((state, previous) => {
    if (state.current === previous.current) return;
    const store = useDeepSearchStore.getState();
    if (state.current) void store.load(state.current);
    else store.reset();
  });

  const current = useWorkspaceStore.getState().current;
  if (current) void useDeepSearchStore.getState().load(current);
}
