import i18n from "@/i18n";
import { create } from "zustand";
import { fetchClips, putClips } from "@/lib/api/clips";
import type { DerivedBlock, TopicMap } from "@/lib/api/types";
import { errorMessage } from "@/lib/api/client";
import {
  blocksById,
  boardDecisions,
  clipsPayloadFrom,
  decisionsFromPayload,
  planMarqueeClip,
  uniqueBlockIds,
  type BlockCollection,
  type TopicDecision,
} from "@/lib/clips";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * The passages selected for compilation, the stated intent, the proposed plan
 * and the user's confirmations.
 *
 * Storage is the backend document; this store mirrors it and writes back on
 * change. Three properties matter and are the reason this is not just a load/save
 * pair:
 *
 *  - A change to the plan or to the selection invalidates the previous plan and
 *    its confirmations. Keeping them would let a compile run against a plan that
 *    no longer matches what is selected.
 *  - The reader's own answers are kept across such a change, so re-planning asks
 *    about the pages instead of making someone answer them again. They travel to
 *    the backend as `choices`, apart from the confirmations.
 *  - Confirmations are only persisted once the user has actually changed one.
 *    Until then an empty list is sent, so the backend can tell "nobody has been
 *    through the gate" apart from "these pages were confirmed". The defaults
 *    shown while planning are a suggestion, not consent.
 */
const DEBOUNCE_MS = 280;

function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `col-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

type ClipCollectionInput = {
  sourceKey: string;
  sourceLabel: string;
  blockIds: string[];
  blocks: DerivedBlock[];
  roundId?: string;
  targetSlug?: string;
};

type ClipsState = {
  project: string | null;
  hydrated: boolean;
  loading: boolean;
  error: string | null;

  collections: BlockCollection[];
  focusedId: string | null;
  intent: string;
  topicMap: TopicMap | null;
  decisions: Record<string, TopicDecision>;
  decisionsTouched: boolean;

  hydrate: (project: string) => Promise<void>;
  reload: () => Promise<void>;
  reset: () => void;

  setIntent: (value: string) => void;
  setDecision: (slug: string, patch: Partial<TopicDecision>) => void;

  addCollection: (input: ClipCollectionInput) => void;
  removeCollection: (id: string) => void;
  removeSource: (sourceKey: string) => void;
  clearAll: () => void;
  focusCollection: (id: string | null) => void;
  /**
   * Says which page a selection is meant for, or clears it with an empty slug.
   *
   * This is the reader taking charge of the page: it becomes a page the planning
   * pass is about, whether or not a search proposed it first.
   */
  setTarget: (id: string, slug: string) => void;

  /**
   * Adopts the selections left behind by a round that is no longer running: they
   * stop naming a round, so the everyday Planning pass can use them.
   *
   * Only ever called once that round is over. While it is running, the round's own
   * bookkeeping refers to these selections by id, and clearing the name would leave
   * the round pointing at material it can no longer find.
   */
  releaseRound: (roundId: string) => void;
  /** Drops the selections left behind by a round that is no longer running. */
  dropRound: (roundId: string) => void;

  /** Rewrites the stored keys of the given selections, then writes immediately. */
  remapSourceKeys: (
    pairs: Array<{ fromKey: string; toKey: string; label?: string }>,
  ) => Promise<void>;
  /** Writes the current state now, cancelling any pending debounce. */
  persistNow: () => Promise<void>;
};

/**
 * Write-back scheduling.
 *
 * Mutations schedule a write rather than writing inline, so a burst of edits
 * becomes one request. A confirmation change asks for an immediate write, which
 * always wins over a pending debounce: the earliest deadline is kept.
 */
let timer: ReturnType<typeof setTimeout> | null = null;
let timerDeadline = 0;
/** Incremented whenever the document is replaced wholesale (load, reset). */
let epoch = 0;
let inFlight: Promise<void> | null = null;

function clearTimer(): void {
  if (timer !== null) clearTimeout(timer);
  timer = null;
  timerDeadline = 0;
}

function cancelPending(): void {
  clearTimer();
  epoch += 1;
}

export const useClipsStore = create<ClipsState>((set, get) => {
  function currentPayload() {
    const state = get();
    if (!state.project) return null;
    return clipsPayloadFrom({
      intent: state.intent,
      collections: state.collections,
      topicMap: state.topicMap,
      decisions: state.decisions,
      decisionsTouched: state.decisionsTouched,
    });
  }

  async function write(): Promise<void> {
    const project = get().project;
    const body = currentPayload();
    if (!project || !body) return;
    const at = epoch;
    try {
      await putClips(project, body);
      // The document was replaced while this was in flight; the newer state
      // has to go out too, or the backend would keep the stale version.
      if (at !== epoch && get().project === project) {
        await write();
      }
    } catch (err) {
      // Local state stays as the user left it; the next edit retries. Reports
      // only, so a failed save cannot silently look like a successful one.
      set({ error: errorMessage(err, i18n.t("selections.saveFailed")) });
    }
  }

  function schedule(delay: number): void {
    if (!get().project || !get().hydrated) return;
    const deadline = Date.now() + delay;
    if (timer !== null && timerDeadline <= deadline) return;
    clearTimer();
    timerDeadline = deadline;
    timer = setTimeout(() => {
      timer = null;
      timerDeadline = 0;
      inFlight = write().finally(() => {
        inFlight = null;
      });
    }, delay);
  }

  /**
   * Any change to the selection or the plan discards the plan and its
   * confirmations. The reader's own answers stay: they are about the pages, and a
   * later plan carries them over instead of asking again.
   */
  function invalidatePlan() {
    set({ topicMap: null, decisionsTouched: false });
  }

  function applyHydration(project: string, payload: Awaited<ReturnType<typeof fetchClips>>) {
    const map = payload.topicMap ?? null;
    const gated = Object.keys(decisionsFromPayload(payload.decisions)).length > 0;
    set({
      project,
      collections: (payload.collections ?? []).map((item) => ({
        ...item,
        project,
        roundId: item.roundId ?? "",
        targetSlug: item.targetSlug ?? "",
      })),
      intent: payload.intent ?? "",
      topicMap: map,
      decisions: boardDecisions(map, decisionsFromPayload(payload.choices)),
      decisionsTouched: gated,
      focusedId: null,
      hydrated: true,
      loading: false,
      error: null,
    });
  }

  return {
    project: null,
    hydrated: false,
    loading: false,
    error: null,

    collections: [],
    focusedId: null,
    intent: "",
    topicMap: null,
    decisions: {},
    decisionsTouched: false,

    hydrate: async (project) => {
      if (!project) return;
      if (get().project === project && get().hydrated) return;
      cancelPending();
      set({ loading: true, hydrated: false });
      try {
        const payload = await fetchClips(project);
        // A newer project may have been selected while this was loading.
        if (useWorkspaceStore.getState().current !== project) return;
        applyHydration(project, payload);
      } catch (err) {
        if (useWorkspaceStore.getState().current !== project) return;
        set({
          project,
          collections: [],
          intent: "",
          topicMap: null,
          decisions: {},
          decisionsTouched: false,
          focusedId: null,
          hydrated: true,
          loading: false,
          error: errorMessage(err, i18n.t("selections.readFailed")),
        });
      }
    },

    reload: async () => {
      const project = get().project;
      if (!project) return;
      cancelPending();
      try {
        const payload = await fetchClips(project);
        if (get().project !== project) return;
        applyHydration(project, payload);
      } catch (err) {
        set({ error: errorMessage(err, i18n.t("selections.readFailed")) });
      }
    },

    reset: () => {
      cancelPending();
      set({
        project: null,
        hydrated: false,
        loading: false,
        error: null,
        collections: [],
        focusedId: null,
        intent: "",
        topicMap: null,
        decisions: {},
        decisionsTouched: false,
      });
    },

    setIntent: (value) => {
      set({ intent: value });
      schedule(DEBOUNCE_MS);
    },

    setDecision: (slug, patch) => {
      set((state) => {
        const previous = state.decisions[slug] ?? {
          slug,
          action: "skip" as const,
          sectionIds: [],
        };
        return {
          decisions: { ...state.decisions, [slug]: { ...previous, ...patch, slug } },
          decisionsTouched: true,
        };
      });
      // Confirmations are the human gate; they go out straight away.
      schedule(0);
    },

    addCollection: (input) => {
      const project = get().project;
      if (!project) return;
      const blockIds = uniqueBlockIds(input.blockIds);
      if (blockIds.length === 0) return;

      const plan = planMarqueeClip(
        blockIds,
        get().collections,
        input.sourceKey,
        input.blocks.map((block) => block.id),
        input.roundId ?? "",
      );
      if (plan.action === "empty") return;
      if (plan.action === "noop") {
        set({ focusedId: plan.focusId });
        return;
      }

      if (plan.action === "create") {
        const id = newId();
        set((state) => ({
          collections: [
            ...state.collections,
            {
              id,
              project,
              sourceKey: input.sourceKey,
              sourceLabel: input.sourceLabel,
              blockIds: plan.blockIds,
              blocks: blocksById(plan.blockIds, [input.blocks]),
              roundId: input.roundId,
              targetSlug: input.targetSlug,
            },
          ],
          focusedId: id,
        }));
      } else {
        const drop = new Set(plan.dropIds);
        set((state) => {
          const survivors = state.collections.filter((item) => !drop.has(item.id));
          // Overlapping selections merge, so the surviving entry has to pick up
          // the blocks of the ones it absorbed.
          const absorbed = state.collections
            .filter((item) => item.id === plan.keepId || drop.has(item.id))
            .map((item) => item.blocks);
          return {
            collections: survivors.map((item) =>
              item.id !== plan.keepId
                ? item
                : {
                    ...item,
                    sourceLabel: input.sourceLabel || item.sourceLabel,
                    blockIds: plan.blockIds,
                    blocks: blocksById(plan.blockIds, [...absorbed, input.blocks]),
                  },
            ),
            focusedId: plan.keepId,
          };
        });
      }

      invalidatePlan();
      schedule(DEBOUNCE_MS);
    },

    removeCollection: (id) => {
      set((state) => ({
        collections: state.collections.filter((item) => item.id !== id),
        focusedId: state.focusedId === id ? null : state.focusedId,
      }));
      invalidatePlan();
      schedule(DEBOUNCE_MS);
    },

    removeSource: (sourceKey) => {
      const focused = get().collections.find((item) => item.id === get().focusedId);
      set((state) => ({
        collections: state.collections.filter((item) => item.sourceKey !== sourceKey),
        focusedId: focused?.sourceKey === sourceKey ? null : state.focusedId,
      }));
      invalidatePlan();
      schedule(DEBOUNCE_MS);
    },

    clearAll: () => {
      set({ collections: [], focusedId: null });
      invalidatePlan();
      schedule(DEBOUNCE_MS);
    },

    focusCollection: (id) => set({ focusedId: id }),

    setTarget: (id, slug) => {
      const target = slug.trim();
      const current = get().collections.find((item) => item.id === id);
      if (!current || (current.targetSlug ?? "") === target) return;
      set((state) => ({
        collections: state.collections.map((item) =>
          item.id === id ? { ...item, targetSlug: target } : item,
        ),
      }));
      // Which pages a pass is about decides what its plan is a plan of, so the
      // previous plan is no longer that plan.
      invalidatePlan();
      schedule(DEBOUNCE_MS);
    },

    releaseRound: (roundId) => {
      const owner = roundId.trim();
      if (!owner) return;
      set((state) => ({
        collections: state.collections.map((item) =>
          item.roundId === owner ? { ...item, roundId: "", targetSlug: "" } : item,
        ),
      }));
      // Their place in the plan has changed, so it is no longer the plan for them.
      invalidatePlan();
      schedule(DEBOUNCE_MS);
    },

    dropRound: (roundId) => {
      const owner = roundId.trim();
      if (!owner) return;
      set((state) => {
        const kept = state.collections.filter((item) => item.roundId !== owner);
        const focused = state.collections.find((item) => item.id === state.focusedId);
        return {
          collections: kept,
          focusedId: focused?.roundId === owner ? null : state.focusedId,
        };
      });
      invalidatePlan();
      schedule(DEBOUNCE_MS);
    },

    remapSourceKeys: async (pairs) => {
      const project = get().project;
      if (!project) return;
      const remap = new Map(
        pairs
          .filter((pair) => pair.toKey && pair.fromKey !== pair.toKey)
          .map((pair) => [pair.fromKey, pair]),
      );
      if (remap.size === 0) return;

      // The stored keys have changed, which is exactly what the next write must
      // record, so this bypasses the debounce instead of racing it.
      cancelPending();
      set((state) => ({
        collections: state.collections.map((item) => {
          const hit = remap.get(item.sourceKey);
          return hit
            ? { ...item, sourceKey: hit.toKey, sourceLabel: hit.label || item.sourceLabel }
            : item;
        }),
      }));
      await write();
    },

    persistNow: async () => {
      clearTimer();
      if (inFlight) await inFlight;
      await write();
    },
  };
});

/**
 * Keeps the selections in step with the active project.
 *
 * Called once at startup rather than from a component, so no view has to
 * remember to do it and switching projects cannot leave stale selections behind.
 */
let syncStarted = false;

export function startClipsSync(): void {
  if (syncStarted) return;
  syncStarted = true;

  useWorkspaceStore.subscribe((state, previous) => {
    if (state.current === previous.current) return;
    const clips = useClipsStore.getState();
    if (state.current) void clips.hydrate(state.current);
    else clips.reset();
  });

  const current = useWorkspaceStore.getState().current;
  if (current) void useClipsStore.getState().hydrate(current);
}
