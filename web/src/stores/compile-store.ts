import { create } from "zustand";
import { compileConfirmed } from "@/lib/api/plan";
import type { ConfirmedPage, IngestDone } from "@/lib/api/types";
import { ShownError, errorMessage } from "@/lib/api/client";
import { fetchIngestRecovery } from "@/lib/api/inbox";
import { runAsActivity } from "@/lib/run";
import { roundFromIngestDone, roundFromRecord, type CompileRound } from "@/lib/compile-round";
import {
  MissingSourcesError,
  SourceNotStoredError,
  ensureSourcesOnDisk,
  missingSourceLabels,
} from "@/lib/persist-sources";
import { useAcquireStore } from "@/stores/acquire-store";
import { useClipsStore } from "@/stores/clips-store";
import { useDeepSearchStore } from "@/stores/deep-search-store";
import { useDocsStore } from "@/stores/docs-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import i18n from "@/i18n";

/**
 * Writing pages out.
 *
 * The action lives here rather than in the panel because there are two ways in:
 * the button, and resuming a compile that was confirmed but interrupted. Both
 * have to do exactly the same thing, and the result has to outlive a re-render
 * so the list of written files is still there while the user looks at them.
 *
 * It also outlives the window: the round that finished is kept by the project, so
 * opening it again reads that one back instead of starting blank — and switching
 * projects reads that project's, not the one left over from the last.
 */
type CompileState = {
  running: boolean;
  /** The round to report on: the one just made, or the one the project kept. */
  round: CompileRound | null;
  /**
   * Why the round on screen is not the one that was asked for.
   *
   * A compile that did not finish must not leave the previous round looking like
   * its result: "nothing was written" and "what you see was written last time"
   * are different answers, and only one of them is true.
   */
  error: string | null;
  /** The last round asked for, so a failure can be tried again as it was. */
  last: CompileInput | null;
  run: (input: CompileInput) => Promise<void>;
  /** Runs the round that failed again, with the same pages and intent. */
  retry: () => void;
  /** Reads back the last round this project finished. */
  load: (project: string) => Promise<void>;
  reset: () => void;
};

type CompileInput = {
  confirmed: ConfirmedPage[];
  intent: string;
  /** Whether the panel should offer to try again if it fails. */
  retryable?: boolean;
};

export const useCompileStore = create<CompileState>((set, get) => ({
  running: false,
  round: null,
  error: null,
  last: null,

  reset: () => set({ round: null, error: null, last: null }),

  retry: () => {
    const last = get().last;
    if (last) void get().run(last);
  },

  load: async (project) => {
    try {
      const payload = await fetchIngestRecovery(project);
      // A newer project may have been selected while this was loading.
      if (useWorkspaceStore.getState().current !== project) return;
      set({ round: roundFromRecord(payload.last), error: null });
    } catch (err) {
      // The panel then reports nothing rather than something untrue — and says
      // that it could not read the round, so the blank is not mistaken for one.
      if (useWorkspaceStore.getState().current !== project) return;
      set({
        round: null,
        error: errorMessage(err, i18n.t("compile.roundUnavailable")),
      });
    }
  },

  run: async (input) => {
    const { confirmed, intent, retryable = true } = input;
    const project = useWorkspaceStore.getState().current;
    if (!project) return;
    const collections = useClipsStore.getState().collections;

    // The previous round stops being the answer the moment another is asked for.
    set({ running: true, error: null, round: null, last: input });

    const work = () =>
      runAsActivity({
        kind: "compose",
        title: i18n.t("compile.title"),
        module: "docs",
        message: i18n.t("compile.working"),
        steps: [
          { id: "plan", label: i18n.t("compile.track.plan"), state: "done" },
          { id: "compose", label: i18n.t("compile.track.compile"), state: "current" },
        ],
        retry: retryable ? () => void get().run({ confirmed, intent }) : undefined,
        doneMessage: (payload: IngestDone) =>
          i18n.t("compile.done", { count: payload.results.length }),
        filesWritten: (payload: IngestDone) =>
          payload.results.flatMap((item) => item.written.map((file) => file.path)),
        run: async (ctx) => {
          const captures = useAcquireStore
            .getState()
            .entries.map((entry) => entry.capture)
            .filter((capture): capture is NonNullable<typeof capture> =>
              Boolean(capture),
            );

          // A page's sources have to point at a file that exists, so anything
          // not stored yet is stored before anything is written.
          if (collections.some((item) => item.sourceKey.startsWith("preview:"))) {
            ctx.status(i18n.t("plan.storing"));
            try {
              const stored = await ensureSourcesOnDisk({
                project,
                collections,
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

          // The decisions on screen are the input, so they are recorded before
          // the compile reads them.
          await useClipsStore.getState().persistNow();

          ctx.status(i18n.t("compile.composing"));
          const payload = await compileConfirmed(
            project,
            { intent, confirmed },
            {
              onStatus: (message, stepId) => {
                // The backend names internal stages, so its wording belongs in
                // the log rather than on screen.
                if (message) ctx.note(message);
                ctx.status(i18n.t("compile.composing"), stepId ?? "compose");
              },
              signal: ctx.signal,
            },
          );
          set({ round: roundFromIngestDone(payload, intent) });

          for (const item of payload.results) {
            const doctor = item.doctor;
            if (doctor && (doctor.errors > 0 || doctor.warnings > 0)) {
              ctx.note(
                i18n.t("compile.checksSummary", {
                  errors: doctor.errors,
                  warnings: doctor.warnings,
                }),
              );
            }
          }

          // The backend records what this round consumed, so the document is
          // re-read rather than left to be written back over later.
          await useClipsStore.getState().reload();
          // Writing pages adds pages: without re-reading the tree, the file list
          // does not know them, and asking to open one of them names a path the
          // reader cannot reach.
          await useDocsStore.getState().refresh();
          // Writing the pages also moves the search that asked for them on: the
          // core records what was consumed, and the panel would otherwise still be
          // offering to finish a search that has already got there.
          await useDeepSearchStore.getState().refresh(project);

          if (payload.errors.length > 0) {
            // The written files are already on screen; the sentence only has to
            // say that some of the pages did not make it. The core's own wording,
            // with paths, belongs in the job's log.
            throw new ShownError(
              i18n.t("compile.someFailed", { count: payload.errors.length }),
            );
          }
          return payload;
        },
      });

    try {
      await work();
    } catch (err) {
      // Reported on the job as well; this is what keeps the panel from showing the
      // last round as though it were this one.
      set({ error: errorMessage(err, i18n.t("compile.runFailed")) });
    } finally {
      set({ running: false });
    }
  },
}));

/**
 * Follows the active project, the way the other stores do, so the round this
 * project finished last is on screen when its panel is opened rather than a
 * blank panel where the files should be.
 */
let started = false;

export function startCompileSync(): void {
  if (started) return;
  started = true;

  useWorkspaceStore.subscribe((state, previous) => {
    if (state.current === previous.current) return;
    const store = useCompileStore.getState();
    if (state.current) void store.load(state.current);
    else store.reset();
  });

  const current = useWorkspaceStore.getState().current;
  if (current) void useCompileStore.getState().load(current);
}
