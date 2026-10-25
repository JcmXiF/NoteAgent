import { create } from "zustand";
import {
  postAcquireFetch,
  postAcquireFromLocal,
  postAcquireFromRendered,
  postAcquireSearch,
  type LocalFilePayload,
} from "@/lib/api/acquire";
import type { CapturePreview, ResourceCard } from "@/lib/api/types";
import { cardFromCapture, captureMatches, failedCaptureHint } from "@/lib/capture";
import { errorMessage, isSseAborted } from "@/lib/api/client";
import { runAsActivity } from "@/lib/run";
import {
  clearIndex,
  entryFromCapture,
  mergeEntries,
  readIndex,
  writeIndex,
  type CaptureEntry,
} from "@/lib/acquire-index";
import { previewSourceKey } from "@/lib/clips";
import type { Message } from "@/lib/message";
import type { SourceOrigin } from "@/lib/source-origin";
import { useClipsStore } from "@/stores/clips-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import i18n from "@/i18n";

/**
 * The workbench: sources that have been brought in, and the search results that
 * can be brought in.
 *
 * `entries` is the whole list. Each one carries a full capture while it is in
 * memory, and only the small record described in `acquire-index` across reloads.
 * Callers that need extracted text filter for the captures that are present.
 */
type AcquireState = {
  project: string | null;

  entries: CaptureEntry[];
  /** Results of the last search, shown as cards the user can tick. */
  results: ResourceCard[];
  /** What was searched for; also the query the results belong to. */
  query: string;
  /**
   * What a finished search or fetch left behind, so the panel can say it: how many
   * pages came in, and which one to look at first.
   */
  lastFetch: { count: number; firstUrl: string | null } | null;
  focusedUrl: string | null;
  /**
   * Something to say about the list that is not an error, e.g. that a request to
   * show a source could not be met. Held as a `Message` so the store carries no
   * user-facing wording.
   */
  notice: Message | null;
  /**
   * Where the source the notice is about came from, when it could be worked out.
   *
   * Kept beside the notice because the two are one thing: the sentence says the page
   * is not in this list, and this is what makes it offerable rather than only
   * stated. The address comes out of the stored original's own head, so it is not
   * remembered anywhere new — and only the site is shown, since that is what tells
   * the reader what they are being offered.
   */
  noticeOrigin: SourceOrigin | null;
  error: string | null;
  busy: boolean;

  hydrate: (project: string) => void;
  reset: () => void;

  setFocusedUrl: (url: string | null) => void;
  setError: (message: string | null) => void;
  setBusy: (busy: boolean) => void;
  setNotice: (
    message: Message | null,
    origin?: SourceOrigin | null,
  ) => void;
  clearNotice: () => void;

  setResults: (cards: ResourceCard[]) => void;
  /**
   * Puts one result in the tickable list, replacing a row for the same address.
   *
   * For a result found elsewhere — a chat answer naming a page — being handed to the
   * list the reader ticks. Nothing is read here: fetching is still theirs to ask for.
   */
  addResult: (card: ResourceCard) => void;
  /** Searches the web and shows what came back as tickable cards. */
  search: (query: string) => Promise<void>;
  /** Fetches the ticked links, adding each page to the workbench. */
  fetchCards: (urls: string[]) => Promise<void>;
  /**
   * Keeps the page the reader is looking at in the built-in browser.
   *
   * The markup comes from the shell rather than from a request, because the point
   * is the page as it was rendered — after scripts ran, and with whatever the
   * reader signed in for.
   */
  takeRendered: (url: string, html: string) => Promise<void>;
  /** Adds captures, keeping any search card the user already saw for them. */
  acceptCaptures: (
    incoming: CapturePreview[],
    options?: { cards?: ResourceCard[]; notice?: Message | null },
  ) => void;
  removeCaptures: (urls: string[]) => void;
  /** Records where originals were stored, so the list can link to them. */
  recordWrittenPaths: (items: Array<{ url: string; path: string }>) => void;

  /** Fetches one link and adds the result. */
  fetchUrl: (url: string) => Promise<void>;
  /** Adds already-encoded local files. */
  addLocalFiles: (files: LocalFilePayload[]) => Promise<void>;
};

function persist(state: { project: string | null; entries: CaptureEntry[] }): void {
  if (!state.project) return;
  writeIndex(state.project, state.entries);
}

export const useAcquireStore = create<AcquireState>((set, get) => ({
  project: null,

  entries: [],
  results: [],
  query: "",
  lastFetch: null,
  focusedUrl: null,
  notice: null,
  noticeOrigin: null,
  error: null,
  busy: false,

  hydrate: (project) => {
    if (!project || get().project === project) return;
    // Live captures from the previous project are dropped; the stored record
    // for the new project is what the list shows. The first entry is focused so
    // the main area is not empty while the list is not.
    const entries = readIndex(project);
    set({
      project,
      entries,
      results: [],
      query: "",
      lastFetch: null,
      focusedUrl: entries[0]?.url ?? null,
      notice: null,
      noticeOrigin: null,
      error: null,
      busy: false,
    });
  },

  reset: () =>
    set({
      project: null,
      entries: [],
      results: [],
      query: "",
      lastFetch: null,
      focusedUrl: null,
      notice: null,
      noticeOrigin: null,
      error: null,
      busy: false,
    }),

  setFocusedUrl: (url) => set({ focusedUrl: url }),
  setError: (message) => set({ error: message }),
  setBusy: (busy) => set({ busy }),
  setNotice: (message, origin = null) => set({ notice: message, noticeOrigin: origin }),
  clearNotice: () => set({ notice: null, noticeOrigin: null }),

  setResults: (cards) => set({ results: cards }),

  addResult: (card) =>
    set((state) => {
      const kept = state.results.filter((item) => item.url !== card.url);
      return { results: [card, ...kept] };
    }),

  acceptCaptures: (incoming, options) => {
    if (incoming.length === 0) return;
    const preferred = new Map((options?.cards ?? []).map((card) => [card.url, card]));
    const entries = incoming.map((capture) =>
      entryFromCapture(capture, Date.now()),
    );

    set((state) => ({ entries: mergeEntries(state.entries, entries) }));

    // Keep the search card the user actually saw, rather than a summary built
    // from the capture, so the list does not reword itself after fetching.
    const cards = incoming.map(
      (capture) =>
        preferred.get(capture.url) ?? cardFromCapture(capture, (key) => i18n.t(key)),
    );
    set((state) => {
      const urls = new Set(cards.map((card) => card.url));
      const kept = state.results.filter(
        (card) =>
          !urls.has(card.url) &&
          !incoming.some((capture) => captureMatches(capture, card.url)),
      );
      return {
        results: [...cards, ...kept],
        focusedUrl: incoming[0]?.url ?? state.focusedUrl,
        // Bringing something in answers whatever the list was saying.
        notice: options?.notice ?? null,
        noticeOrigin: null,
      };
    });
    persist(get());
  },

  removeCaptures: (urls) => {
    if (urls.length === 0) return;
    const { entries } = get();
    const urlSet = new Set(urls);
    // A capture answers to the address it was asked for and the one it settled on.
    const matches = (entry: CaptureEntry): boolean => {
      if (urlSet.has(entry.url)) return true;
      const capture = entry.capture;
      return capture ? urls.some((url) => captureMatches(capture, url)) : false;
    };
    const removed = entries.filter(matches);

    // A selection made from a page that is no longer in the workbench would be
    // unusable, so it goes too. The `preview:` key is what the selection holds.
    const clips = useClipsStore.getState();
    for (const entry of removed) {
      clips.removeSource(previewSourceKey(entry.url));
      const finalUrl = entry.capture?.finalUrl;
      if (finalUrl && finalUrl !== entry.url) {
        clips.removeSource(previewSourceKey(finalUrl));
      }
    }

    set((state) => ({
      entries: state.entries.filter((entry) => !matches(entry)),
      results: state.results.filter((card) => !urlSet.has(card.url)),
      focusedUrl:
        state.focusedUrl && urlSet.has(state.focusedUrl) ? null : state.focusedUrl,
    }));
    persist(get());
  },

  recordWrittenPaths: (items) => {
    if (items.length === 0) return;
    const byUrl = new Map<string, string>();
    for (const item of items) {
      if (item.url && item.path) byUrl.set(item.url, item.path);
    }
    if (byUrl.size === 0) return;

    set((state) => ({
      entries: state.entries.map((entry) => {
        const path =
          byUrl.get(entry.url) ??
          (entry.capture?.finalUrl ? byUrl.get(entry.capture.finalUrl) : undefined);
        return path ? { ...entry, path } : entry;
      }),
    }));
    persist(get());
  },

  /**
   * Both entry points run through the jobs panel, so a fetch stays visible after
   * the user moves to another view and can be stopped or tried again from there.
   * The work lives here rather than in a component so the views do not have to
   * know about it.
   */
  search: async (query) => {
    const { project, busy } = get();
    const text = query.trim();
    if (!project) {
      // Said rather than silently ignored: a search that does nothing looks like
      // a search that found nothing, and this one never ran.
      set({ error: i18n.t("project.needed") });
      return;
    }
    if (busy || !text) return;
    set({ error: null, busy: true, query: text, lastFetch: null });
    try {
      await runAsActivity({
        kind: "search",
        title: text,
        module: "browse",
        message: i18n.t("browse.search.running"),
        doneMessage: (count: number) => i18n.t("browse.search.done", { count }),
        retry: () => void get().search(text),
        run: async (ctx) => {
          const data = await postAcquireSearch(project, text, undefined, {
            signal: ctx.signal,
          });
          get().setResults(data.cards ?? []);
          return data.cards?.length ?? 0;
        },
      });
    } catch (err) {
      // A stop is not a failure, so it does not become one on screen.
      if (!isSseAborted(err)) {
        set({ error: errorMessage(err, i18n.t("browse.search.failed")) });
      }
    } finally {
      set({ busy: false });
    }
  },

  /**
   * Reads the ticked pages.
   *
   * One job for the whole selection even though it may take more than one request,
   * because the reader ticked a set of pages and asked for one thing.
   */
  fetchCards: async (urls) => {
    const { project, busy, query } = get();
    if (!project) {
      set({ error: i18n.t("project.needed") });
      return;
    }
    if (busy || urls.length === 0) return;
    set({ error: null, busy: true, lastFetch: null });
    try {
      const count = await runAsActivity({
        kind: "fetch",
        title: i18n.t("browse.fetch.title", { count: urls.length }),
        module: "sources",
        message: i18n.t("browse.fetch.running"),
        retry: () => void get().fetchCards(urls),
        doneMessage: (added: number) => i18n.t("sources.job.added", { count: added }),
        run: async (ctx) => {
          const data = await postAcquireFetch(project, urls, query, {
            signal: ctx.signal,
          });
          const captures = data.captures ?? [];
          ctx.status(i18n.t("sources.job.reading"));
          get().acceptCaptures(captures);
          set({ lastFetch: { count: captures.length, firstUrl: captures[0]?.url ?? null } });
          return captures.length;
        },
      });
      if (count === 0) set({ error: i18n.t("browse.fetch.nothing") });
    } catch (err) {
      if (!isSseAborted(err)) {
        set({ error: errorMessage(err, i18n.t("browse.fetch.failed")) });
      }
    } finally {
      set({ busy: false });
    }
  },

  /**
   * Keeps the page the shell serialized.
   *
   * The core reads the markup as it would a fetched page, minus the request: what
   * it sees is what the reader saw. Nothing is written to disk here — the originals
   * are stored when the pages are planned — so this adds the page to the workbench
   * and says where passages are chosen next.
   */
  takeRendered: async (url, html) => {
    const { project, busy } = get();
    if (!project) {
      set({ error: i18n.t("project.needed") });
      return;
    }
    if (busy) return;
    set({ error: null, busy: true, lastFetch: null });
    try {
      const count = await runAsActivity({
        kind: "import",
        title: url,
        module: "sources",
        message: i18n.t("browse.keep.running"),
        retry: () => void get().takeRendered(url, html),
        doneMessage: (added: number) => i18n.t("sources.job.added", { count: added }),
        run: async (ctx) => {
          const data = await postAcquireFromRendered(project, url, html, {
            signal: ctx.signal,
          });
          const captures = data.captures ?? [];
          ctx.status(i18n.t("sources.job.reading"));
          get().acceptCaptures(captures);
          set({ lastFetch: { count: captures.length, firstUrl: captures[0]?.url ?? url } });
          if (captures.every((capture) => capture.fidelity === "failed")) {
            // The page is in the list as a failure with its own explanation; the
            // reader is told at once rather than having to notice the card.
            const first = captures[0];
            if (first) {
              const hint = failedCaptureHint(first, true);
              set({ error: i18n.t(hint.key, hint.params) });
            }
          }
          return captures.length;
        },
      });
      if (count === 0) set({ error: i18n.t("browse.keep.nothing") });
    } catch (err) {
      if (!isSseAborted(err)) {
        set({ error: errorMessage(err, i18n.t("browse.keep.failed")) });
      }
    } finally {
      set({ busy: false });
    }
  },

  fetchUrl: async (url) => {
    const { project } = get();
    if (!project) {
      set({ error: i18n.t("project.needed") });
      return;
    }
    set({ error: null, busy: true });
    try {
      await runAsActivity({
        kind: "fetch",
        title: url,
        module: "sources",
        message: i18n.t("sources.job.fetching"),
        retry: () => void get().fetchUrl(url),
        doneMessage: (count) => i18n.t("sources.job.added", { count }),
        run: async (ctx) => {
          const data = await postAcquireFetch(project, [url], "", {
            signal: ctx.signal,
          });
          ctx.status(i18n.t("sources.job.reading"));
          get().acceptCaptures(data.captures ?? []);
          return data.captures?.length ?? 0;
        },
      });
    } catch (err) {
      if (!isSseAborted(err)) {
        set({ error: errorMessage(err, i18n.t("sources.fetchFailed")) });
      }
    } finally {
      set({ busy: false });
    }
  },

  addLocalFiles: async (files) => {
    const { project } = get();
    if (!project) {
      set({ error: i18n.t("project.needed") });
      return;
    }
    if (files.length === 0) return;
    set({ error: null, busy: true });
    const label =
      files.length === 1
        ? files[0].name
        : i18n.t("sources.job.filesCount", { count: files.length });
    try {
      await runAsActivity({
        kind: "import",
        title: label,
        module: "sources",
        message: i18n.t("sources.job.reading"),
        retry: () => void get().addLocalFiles(files),
        doneMessage: (count) => i18n.t("sources.job.added", { count }),
        run: async (ctx) => {
          const data = await postAcquireFromLocal(project, files, {
            signal: ctx.signal,
          });
          get().acceptCaptures(data.captures ?? []);
          return data.captures?.length ?? 0;
        },
      });
    } catch (err) {
      if (!isSseAborted(err)) {
        set({ error: errorMessage(err, i18n.t("sources.localFailed")) });
      }
    } finally {
      set({ busy: false });
    }
  },
}));

/* -------------------------------------------------------------- selectors */

/** The entry for a page, by the address it was asked for or the one it settled on. */
export function findEntry(
  state: AcquireState,
  url: string,
): CaptureEntry | undefined {
  return (
    state.entries.find((entry) => entry.url === url) ??
    state.entries.find((entry) =>
      entry.capture ? captureMatches(entry.capture, url) : false,
    )
  );
}

/* ------------------------------------------------------------ project sync */

/**
 * Follows the active project, the same way the selections do. Called once at
 * startup so no view has to own it.
 */
let started = false;

export function startAcquireSync(): void {
  if (started) return;
  started = true;

  useWorkspaceStore.subscribe((state, previous) => {
    if (state.current === previous.current) return;
    const acquire = useAcquireStore.getState();
    if (state.current) acquire.hydrate(state.current);
    else acquire.reset();
  });

  const current = useWorkspaceStore.getState().current;
  if (current) useAcquireStore.getState().hydrate(current);
}

/** Removes the stored list for a project, e.g. when it is deleted. */
export function forgetProjectIndex(project: string): void {
  clearIndex(project);
}
