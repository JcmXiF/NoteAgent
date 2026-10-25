import i18n from "@/i18n";
import { create } from "zustand";
import { fetchSourceCitations, fetchTree, type SourceCitations } from "@/lib/api/files";
import { errorMessage } from "@/lib/api/client";
import {
  buildProjectTree,
  decodeLinkTarget,
  linkIndex,
  resolveWikiLink,
  type TreeFile,
  type TreeNode,
} from "@/lib/project-tree";
import { readPair, writePair } from "@/lib/reading-history";
import { otherPane, type PanePlan } from "@/lib/placement";
import { rememberReading, useReadingStore } from "@/stores/reading-store";
import { useDocsReads } from "@/stores/docs-reads";
import { readableTitleFromHtml, readableTitles } from "@/lib/source-title";
import { PANE_SLOTS, useLayoutStore, type PaneSlot } from "@/stores/layout-store";
import { useNavStore } from "@/stores/nav-store";
import { useNotesStore } from "@/stores/notes-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * The documents view: the project's files, and what each pane is reading.
 *
 * A document's text is read once and kept by path, because where it is shown does
 * not change what it says — two panes showing the same source share one read.
 * What each pane has open is kept per pane, so comparing a note with the source
 * it came from means both are in front of you rather than one replacing the
 * other.
 */

/** Which list the sidebar shows. */
export type SidebarView = "files" | "knowledge";

/**
 * Where in a note to land: the heading it was written under, the selection it came
 * from, or both. Either can be missing, which is why they are separate.
 */
export type SectionTarget = {
  heading?: string;
  collectionId?: string;
};

/** A document arriving where a placement said it goes. */
type Arrival = {
  path: string;
  /** The passage to bring into view, when the request named one. */
  blockId?: string | null;
  /** Where the panes were left by the placement, and what it moved on the way. */
  plan: PanePlan;
};

type DocsState = {
  project: string | null;
  tree: TreeNode[];
  /** Every document in the project, for resolving links and checking existence. */
  files: TreeFile[];
  status: "idle" | "loading" | "ready" | "error";
  error: string | null;

  /**
   * Which notes cite which stored source, and the selections the project recorded
   * for them. Read once with the tree, because it is the same project record and
   * several views ask for it.
   */
  citations: SourceCitations | null;
  /**
   * The document each pane is showing.
   *
   * One each: the page being read and the one opened from it. Where the reader
   * has been is the history's business, not a stack of tabs per pane.
   */
  paths: Record<PaneSlot, string | null>;
  /**
   * The passage a pane was asked to bring into view, and the link target it could
   * not open. Both belong to the document in front, so they are cleared whenever
   * a different one comes forward.
   */
  focusBlockIds: Record<PaneSlot, string | null>;
  linkErrors: Record<PaneSlot, string | null>;
  /**
   * The section a pane was asked to land on in a note. Held apart from the passage
   * it was asked to show, because a note and a source name their places
   * differently, and one request is about one of them.
   */
  sectionTargets: Record<PaneSlot, SectionTarget | null>;

  /** The full tree, or just the notes. Shared: the sidebar has one of each. */
  sidebarView: SidebarView;
  setSidebarView: (view: SidebarView) => void;
  /**
   * Folders the user has closed, per view. Folders are open unless listed here,
   * because the pages are the point of this view and a tree that starts collapsed
   * makes the user open a folder before seeing anything. The two views group
   * differently — by folder in one, by type in the other — so each keeps its own
   * set of closed groups, and the sidebar has one of each.
   */
  foldedPaths: Record<SidebarView, string[]>;
  toggleFold: (view: SidebarView, path: string) => void;
  /**
   * Gives a file the name it says it has.
   *
   * A stored page carries its own title in its head, which can only be read once
   * the page has been read; until then the list has just its address. This is how
   * that name reaches the lists, the tabs and the reader's own heading, which all
   * read the file list.
   */
  setFileTitle: (path: string, title: string) => void;

  hydrate: (project: string) => Promise<void>;
  refresh: () => Promise<void>;
  /**
   * Shows a document in a pane.
   *
   * For a list that belongs to that pane — the sources of the page being read, a
   * citation row — where the reader is looking at one pane and picking from it.
   */
  open: (slot: PaneSlot, path: string, blockId?: string | null) => void;
  /**
   * Shows a document in a pane without counting as a step.
   *
   * For putting the panes back where the history says they were: that is the
   * reader returning to a place they have been, not moving to a new one.
   */
  setPanePath: (slot: PaneSlot, path: string | null) => void;
  /**
   * Opens a document the reader picked out: from the tree, the library, the graph.
   *
   * They have said what they are reading now, so it takes the reading pane and the
   * page's extension closes — it was opened from the page they have left.
   */
  openDocument: (path: string, blockId?: string | null) => void;
  /**
   * Opens something the page being read points at.
   *
   * A link, a citation, a passage's source. From the reading pane it arrives as the
   * extension, with the page still in front of the reader. From the extension it
   * takes over: the extension becomes the page being read, and this arrives as its
   * extension. Answers with the pane the document is in, so a caller that has more
   * to say about it — the section of the note to land on — can say it there.
   *
   * `from` is the pane the reader is in, told by the view doing the asking: it is
   * the one thing about the request that cannot be read off the panes afterwards.
   */
  followDocument: (path: string, blockId: string | null | undefined, from: PaneSlot) => PaneSlot;
  /**
   * Follows a citation into the note it came from, landing on the section it was
   * written under.
   *
   * Which section that is has two answers and they need not both be known: a
   * citation names the heading when the pipeline recorded one, and the selection
   * it was written from either way. The note itself decides which it can use.
   */
  followNote: (path: string, target: SectionTarget, from: PaneSlot) => void;
  /**
   * Shows a document where a placement has already put it.
   *
   * The panes have moved by the time this runs — that is one decision, made before
   * anything changed — so what is left is the document: which pane reads it, what
   * the pane it came from was left holding, and what can be gone back to.
   */
  arrive: (arrival: Arrival) => void;
  /** Puts a pane away: its document goes, and the extension pane goes with it. */
  close: (slot: PaneSlot) => void;
  /** Opens a `[[link]]` target, resolving it against the project. */
  openLink: (target: string, from: PaneSlot) => void;
  /**
   * Follows a `[[link]]` from somewhere other than the reader.
   *
   * The page opens beside what the reader was looking at, so an answer stays in
   * front while the page it named is read next to it. Answers false when the link
   * names no page, so the caller can say so where the link was clicked.
   */
  followLink: (target: string, from: PaneSlot) => boolean;
  /** Whether a `[[link]]` target names a page this project has. */
  hasPage: (target: string) => boolean;
  reset: () => void;
};

/** Whether a pane has anything of its own to remember, keyed by pane. */
const noPaneState = (): Record<PaneSlot, string | null> => ({ main: null, side: null });
const noPaneSectionState = (): Record<PaneSlot, SectionTarget | null> => ({
  main: null,
  side: null,
});

/**
 * Reads which notes cite which sources.
 *
 * Failure is not reported: the documents view is about reading, and a project
 * whose citations cannot be read is still readable. What is lost is the
 * annotation, so nothing is claimed rather than something wrong.
 */
async function loadCitations(
  project: string,
  set: (partial: Partial<DocsState>) => void,
  get: () => DocsState,
): Promise<void> {
  try {
    const citations = await fetchSourceCitations(project);
    if (get().project !== project) return;
    set({ citations });
  } catch {
    if (get().project !== project) return;
    set({ citations: null });
  }
}

/**
 * Reads a document, and takes the name it gives itself.
 *
 * A captured page is stored under its address, so the list can only show an
 * address leaf for it until the page is read — and then its own `<title>` is right
 * there. Waiting for a read to answer costs nothing, since the read happens anyway.
 */
async function readDocument(project: string, path: string): Promise<void> {
  const read = await useDocsReads.getState().read(project, path);
  if (!read || read.status !== "ready" || read.kind !== "html") return;
  const title = readableTitleFromHtml(read.content);
  if (!title || useDocsStore.getState().project !== project) return;
  useDocsStore.getState().setFileTitle(path, title);
}

export const useDocsStore = create<DocsState>((set, get) => ({
  project: null,
  tree: [],
  files: [],
  status: "idle",
  error: null,

  citations: null,
  paths: { main: null, side: null },
  focusBlockIds: noPaneState(),
  linkErrors: noPaneState(),
  sectionTargets: noPaneSectionState(),

  foldedPaths: { files: [], knowledge: [] },

  sidebarView: "files",

  setSidebarView: (view) => set({ sidebarView: view }),

  toggleFold: (view, path) =>
    set((state) => {
      const folded = state.foldedPaths[view];
      return {
        foldedPaths: {
          ...state.foldedPaths,
          [view]: folded.includes(path)
            ? folded.filter((item) => item !== path)
            : [...folded, path],
        },
      };
    }),

  setFileTitle: (path, title) =>
    set((state) => {
      const known = state.files.find((file) => file.path === path);
      if (!known || known.title === title) return state;
      const files = state.files.map((file) =>
        file.path === path ? { ...file, title } : file,
      );
      return { files, tree: buildProjectTree(files) };
    }),

  hydrate: async (project) => {
    if (!project || get().project === project) return;
    // Where this project was left: one document per pane, and where the reader
    // had got to.
    const stored = readPair(project);
    useDocsReads.getState().reset(project);
    useNotesStore.getState().reset(project);
    set({
      project,
      tree: [],
      files: [],
      status: "loading",
      error: null,
      citations: null,
      paths: {
        main: stored?.main.module === "docs" ? stored.main.target : null,
        side: stored?.side?.module === "docs" ? stored.side.target : null,
      },
      focusBlockIds: noPaneState(),
      linkErrors: noPaneState(),
      sectionTargets: noPaneSectionState(),
      foldedPaths: { files: [], knowledge: [] },
    });
    await get().refresh();
  },

  refresh: async () => {
    const project = get().project;
    if (!project) return;
    set({ status: "loading" });
    try {
      // A stored file's name is its address plus a code suffix; what can be read
      // off it is read off once, here, so every list and tab shows the same name.
      const files = readableTitles(await fetchTree(project));
      if (get().project !== project) return;
      const exists = new Set(files.map((file) => file.path));
      set((state) => ({
        files,
        tree: buildProjectTree(files),
        status: "ready",
        error: null,
        // A stored place outlives the project's files: a page can be deleted, so
        // what is shown is checked against what is there.
        paths: {
          main: state.paths.main && exists.has(state.paths.main) ? state.paths.main : null,
          side: state.paths.side && exists.has(state.paths.side) ? state.paths.side : null,
        },
      }));
      // The list was read afresh, so a document read earlier may be out of date
      // and the remarks read beside it are as stale as it is. What is dropped is
      // read again below for the documents in front.
      useDocsReads.getState().dropAll();
      useNotesStore.getState().dropAll();
      for (const slot of PANE_SLOTS) {
        const active = get().paths[slot];
        if (active) {
          void readDocument(project, active);
          void useNotesStore.getState().read(project, active);
        }
      }
      // Separate from the tree so a failure here cannot take the file list with
      // it: citations are an annotation, and the reader works without them.
      void loadCitations(project, set, get);
    } catch (err) {
      if (get().project !== project) return;
      set({ status: "error", error: errorMessage(err, i18n.t("docs.tree.failed")) });
    }
  },

  setPanePath: (slot, path) => {
    const project = get().project;
    if (!project) return;
    set((state) => ({
      paths: { ...state.paths, [slot]: path },
      focusBlockIds: { ...state.focusBlockIds, [slot]: null },
      sectionTargets: { ...state.sectionTargets, [slot]: null },
      linkErrors: { ...state.linkErrors, [slot]: null },
    }));
    if (path) void readDocument(project, path);
  },

  open: (slot, path, blockId = null) => {
    const project = get().project;
    if (!project || !path) return;
    set((state) => ({
      paths: { ...state.paths, [slot]: path },
      // The passage and the failed link belong to the document, so they follow it
      // rather than the pane.
      focusBlockIds: { ...state.focusBlockIds, [slot]: blockId },
      sectionTargets: { ...state.sectionTargets, [slot]: null },
      linkErrors: { ...state.linkErrors, [slot]: null },
    }));
    void readDocument(project, path);
    rememberReading();
  },

  openDocument: (path, blockId = null) => {
    const project = get().project;
    if (!project || !path) return;
    // A page asked for outright is what the reader is reading now, and that is read
    // in front. Where exactly it lands is the panes' call, asked for rather than
    // worked out here.
    const plan = useNavStore.getState().place({
      module: "docs",
      placement: "replace",
      from: "main",
    });
    get().arrive({ path, blockId, plan });
  },

  followDocument: (path, blockId = null, from) => {
    const project = get().project;
    if (!project || !path) return from;
    // Following to the document already being read has nowhere to go: it is the
    // page in front, and showing it twice says nothing.
    if (get().paths[from] === path) return from;
    // A link is something the page named, so the page stays in front and this one
    // arrives beside it.
    const plan = useNavStore.getState().place({
      module: "docs",
      placement: "beside",
      from,
    });
    get().arrive({ path, blockId, plan });
    return plan.target;
  },

  arrive: ({ path, blockId = null, plan }) => {
    const project = get().project;
    if (!project || !path) return;
    // A document that moves up takes the front pane with it, and what it left is
    // the pane the request arrived in.
    if (plan.promoteFrom) promoteDocument(plan.promoteFrom);
    // Asking for the page the extension is already reading is asking to have it in
    // front: the two panes trade places, so the reader gets it without a second
    // frame saying the same thing. With nothing to trade with, the arrival simply
    // takes the pane it was given and the extension keeps what it had.
    const before = get().paths;
    const layout = useLayoutStore.getState();
    const swap =
      plan.target === "main" && layout.side === "docs" && before.side === path;
    set((state) => {
      const paths = { ...state.paths, [plan.target]: path };
      const focusBlockIds = { ...state.focusBlockIds, [plan.target]: blockId };
      const sectionTargets = { ...state.sectionTargets, [plan.target]: null };
      const linkErrors = { ...state.linkErrors, [plan.target]: null };
      if (swap) {
        // The panes traded places, so what each had been asked to show travels with
        // the document that was in it.
        paths.side = before.main;
        focusBlockIds.side = state.focusBlockIds.main;
        sectionTargets.side = state.sectionTargets.main;
        linkErrors.side = state.linkErrors.main;
      }
      return { paths, focusBlockIds, sectionTargets, linkErrors };
    });
    if (swap) useLayoutStore.getState().focusPane("main");
    void readDocument(project, path);
    rememberReading();
  },

  followNote: (path, target, from) => {
    const slot = get().followDocument(path, null, from);
    // Set after opening, because opening clears whatever the pane was asked for
    // last: the two requests are different questions about different documents.
    set((state) => ({ sectionTargets: { ...state.sectionTargets, [slot]: target } }));
  },

  close: (slot) => {
    // With one document a pane, closing it empties the pane; closing the extension
    // takes the pane away, since an empty pane beside the page says nothing.
    set((state) => ({
      paths: { ...state.paths, [slot]: null },
      focusBlockIds: { ...state.focusBlockIds, [slot]: null },
      sectionTargets: { ...state.sectionTargets, [slot]: null },
      linkErrors: { ...state.linkErrors, [slot]: null },
    }));
    if (slot === "side") useLayoutStore.getState().closeSide();
    rememberReading();
  },

  openLink: (target, from) => {
    const resolved = resolveWikiLink(target, linkIndex(get().files));
    if (resolved) {
      // Following a link is reading something the page named, so the page the
      // reader is on stays in front and this one arrives beside it.
      get().followDocument(resolved.path, null, from);
      return;
    }
    // A link with no page behind it: opening the wrong file would be worse than
    // not opening one, so the request is reported instead. The name is decoded,
    // because markdown encodes the non-ASCII characters in a link.
    set((state) => ({
      linkErrors: { ...state.linkErrors, [from]: decodeLinkTarget(target) },
    }));
  },

  hasPage: (target) => Boolean(resolveWikiLink(target, linkIndex(get().files))),

  followLink: (target, from) => {
    const resolved = resolveWikiLink(target, linkIndex(get().files));
    if (!resolved) return false;
    get().followDocument(resolved.path, null, from);
    return true;
  },

  reset: () => {
    // What was read belongs to the project that is gone; the next one reads its own.
    useDocsReads.getState().reset();
    useNotesStore.getState().reset();
    set({
      project: null,
      tree: [],
      files: [],
      status: "idle",
      error: null,
      citations: null,
      paths: { main: null, side: null },
      focusBlockIds: noPaneState(),
      linkErrors: noPaneState(),
      sectionTargets: noPaneSectionState(),
      sidebarView: "files",
      foldedPaths: { files: [], knowledge: [] },
    });
  },
}));

/**
 * Follows the active project, the same way the other stores do, so the tree is
 * there before the view is first opened.
 */
let started = false;

/**
 * Moves the document a pane is reading to the other pane.
 *
 * For a request that arrives beside a document: the page the reader is in moves up
 * to be the one in front, and the request takes the pane it was in. Which document
 * that is, and where it goes, belongs to this store — whichever module made the
 * request.
 */
export function promoteDocument(from: PaneSlot): void {
  const docs = useDocsStore.getState();
  const held = docs.paths[from];
  if (!held) return;
  docs.setPanePath(otherPane(from), held);
  docs.setPanePath(from, null);
}

/** Forgets where a project was left, e.g. when it is deleted. */
export function forgetProjectReading(project: string): void {
  writePair(project, null);
}

export function startDocsSync(): void {
  if (started) return;
  started = true;

  useWorkspaceStore.subscribe((state, previous) => {
    if (state.current === previous.current) return;
    const docs = useDocsStore.getState();
    const reading = useReadingStore.getState();
    if (state.current) {
      // The steps being kept are steps through the project that was open, so the
      // one arriving starts at the place it was left and with nothing behind it.
      reading.hydrate(state.current);
      void docs.hydrate(state.current);
    } else {
      reading.reset();
      docs.reset();
    }
  });

  const current = useWorkspaceStore.getState().current;
  if (current) {
    useReadingStore.getState().hydrate(current);
    void useDocsStore.getState().hydrate(current);
  }

  // Arriving from another view with a file in mind: the panes were moved as the
  // request was made, so what is left is the document and the pane it landed in.
  useNavStore.subscribe((state, previous) => {
    if (state.seq === previous.seq) return;
    const intent = state.intent;
    if (!intent || intent.kind !== "docs" || !state.plan) return;
    const docs = useDocsStore.getState();
    if (!docs.files.some((file) => file.path === intent.path)) return;
    docs.arrive({
      path: intent.path,
      blockId: intent.locator ?? null,
      plan: state.plan,
    });
  });
}
