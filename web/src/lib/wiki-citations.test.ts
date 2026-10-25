import { describe, expect, it } from "vitest";
import { citationEntries, evidenceFrom, marksForSource } from "@/lib/wiki-citations";
import type { EvidenceCollection } from "@/lib/wiki-evidence";
import type { SourceLocator } from "@/lib/wiki-locators";
import type { TreeFile } from "@/lib/project-tree";

const HTML = "raw/web/page.html";
const PDF = "raw/pdf/book.pdf";

function locator(path: string, blockId: string | null = null): SourceLocator {
  return { path, blockId };
}

function collection(
  id: string,
  sourceKey: string,
  blockIds: string[],
  title = "",
): EvidenceCollection {
  return { id, sourceKey, blockIds, title };
}

function file(path: string, title = ""): TreeFile {
  return { path, name: path.split("/").pop() ?? path, title };
}

describe("citationEntries", () => {
  it("lists one row per cited original, with its passages", () => {
    const entries = citationEntries(
      [locator(HTML, "b2"), locator(HTML, "b5"), locator(PDF, "p3")],
      [],
      [file(HTML), file(PDF)],
    );
    expect(entries.map((entry) => [entry.path, entry.blockIds])).toEqual([
      [HTML, ["b2", "b5"]],
      [PDF, ["p3"]],
    ]);
  });

  it("names a row after the file, not after the selection", () => {
    // The row answers "where did this come from", so the file leads; what the
    // passage was is the second thing it says.
    const entries = citationEntries(
      [locator(HTML, "b2")],
      [collection("c1", HTML, ["b2"], "信息熵（维基）")],
      [file(HTML, "互信息")],
    );
    expect(entries[0]?.name).toBe("互信息");
    expect(entries[0]?.from).toBe("信息熵（维基）");
  });

  it("falls back to the project's own name for the file", () => {
    // No selection matches — the passages were written in an earlier session —
    // so the file is all there is, and it is named from the project.
    const entries = citationEntries([locator(HTML, "b2")], [], [file(HTML, "互信息")]);
    expect(entries[0]?.name).toBe("互信息");
    expect(entries[0]?.from).toBe("");
  });

  it("falls back to the file's name when the project does not describe it", () => {
    const entries = citationEntries([locator(HTML, "b2")], [], []);
    expect(entries[0]?.name).toBe("page");
    expect(entries[0]?.exists).toBe(false);
  });

  it("merges a file cited by two selections into one row", () => {
    const entries = citationEntries(
      [locator(HTML, "b2"), locator(HTML, "b9")],
      [
        collection("c1", HTML, ["b2"], "first"),
        collection("c2", HTML, ["b9"], "second"),
      ],
      [file(HTML)],
    );
    expect(entries).toHaveLength(1);
    expect(entries[0]?.blockIds).toEqual(["b2", "b9"]);
    expect(entries[0]?.from).toBe("first");
  });

  it("counts a passage cited twice once", () => {
    const entries = citationEntries(
      [locator(HTML, "b2"), locator(HTML, "b2")],
      [],
      [file(HTML)],
    );
    expect(entries[0]?.blockIds).toEqual(["b2"]);
  });

  it("marks an original the project no longer has", () => {
    const entries = citationEntries(
      [locator(HTML, "b2"), locator(PDF, "p1")],
      [],
      [file(HTML)],
    );
    expect(entries.map((entry) => [entry.path, entry.exists])).toEqual([
      [HTML, true],
      [PDF, false],
    ]);
  });

  it("keeps a whole-file citation as a row without passages", () => {
    const entries = citationEntries([locator(PDF)], [], [file(PDF)]);
    expect(entries[0]?.blockIds).toEqual([]);
  });

  it("says nothing about a page that cites nothing", () => {
    expect(citationEntries([], [], [file(HTML)])).toEqual([]);
  });

  it("leaves out a citation that cannot be rendered", () => {
    // A page may name something the documents view cannot show; offering to open
    // it would be a promise the view cannot keep.
    expect(citationEntries([locator("wiki/concepts/other.md", "b1")], [], [])).toEqual([]);
  });
});

describe("evidenceFrom", () => {
  const catalog = [
    { id: "c1", sourceKey: HTML, sourceLabel: "互信息（维基）", blockIds: ["b2", "b3"] },
  ];

  it("names a project-recorded selection after its own label", () => {
    // This is what resolves a page's section markers once a round has ended: the
    // project's record is the only place the names are still known.
    expect(evidenceFrom(catalog, [])).toEqual([
      { id: "c1", sourceKey: HTML, blockIds: ["b2", "b3"], title: "互信息（维基）" },
    ]);
  });

  it("adds this session's selections as well", () => {
    const merged = evidenceFrom(catalog, [
      { id: "s1", sourceKey: PDF, blockIds: ["p1"], title: "本轮" },
    ]);
    expect(merged.map((item) => item.id).sort()).toEqual(["c1", "s1"]);
  });

  it("lets this session's version win when both describe one selection", () => {
    const merged = evidenceFrom(catalog, [
      { id: "c1", sourceKey: HTML, blockIds: ["b9"], title: "改过了" },
    ]);
    expect(merged).toEqual([
      { id: "c1", sourceKey: HTML, blockIds: ["b9"], title: "改过了" },
    ]);
  });

  it("keeps only selections that point at a stored source", () => {
    // A selection still keyed by its URL has no file for a page to open.
    const merged = evidenceFrom(
      [],
      [{ id: "s1", sourceKey: "preview:https://example.com/a", blockIds: ["b1"], title: "x" }],
    );
    expect(merged).toEqual([]);
  });

  it("works with no record at all", () => {
    expect(evidenceFrom(undefined, [])).toEqual([]);
  });
});

describe("marksForSource", () => {
  const bundle = {
    notes: [{ path: "wiki/concepts/b.md", title: "乙" }],
    marks: [
      {
        id: "m2",
        path: "wiki/concepts/b.md",
        title: "乙",
        heading: "第二部分",
        collectionId: "c2",
        blockIds: ["b7"],
      },
      {
        id: "m1",
        path: "wiki/concepts/a.md",
        title: "甲",
        heading: "",
        collectionId: "c1",
        blockIds: ["b2", "b3"],
      },
    ],
  };

  it("orders marks so the numbers do not shuffle", () => {
    // By note name, then by heading: the rail is numbered, so an order that
    // changed between renders would renumber itself.
    expect(marksForSource(bundle).map((mark) => mark.id)).toEqual(["m1", "m2"]);
  });

  it("carries what a mark needs to open the note at the right place", () => {
    const [, second] = marksForSource(bundle);
    expect(second).toMatchObject({
      notePath: "wiki/concepts/b.md",
      noteTitle: "乙",
      heading: "第二部分",
      collectionId: "c2",
    });
  });

  it("falls back to the path when a note has no name", () => {
    const marks = marksForSource({
      notes: [],
      marks: [
        { id: "m", path: "wiki/a.md", title: "", heading: "", collectionId: "", blockIds: [] },
      ],
    });
    expect(marks[0]?.noteTitle).toBe("wiki/a.md");
  });

  it("keeps only the passages the source actually has", () => {
    // A source can be re-derived, so a citation may name passages that are no
    // longer there; keeping them would place a mark nowhere.
    const marks = marksForSource(bundle, ["b2", "b7"]);
    expect(marks.map((mark) => mark.blockIds)).toEqual([["b2"], ["b7"]]);
  });

  it("leaves passages alone when the caller has no order to check against", () => {
    expect(marksForSource(bundle)[0]?.blockIds).toEqual(["b2", "b3"]);
  });

  it("gives a mark an identity even when the record has none", () => {
    const marks = marksForSource({
      notes: [],
      marks: [
        { id: "", path: "wiki/a.md", title: "甲", heading: "H", collectionId: "c", blockIds: [] },
      ],
    });
    expect(marks[0]?.id).toBe("wiki/a.md::c::0");
  });

  it("says nothing when there are no citations", () => {
    expect(marksForSource(undefined)).toEqual([]);
    expect(marksForSource({ notes: [], marks: [] })).toEqual([]);
  });
});
