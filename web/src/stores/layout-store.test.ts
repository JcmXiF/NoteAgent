import { beforeEach, describe, expect, it } from "vitest";
import {
  growingLeftSection,
  leftSectionHeight,
  leftSectionOpen,
  leftWidthFor,
  mergeStoredLayout,
  normalizeLeftSections,
  reorderLeftSection,
  sectionIsOpen,
  useLayoutStore,
} from "@/stores/layout-store";

/**
 * The shell's own arrangements: how wide a module's sidebar is, which parts of the
 * right panel are open, which sections the sidebar is made of, and where a pane came
 * from when it changed module in place.
 */
beforeEach(() => {
  useLayoutStore.setState({
    view: "docs",
    side: null,
    lastSide: "graph",
    focused: "main",
    paneReturn: {},
    leftWidths: { chat: 240, sources: 260, docs: 320, notes: 280, graph: 260, browse: 260 },
    leftSections: { order: ["module", "jobs"], folds: {}, heights: {} },
    rightFolds: {},
    settingsOpen: false,
    settingsDirty: false,
    settingsConfirm: false,
    pendingOpen: null,
  });
});

describe("the sidebar's width", () => {
  it("starts each module at its own width", () => {
    const state = useLayoutStore.getState();
    const chat = leftWidthFor(state, "chat");
    const docs = leftWidthFor(state, "docs");
    // A list of conversations and a file tree do not want the same room.
    expect(chat).not.toBe(docs);
    expect(docs).toBeGreaterThan(chat);
  });

  it("remembers a drag against the module it was dragged for", () => {
    useLayoutStore.setState({ view: "chat" });
    useLayoutStore.getState().setLeftWidth(300);

    const state = useLayoutStore.getState();
    expect(leftWidthFor(state, "chat")).toBe(300);
    // The tree keeps the width it had: the drag was about what was being looked at.
    expect(leftWidthFor(state, "docs")).not.toBe(300);
  });

  it("keeps a dragged width within reach", () => {
    useLayoutStore.setState({ view: "docs" });
    useLayoutStore.getState().setLeftWidth(5000);
    expect(leftWidthFor(useLayoutStore.getState(), "docs")).toBe(420);
    useLayoutStore.getState().setLeftWidth(1);
    expect(leftWidthFor(useLayoutStore.getState(), "docs")).toBe(180);
  });

  it("falls back to a default for a module it has no width for", () => {
    useLayoutStore.setState({ leftWidths: {} as never });
    expect(leftWidthFor(useLayoutStore.getState(), "graph")).toBeGreaterThan(0);
  });
});

describe("the right panel's sections", () => {
  it("opens the sections that belong to the module in front", () => {
    const state = () => useLayoutStore.getState();
    // Planning and compiling is what the graph is for.
    expect(sectionIsOpen(state(), "plan", "graph")).toBe(true);
    expect(sectionIsOpen(state(), "compile", "graph")).toBe(true);
    // A conversation does not write pages.
    expect(sectionIsOpen(state(), "compile", "chat")).toBe(false);
    expect(sectionIsOpen(state(), "selection", "chat")).toBe(true);
  });

  it("opens the whole workbench where the workbench is", () => {
    // Selecting, planning and compiling happen in one sitting in the sources
    // module — its own acceptance runs them all from the right panel.
    const state = () => useLayoutStore.getState();
    expect(sectionIsOpen(state(), "selection", "sources")).toBe(true);
    expect(sectionIsOpen(state(), "plan", "sources")).toBe(true);
    expect(sectionIsOpen(state(), "compile", "sources")).toBe(true);
    // A page's own source, opened from the extension, lands in the same module.
    expect(sectionIsOpen(state(), "plan", "browse")).toBe(false);
  });

  it("lets the reader's own choice win over the default", () => {
    useLayoutStore.setState({ rightFolds: { plan: false } });
    expect(sectionIsOpen(useLayoutStore.getState(), "plan", "graph")).toBe(false);

    useLayoutStore.getState().setSectionOpen("compile", true);
    expect(sectionIsOpen(useLayoutStore.getState(), "compile", "chat")).toBe(true);
  });

  it("closes a section that is opened by default", () => {
    useLayoutStore.getState().setSectionOpen("selection", false);
    expect(sectionIsOpen(useLayoutStore.getState(), "selection", "sources")).toBe(false);
  });
});

describe("the sidebar's sections", () => {
  it("shows every section to start with, the module's own panel first", () => {
    const state = useLayoutStore.getState();
    expect(state.leftSections.order).toEqual(["module", "jobs"]);
    expect(leftSectionOpen(state.leftSections, "module")).toBe(true);
    expect(leftSectionOpen(state.leftSections, "jobs")).toBe(true);
  });

  it("moves a section past its neighbour, both ways", () => {
    useLayoutStore.getState().moveLeftSection("jobs", -1);
    expect(useLayoutStore.getState().leftSections.order).toEqual(["jobs", "module"]);
    useLayoutStore.getState().moveLeftSection("jobs", 1);
    expect(useLayoutStore.getState().leftSections.order).toEqual(["module", "jobs"]);
  });

  it("stops at the ends rather than wrapping", () => {
    useLayoutStore.getState().moveLeftSection("module", -1);
    useLayoutStore.getState().moveLeftSection("jobs", 1);
    expect(useLayoutStore.getState().leftSections.order).toEqual(["module", "jobs"]);
  });

  it("leaves the arrangement alone when a section has nowhere to go", () => {
    const before = useLayoutStore.getState().leftSections;
    expect(reorderLeftSection(before, "module", -1)).toBe(before);
  });

  it("remembers a section the reader folded away", () => {
    useLayoutStore.getState().setLeftSectionOpen("jobs", false);
    expect(leftSectionOpen(useLayoutStore.getState().leftSections, "jobs")).toBe(false);
    // The other one is unaffected: folding is about one section at a time.
    expect(leftSectionOpen(useLayoutStore.getState().leftSections, "module")).toBe(true);
  });

  it("remembers a dragged height, within reach", () => {
    const set = (height: number) => useLayoutStore.getState().setLeftSectionHeight("jobs", height);
    set(220);
    expect(leftSectionHeight(useLayoutStore.getState().leftSections, "jobs")).toBe(220);
    set(5);
    expect(leftSectionHeight(useLayoutStore.getState().leftSections, "jobs")).toBe(64);
    set(5000);
    expect(leftSectionHeight(useLayoutStore.getState().leftSections, "jobs")).toBe(520);
  });

  it("gives the room to the module's own panel, and to the other one once it is folded", () => {
    // The module panel is what should fill the column, so it takes the leftover room
    // until the reader folds it away — and then the room goes to what is left, which
    // is what folding it was for.
    expect(growingLeftSection(useLayoutStore.getState().leftSections)).toBe("module");

    useLayoutStore.getState().setLeftSectionOpen("module", false);
    expect(growingLeftSection(useLayoutStore.getState().leftSections)).toBe("jobs");

    useLayoutStore.getState().setLeftSectionOpen("jobs", false);
    expect(growingLeftSection(useLayoutStore.getState().leftSections)).toBeNull();
  });
});

describe("reading a stored section arrangement", () => {
  it("keeps an arrangement it understands", () => {
    const sections = normalizeLeftSections({
      order: ["jobs", "module"],
      folds: { jobs: true },
      heights: { jobs: 200 },
    });
    expect(sections.order).toEqual(["jobs", "module"]);
    expect(sections.folds.jobs).toBe(true);
    expect(sections.heights.jobs).toBe(200);
  });

  it("adds a section the stored arrangement predates", () => {
    const sections = normalizeLeftSections({ order: ["module"] });
    expect(sections.order).toEqual(["module", "jobs"]);
  });

  it("drops a section this version does not have", () => {
    // A sidebar written by a later version still opens, without the unknown part.
    const sections = normalizeLeftSections({ order: ["outline", "jobs", "module"] });
    expect(sections.order).toEqual(["jobs", "module"]);
  });

  it("shows each section once, however the stored order was written", () => {
    const sections = normalizeLeftSections({ order: ["jobs", "jobs"] });
    expect(sections.order).toEqual(["jobs", "module"]);
  });

  it("falls back to the default arrangement for anything unreadable", () => {
    for (const raw of [undefined, null, "module, jobs", 42, { order: "jobs" }]) {
      expect(normalizeLeftSections(raw).order).toEqual(["module", "jobs"]);
    }
  });

  it("keeps a dragged height within reach, and ignores a folded value that is not one", () => {
    const sections = normalizeLeftSections({ folds: { jobs: "yes" }, heights: { jobs: 9999 } });
    expect(sections.folds.jobs).toBeUndefined();
    expect(sections.heights.jobs).toBe(520);
  });
});

describe("the jobs list", () => {
  it("opens only where there is a shell to cover", () => {
    // The page the shell shows is drawn over the page, so only the overlay can be
    // read there; in a plain browser there is nothing to cover, so the sidebar's
    // own row shows the list instead.
    useLayoutStore.setState({ hasShell: false, jobsOpen: false });
    useLayoutStore.getState().setJobsOpen(true);
    expect(useLayoutStore.getState().jobsOpen).toBe(false);

    useLayoutStore.setState({ hasShell: true });
    useLayoutStore.getState().setJobsOpen(true);
    expect(useLayoutStore.getState().jobsOpen).toBe(true);

    useLayoutStore.getState().setJobsOpen(false);
    expect(useLayoutStore.getState().jobsOpen).toBe(false);
  });

  it("counts what is in front, so the page comes back only at the end", () => {
    useLayoutStore.setState({ guestHolds: 0 });
    useLayoutStore.getState().holdGuest();
    useLayoutStore.getState().holdGuest();
    expect(useLayoutStore.getState().guestHolds).toBe(2);

    useLayoutStore.getState().releaseGuest();
    // One is still in front, so the page is still away.
    expect(useLayoutStore.getState().guestHolds).toBe(1);
    useLayoutStore.getState().releaseGuest();
    expect(useLayoutStore.getState().guestHolds).toBe(0);
  });

  it("does not go negative when released more often than held", () => {
    useLayoutStore.setState({ guestHolds: 0 });
    useLayoutStore.getState().releaseGuest();
    expect(useLayoutStore.getState().guestHolds).toBe(0);
  });
});

describe("reading a stored arrangement", () => {
  const current = () => useLayoutStore.getState();

  it("takes a modern arrangement as it is", () => {
    const stored = {
      view: "graph",
      leftWidths: { ...current().leftWidths, docs: 400 },
      rightFolds: { plan: false },
    };
    const merged = mergeStoredLayout(stored, current());
    expect(merged.view).toBe("graph");
    expect(merged.leftWidths.docs).toBe(400);
    expect(merged.rightFolds.plan).toBe(false);
  });

  it("opens a sidebar that was stored before it had sections", () => {
    const merged = mergeStoredLayout({ view: "docs" }, current());
    expect(merged.leftSections.order).toEqual(["module", "jobs"]);
    expect(merged.leftSections.folds).toEqual({});
  });

  it("takes a stored sidebar arrangement as it is", () => {
    const merged = mergeStoredLayout(
      { leftSections: { order: ["jobs", "module"], folds: { module: true }, heights: {} } },
      current(),
    );
    expect(merged.leftSections.order).toEqual(["jobs", "module"]);
    expect(merged.leftSections.folds.module).toBe(true);
  });

  it("gives a single stored width to the module it was chosen for", () => {
    // Before widths were per module there was one number. Whoever dragged it was
    // looking at something, and that is what they were sizing.
    const merged = mergeStoredLayout({ view: "chat", leftWidth: 355 }, current());
    expect(merged.leftWidths.chat).toBe(355);
    // The others keep the defaults rather than inheriting one reader's choice.
    expect(merged.leftWidths.docs).toBe(320);
  });

  it("clamps what it reads, because stored values are editable", () => {
    const merged = mergeStoredLayout(
      { leftWidths: { docs: 9999 }, rightWidth: 10, splitRatio: 5 },
      current(),
    );
    expect(merged.leftWidths.docs).toBe(420);
    expect(merged.rightWidth).toBe(260);
    expect(merged.splitRatio).toBe(1);

    // The old single width is clamped too, and only for a file that has only that.
    expect(mergeStoredLayout({ leftWidth: -50 }, current()).leftWidths.docs).toBe(180);
    expect(mergeStoredLayout({ leftWidth: 9999 }, current()).leftWidths.docs).toBe(420);
  });

  it("lets the per-module widths win over the old single one", () => {
    // A file carrying both is a newer one, so a stale key must not overrule it.
    const merged = mergeStoredLayout(
      { view: "docs", leftWidths: { docs: 400 }, leftWidth: 200 },
      current(),
    );
    expect(merged.leftWidths.docs).toBe(400);
  });

  it("ignores what is not a module or a section", () => {
    const merged = mergeStoredLayout(
      {
        view: "not-a-module",
        side: 7,
        lastSide: {},
        rightFolds: { nope: true },
        paneReturn: { main: "nope" },
      },
      current(),
    );
    expect(merged.view).toBe(current().view);
    expect(merged.side).toBeNull();
    expect(merged.rightFolds).toEqual({});
    expect(merged.paneReturn).toEqual({});
  });

  it("has nothing to read", () => {
    const merged = mergeStoredLayout(undefined, current());
    expect(merged.view).toBe(current().view);
    expect(merged.leftWidths).toEqual(current().leftWidths);
  });

  it("cannot leave a pane focused when there is no pane beside it", () => {
    const merged = mergeStoredLayout({ side: null, focused: "side" }, current());
    expect(merged.focused).toBe("main");
  });
});

describe("showing a module in place of another", () => {
  it("remembers what the pane was showing", () => {
    useLayoutStore.getState().pushPaneModule("main", "browse");

    const state = useLayoutStore.getState();
    expect(state.view).toBe("browse");
    expect(state.paneReturn.main).toBe("docs");
  });

  it("goes back to it, and forgets it after that", () => {
    useLayoutStore.getState().pushPaneModule("main", "browse");
    useLayoutStore.getState().returnPaneModule("main");

    const state = useLayoutStore.getState();
    expect(state.view).toBe("docs");
    // Nothing to go back to any more: the pane is where it started.
    expect(state.paneReturn.main).toBeUndefined();
  });

  it("does nothing when the pane already shows that module", () => {
    useLayoutStore.getState().pushPaneModule("main", "docs");

    expect(useLayoutStore.getState().view).toBe("docs");
    expect(useLayoutStore.getState().paneReturn.main).toBeUndefined();
  });

  it("does nothing for a pane that is not there", () => {
    // There is no side pane, so there is nothing to remember about it.
    useLayoutStore.getState().pushPaneModule("side", "browse");
    expect(useLayoutStore.getState().side).toBeNull();
    expect(useLayoutStore.getState().paneReturn.side).toBeUndefined();
  });

  it("remembers the side pane separately from the main one", () => {
    useLayoutStore.setState({ side: "docs" });
    useLayoutStore.getState().pushPaneModule("side", "browse");
    useLayoutStore.getState().pushPaneModule("main", "chat");

    const state = useLayoutStore.getState();
    expect(state.side).toBe("browse");
    expect(state.paneReturn.side).toBe("docs");
    expect(state.view).toBe("chat");
    expect(state.paneReturn.main).toBe("docs");
  });

  it("forgets the way back when the pane goes elsewhere by another route", () => {
    useLayoutStore.getState().pushPaneModule("main", "browse");
    useLayoutStore.getState().setPaneModule("main", "chat");

    const state = useLayoutStore.getState();
    expect(state.view).toBe("chat");
    // Showing something else means there is no longer a place to return to.
    expect(state.paneReturn.main).toBeUndefined();
  });

  it("returns without a way back is a no-op", () => {
    useLayoutStore.getState().returnPaneModule("main");
    expect(useLayoutStore.getState().view).toBe("docs");
  });
});

describe("showing a module beside another", () => {
  it("gives the split a module of its own", () => {
    useLayoutStore.getState().openModule("graph", { side: true });

    const state = useLayoutStore.getState();
    expect(state.view).toBe("docs");
    expect(state.side).toBe("graph");
    expect(state.focused).toBe("side");
  });

  it("opens the same module twice, because that is what a split is for", () => {
    useLayoutStore.getState().openModule("docs", { side: true });

    const state = useLayoutStore.getState();
    expect(state.view).toBe("docs");
    expect(state.side).toBe("docs");
  });

  it("goes to a module the window can only show once instead of showing it twice", () => {
    // The shell keeps one browser, so a second pane showing it would be a frame with
    // nothing in it: the request is answered by going to the one there is.
    useLayoutStore.setState({ view: "browse", side: "docs", focused: "side" });

    useLayoutStore.getState().openModule("browse", { side: true });

    const state = useLayoutStore.getState();
    expect(state.view).toBe("browse");
    expect(state.side).toBe("docs");
    expect(state.focused).toBe("main");
  });

  it("forgets the way back in the pane it takes over", () => {
    useLayoutStore.setState({ side: "graph", paneReturn: { side: "docs" } });

    useLayoutStore.getState().openModule("chat", { side: true });

    // The pane went elsewhere by a route of its own, so it has nothing to go back to
    // — the same as any other way of showing a module in it.
    expect(useLayoutStore.getState().paneReturn.side).toBeUndefined();
  });
});

describe("choosing a page from the rail", () => {
  it("brings the page beside forward, and gives it the pane it left", () => {
    useLayoutStore.setState({ view: "docs", side: "graph", focused: "side", lastSide: "graph" });

    useLayoutStore.getState().openModule("graph");

    const state = useLayoutStore.getState();
    expect(state.view).toBe("graph");
    expect(state.side).toBe("docs");
    // The page asked for is the one being read, so it is the one with the sidebar.
    expect(state.focused).toBe("main");
    // And the split reopens what is beside it now.
    expect(state.lastSide).toBe("docs");
  });

  it("leaves the split alone when the page in front is chosen again", () => {
    useLayoutStore.setState({ view: "docs", side: "graph", focused: "side" });

    useLayoutStore.getState().openModule("docs");

    const state = useLayoutStore.getState();
    expect(state.view).toBe("docs");
    expect(state.side).toBe("graph");
    expect(state.focused).toBe("main");
  });

  it("does nothing at all when it is already the only page, and being read", () => {
    const before = useLayoutStore.getState();

    useLayoutStore.getState().openModule("docs");

    // Nothing to change, so nothing is written.
    expect(useLayoutStore.getState()).toBe(before);
  });

  it("keeps what is beside when a page that is not open is chosen", () => {
    useLayoutStore.setState({ view: "docs", side: "graph", lastSide: "graph" });

    useLayoutStore.getState().openModule("chat");

    const state = useLayoutStore.getState();
    expect(state.view).toBe("chat");
    // Choosing a page is not asking for the one beside it to go.
    expect(state.side).toBe("graph");
    expect(state.lastSide).toBe("graph");
    expect(state.focused).toBe("main");
  });

  it("has no split to keep when there was none", () => {
    expect(useLayoutStore.getState().side).toBeNull();

    useLayoutStore.getState().openModule("chat");

    expect(useLayoutStore.getState().view).toBe("chat");
    expect(useLayoutStore.getState().side).toBeNull();
  });

  it("forgets the way back in the pane it fills, and keeps the other pane's", () => {
    useLayoutStore.setState({
      view: "docs",
      side: "graph",
      paneReturn: { main: "browse", side: "docs" },
    });

    useLayoutStore.getState().openModule("chat");

    const state = useLayoutStore.getState();
    expect(state.paneReturn.main).toBeUndefined();
    // The pane beside still shows what it did, so what it can go back to still stands.
    expect(state.paneReturn.side).toBe("docs");
  });

  it("forgets both ways back when the two panes trade places", () => {
    useLayoutStore.setState({ view: "browse", side: "graph", paneReturn: { main: "docs" } });

    useLayoutStore.getState().openModule("graph");

    // Both panes changed what they show, so neither has a place to return to.
    expect(useLayoutStore.getState().paneReturn).toEqual({});
  });

  it("goes to a page the window can only show once rather than trading places", () => {
    useLayoutStore.setState({ view: "docs", side: "browse", focused: "main" });

    useLayoutStore.getState().openModule("browse");

    const state = useLayoutStore.getState();
    expect(state.view).toBe("docs");
    expect(state.side).toBe("browse");
    // The shell can only put its page where the focus is, so going to the browser is
    // what focusing the pane holding it means.
    expect(state.focused).toBe("side");
  });

  it("goes to the browser in front rather than trading places with it", () => {
    useLayoutStore.setState({ view: "browse", side: "docs", focused: "side" });

    useLayoutStore.getState().openModule("browse");

    const state = useLayoutStore.getState();
    expect(state.view).toBe("browse");
    expect(state.side).toBe("docs");
    expect(state.focused).toBe("main");
  });
});

describe("settings", () => {
  it("asks before a page change discards unsaved edits", () => {
    useLayoutStore.setState({ settingsOpen: true, settingsDirty: true, view: "docs" });

    useLayoutStore.getState().openModule("chat");

    expect(useLayoutStore.getState().settingsOpen).toBe(true);
    expect(useLayoutStore.getState().settingsConfirm).toBe(true);
    expect(useLayoutStore.getState().view).toBe("docs");

    useLayoutStore.getState().confirmSettingsLeave();

    expect(useLayoutStore.getState().settingsOpen).toBe(false);
    expect(useLayoutStore.getState().view).toBe("chat");
  });

  it("returns to the current page when nothing is unsaved", () => {
    useLayoutStore.setState({ settingsOpen: true, settingsDirty: false, view: "docs" });

    useLayoutStore.getState().openModule("docs");

    expect(useLayoutStore.getState().settingsOpen).toBe(false);
    expect(useLayoutStore.getState().view).toBe("docs");
  });
});
