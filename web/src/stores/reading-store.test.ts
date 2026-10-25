import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/files", () => ({
  fetchFile: vi.fn(async () => ({ content: "", blocks: [], sourceUrl: null, kind: "md" })),
  fetchTree: vi.fn(async () => []),
  fetchSourceCitations: vi.fn(async () => null),
}));

import { startHistory, view } from "@/lib/reading-history";
import { useDocsStore } from "@/stores/docs-store";
import { useLayoutStore } from "@/stores/layout-store";
import { rememberReading, startReadingSync, useReadingStore } from "@/stores/reading-store";

/**
 * Where the reader has been, and what each move costs them.
 *
 * The module a pane is showing is part of where they are, not a detail of a view:
 * the graph in front with a page beside it is a place, and going back has to be able
 * to return to it. That half is kept by following the panes, because the moves it
 * covers — the rail, a split, closing one — are made by nothing that knows what to
 * read next. What it must not do is cost a step of its own when a step is being put
 * back, which is the same change seen from the panes' side.
 */

const layout = () => useLayoutStore.getState();
const reading = () => useReadingStore.getState();

/** A step is taken once the task that moved the panes has finished. */
const settled = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(async () => {
  startReadingSync();
  useLayoutStore.setState({
    view: "docs",
    side: null,
    lastSide: "graph",
    focused: "main",
    paneReturn: {},
  });
  useDocsStore.setState({ project: "test", paths: { main: null, side: null } });
  // The move above is the fixture moving, not the reader: it is allowed to land,
  // and then the history starts empty from the panes as they now stand.
  await settled();
  useReadingStore.setState({ history: startHistory({ main: view("docs", null), side: null }) });
});

describe("moving between modules", () => {
  it("is a step, with the place it was made from behind it", async () => {
    useDocsStore.setState({ paths: { main: "wiki/concepts/熵.md", side: null } });
    reading().record();

    layout().openModule("graph");
    await settled();

    expect(reading().history.current).toEqual({ main: view("graph"), side: null });
    expect(reading().history.past.at(-1)).toEqual({
      main: view("docs", "wiki/concepts/熵.md"),
      side: null,
    });
  });

  it("is one step however many panes it moves", async () => {
    reading().record();
    const steps = reading().history.past.length;

    layout().setPaneModule("main", "graph");
    layout().setPaneModule("side", "chat");
    await settled();

    expect(reading().history.past.length).toBe(steps + 1);
    expect(reading().history.current).toEqual({
      main: view("graph"),
      side: view("chat"),
    });
  });

  it("is one step when the view that moved the pane records it too", async () => {
    // What opening a document outright looks like from here: the panes move to the
    // documents module, and the view records the step it knows the document of.
    reading().record();
    const steps = reading().history.past.length;

    useDocsStore.setState({ paths: { main: "wiki/concepts/熵.md", side: null } });
    layout().setPaneModule("main", "docs");
    rememberReading();
    await settled();

    expect(reading().history.past.length).toBe(steps + 1);
    expect(reading().history.current.main).toEqual(view("docs", "wiki/concepts/熵.md"));
  });
});

describe("putting a step back", () => {
  it("leaves no step of its own behind", async () => {
    reading().record();
    layout().openModule("graph");
    await settled();
    layout().openModule("chat");
    await settled();
    const before = reading().history;

    reading().back();
    await settled();

    expect(reading().history.current).toEqual(before.past.at(-1));
    expect(reading().history.past.length).toBe(before.past.length - 1);
    expect(reading().history.future.length).toBe(1);
    // The panes were put where the step says, rather than left where the reader was.
    expect(layout().view).toBe("graph");
  });

  it("is repeatable: two looks back do not grow the steps behind", async () => {
    reading().record();
    layout().openModule("graph");
    await settled();
    layout().openModule("chat");
    await settled();
    const steps = reading().history.past.length;

    reading().back();
    await settled();
    reading().forward();
    await settled();
    reading().back();
    await settled();

    expect(reading().history.past.length).toBeLessThanOrEqual(steps);
    expect(reading().history.current).toEqual({ main: view("graph"), side: null });
  });

  it("moves a document back without the watcher adding anything", async () => {
    useDocsStore.setState({ paths: { main: "wiki/a.md", side: null } });
    reading().record();
    useDocsStore.setState({ paths: { main: "wiki/b.md", side: null } });
    reading().record();
    const before = reading().history.past.length;

    reading().back();
    await settled();

    expect(reading().history.past.length).toBe(before - 1);
    expect(useDocsStore.getState().paths.main).toBe("wiki/a.md");
  });
});
