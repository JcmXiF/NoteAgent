import { create } from "zustand";
import { planTopicMap } from "@/lib/api/plan";
import { runAsActivity } from "@/lib/run";
import { ShownError } from "@/lib/api/client";
import { collectionsByScope } from "@/lib/clips";
import {
  MissingSourcesError,
  SourceNotStoredError,
  ensureSourcesOnDisk,
  missingSourceLabels,
} from "@/lib/persist-sources";
import { useAcquireStore } from "@/stores/acquire-store";
import { useClipsStore } from "@/stores/clips-store";
import { useDeepSearchStore } from "@/stores/deep-search-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import i18n from "@/i18n";

/**
 * Working out which pages to write.
 *
 * Kept out of the panel because the inbox needs the same action: revising one
 * source after it changed plans that source alone, and it has to go through the
 * identical path rather than a second implementation.
 */
type PlanState = {
  running: boolean;
  /**
   * Runs the plan. With `sourceIdentity`, only the selections from that source
   * are planned — which is what revising a single changed source means.
   */
  run: (options?: { sourceIdentity?: string }) => Promise<void>;
};

export const usePlanStore = create<PlanState>((set, get) => ({
  running: false,

  run: async (options) => {
    const project = useWorkspaceStore.getState().current;
    if (!project) return;

    const clips = useClipsStore.getState();
    const scope = options?.sourceIdentity?.replace(/\\/g, "/").trim() ?? "";
    const round = useDeepSearchStore.getState().session;
    // Which selections this pass plans. A round that has reached the planning step
    // is planned from its own selections, and the everyday ones are planned from
    // the rest — the same split the core makes, so both sides agree on what a
    // planning pass is looking at.
    const inRound = Boolean(round?.roundId) && (round?.step ?? 0) >= 4;
    const scopeOf = collectionsByScope(clips.collections, round?.roundId ?? "");
    const targeted = (inRound ? scopeOf.mine : scopeOf.everyday).filter(
      (item) => !scope || item.sourceKey.replace(/\\/g, "/").trim() === scope,
    );
    if (targeted.length === 0) return;

    // A round carries its own intent; the everyday pass uses what the reader typed.
    const intent = inRound ? (round?.intent ?? "").trim() : clips.intent.trim();
    set({ running: true });

    const work = () =>
      runAsActivity({
        kind: "plan",
        title: intent || i18n.t("plan.title"),
        module: "docs",
        message: i18n.t("plan.working"),
        retry: () => void get().run(options),
        doneMessage: () => i18n.t("plan.title"),
        run: async (ctx) => {
          const captures = useAcquireStore
            .getState()
            .entries.map((entry) => entry.capture)
            .filter((capture): capture is NonNullable<typeof capture> =>
              Boolean(capture),
            );

          if (targeted.some((item) => item.sourceKey.startsWith("preview:"))) {
            ctx.status(i18n.t("plan.storing"));
            try {
              const stored = await ensureSourcesOnDisk({
                project,
                collections: targeted,
                captures,
              });
              if (stored.wrote > 0) {
                await useClipsStore.getState().remapSourceKeys(stored.remaps);
                useAcquireStore.getState().recordWrittenPaths(stored.writtenPaths);
              }
            } catch (err) {
              if (err instanceof MissingSourcesError) {
                throw new ShownError(
                  i18n.t("selections.missingSources", {
                    labels: missingSourceLabels(err),
                  }),
                );
              }
              if (err instanceof SourceNotStoredError) {
                if (err.detail) ctx.note(err.detail);
                throw new ShownError(i18n.t("selections.storeFailed"));
              }
              throw err;
            }
          }

          // The plan is decided against what the board says, so anything the reader
          // has just answered has to be stored before it is run.
          await useClipsStore.getState().persistNow();
          ctx.status(i18n.t("plan.planProgress"));
          const map = await planTopicMap(project, {
            intent,
            ...(scope ? { sourceIdentity: scope } : {}),
            onStatus: (message, stepId) => {
              // Developer wording from the backend goes to the log, not the
              // screen; the progress sentence is the frontend's.
              if (message) ctx.note(message);
              ctx.status(i18n.t("plan.planProgress"), stepId ?? "plan");
            },
            signal: ctx.signal,
          });
          if (inRound) {
            // A search keeps one list of pages: its plan refines that list, so the
            // panel reads the search rather than a second copy of the plan.
            await useDeepSearchStore.getState().refresh(project);
          } else {
            // The document holds the board this plan produced — the pages it
            // answered for, merged with the ones it did not touch — so it is what
            // the panel reads, not the answer on its own.
            await useClipsStore.getState().reload();
          }
          return map;
        },
      });

    try {
      await work();
    } catch {
      // Reported on the job already.
    } finally {
      set({ running: false });
    }
  },
}));
