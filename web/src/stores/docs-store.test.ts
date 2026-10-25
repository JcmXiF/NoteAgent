import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/files", () => ({
  fetchFile: vi.fn(async () => ({ content: "", blocks: [], sourceUrl: null, kind: "md" })),
  fetchTree: vi.fn(async () => []),
  fetchSourceCitations: vi.fn(async () => null),
}));

import type { TreeFile } from "@/lib/project-tree";
import { promoteDocument, useDocsStore } from "@/stores/docs-store";
import { useLayoutStore } from "@/stores/layout-store";

/**
 * What each pane reads, and what following from one pane does to the pair.
 *
 * The documents view owns one thing in the panes: which document each is showing.
 * Where a document goes among the panes is not decided here — it asks the
 * navigation store — so what is checked here is the documents' own half: the pane
 * a document ends up in, what the pane it came from is left holding, and the
 * section a citation asked to land on.
 */

function file(path: string, title = ""): TreeFile {
  return { path, name: path.split("/").pop() ?? path, title };
}

const layout = () => useLayoutStore.getState();
const docs = () => useDocsStore.getState();

beforeEach(() => {
  useLayoutStore.setState({
    view: "docs",
    side: null,
    lastSide: "graph",
    focused: "main",
    paneReturn: {},
  });
  useDocsStore.setState({
    project: "p",
    files: [file("wiki/a.md", "Alpha"), file("wiki/b.md", "Beta"), file("wiki/c.md", "Gamma")],
    tree: [],
    status: "ready",
    paths: { main: null, side: null },
    focusBlockIds: { main: null, side: null },
    linkErrors: { main: null, side: null },
    sectionTargets: { main: null, side: null },
  });
});

describe("a page opened outright", () => {
  it("reads in front, leaving a pane showing another module alone", () => {
    useLayoutStore.setState({ view: "graph", side: "sources", focused: "side" });

    docs().openDocument("wiki/a.md");

    expect(layout().view).toBe("docs");
    expect(layout().side).toBe("sources");
    expect(docs().paths.main).toBe("wiki/a.md");
  });

  it("leaves the pane beside it reading what it was reading", () => {
    // Choosing what to read is not a request to take the other pane away: two pages
    // side by side is what a split is for.
    useLayoutStore.setState({ view: "docs", side: "docs", focused: "side" });
    useDocsStore.setState({ paths: { main: "wiki/b.md", side: "wiki/c.md" } });

    docs().openDocument("wiki/a.md");

    expect(layout().side).toBe("docs");
    expect(docs().paths).toEqual({ main: "wiki/a.md", side: "wiki/c.md" });
  });

  it("trades places with the pane already reading that page", () => {
    // The page asked for is the one the extension is reading, so it comes to the
    // front and what was there takes its place: the reader gets the page they asked
    // for without a second frame saying the same thing.
    useLayoutStore.setState({ view: "docs", side: "docs", focused: "side" });
    useDocsStore.setState({ paths: { main: "wiki/b.md", side: "wiki/a.md" } });

    docs().openDocument("wiki/a.md");

    expect(layout().side).toBe("docs");
    expect(layout().focused).toBe("main");
    expect(docs().paths).toEqual({ main: "wiki/a.md", side: "wiki/b.md" });
  });
});

describe("a page named by the one being read", () => {
  it("opens beside it, keeping the page in front", () => {
    useDocsStore.setState({ paths: { main: "wiki/a.md", side: null } });

    const target = docs().followDocument("wiki/b.md", null, "main");

    expect(target).toBe("side");
    expect(layout().view).toBe("docs");
    expect(layout().side).toBe("docs");
    expect(docs().paths).toEqual({ main: "wiki/a.md", side: "wiki/b.md" });
  });

  it("goes nowhere when it names the page already in that pane", () => {
    useDocsStore.setState({ paths: { main: "wiki/a.md", side: null } });

    const target = docs().followDocument("wiki/a.md", null, "main");

    expect(target).toBe("main");
    expect(layout().side).toBeNull();
  });

  it("moves the page being read up when followed from the extension", () => {
    useLayoutStore.setState({ view: "docs", side: "docs", focused: "side" });
    useDocsStore.setState({ paths: { main: "wiki/a.md", side: "wiki/b.md" } });

    docs().followDocument("wiki/c.md", null, "side");

    expect(docs().paths).toEqual({ main: "wiki/b.md", side: "wiki/c.md" });
  });

  it("brings the extension's module forward whatever it is showing", () => {
    // The extension holds a map rather than a document, so there is no document to
    // move — but the pane the reader asked from still comes forward, and the request
    // takes the pane it was in.
    useLayoutStore.setState({ view: "docs", side: "graph", focused: "side" });
    useDocsStore.setState({ paths: { main: "wiki/a.md", side: null } });

    const target = docs().followDocument("wiki/b.md", null, "side");

    expect(target).toBe("side");
    expect(layout().view).toBe("graph");
    expect(layout().side).toBe("docs");
    // The document read in the front pane was left behind with it; the page that was
    // being read is not the one that came forward, because a map has no page.
    expect(docs().paths).toEqual({ main: "wiki/a.md", side: "wiki/b.md" });
  });
});

describe("a `[[link]]`", () => {
  it("is reported where it was clicked when it names no page", () => {
    docs().openLink("Missing", "main");

    expect(docs().linkErrors.main).toBe("Missing");
    expect(docs().paths.main).toBeNull();
  });

  it("answers whether it could be followed, and opens it beside when it could", () => {
    expect(docs().followLink("Missing", "main")).toBe(false);

    expect(docs().followLink("Beta", "main")).toBe(true);
    expect(docs().paths.side).toBe("wiki/b.md");
  });
});

describe("a citation into the note it came from", () => {
  it("lands on the section it was written under", () => {
    useDocsStore.setState({ paths: { main: "wiki/a.md", side: null } });

    docs().followNote("wiki/c.md", { heading: "H" }, "main");

    expect(docs().paths.side).toBe("wiki/c.md");
    expect(docs().sectionTargets.side).toEqual({ heading: "H" });
  });
});

describe("what a pane is holding", () => {
  it("moves to the other pane, leaving the first empty", () => {
    useDocsStore.setState({ paths: { main: null, side: "wiki/b.md" } });

    promoteDocument("side");

    expect(docs().paths).toEqual({ main: "wiki/b.md", side: null });
  });

  it("does nothing when that pane is not reading a document", () => {
    // The pane may be showing a map or a list; there is then no document to bring
    // forward, whatever module it is showing.
    useDocsStore.setState({ paths: { main: "wiki/a.md", side: null } });

    promoteDocument("side");

    expect(docs().paths).toEqual({ main: "wiki/a.md", side: null });
  });
});

describe("closing a document", () => {
  it("takes the pane away when it was the extension", () => {
    useLayoutStore.setState({ view: "docs", side: "docs", focused: "side" });
    useDocsStore.setState({ paths: { main: "wiki/a.md", side: "wiki/b.md" } });

    docs().close("side");

    expect(docs().paths.side).toBeNull();
    expect(layout().side).toBeNull();
  });
});
