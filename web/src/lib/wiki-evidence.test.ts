import { describe, expect, it } from "vitest";
import {
  bindWikiSections,
  groupWikiEvidence,
  mergeEvidenceCollections,
  stripClipMarkers,
  type EvidenceCollection,
} from "@/lib/wiki-evidence";

function collection(overrides: Partial<EvidenceCollection> = {}): EvidenceCollection {
  return {
    id: "c1",
    sourceKey: "raw/web/a.html",
    blockIds: ["b1", "b2"],
    title: "Page A",
    ...overrides,
  };
}

const KL_BLOCKS = [
  { id: "b1", text: "The KL divergence measures how one distribution differs from a reference." },
  { id: "b2", text: "It is always non-negative, by Gibbs inequality." },
];

describe("mergeEvidenceCollections", () => {
  it("keeps only selections backed by a stored original", () => {
    const merged = mergeEvidenceCollections(
      [collection({ id: "stored" })],
      [collection({ id: "preview", sourceKey: "preview:https://a.com" })],
    );
    expect(merged.map((item) => item.id)).toEqual(["stored"]);
  });

  it("lets the later entry win", () => {
    const merged = mergeEvidenceCollections(
      [collection({ title: "from disk" })],
      [collection({ title: "from session" })],
    );
    expect(merged).toHaveLength(1);
    expect(merged[0].title).toBe("from session");
  });

  it("normalises the key and de-duplicates the passages", () => {
    const merged = mergeEvidenceCollections(
      [collection({ sourceKey: "\\raw\\web\\a.html", blockIds: ["b1", "b1", "b2"] })],
      [],
    );
    expect(merged[0].sourceKey).toBe("raw/web/a.html");
    expect(merged[0].blockIds).toEqual(["b1", "b2"]);
  });
});

describe("groupWikiEvidence", () => {
  it("groups the passages a selection accounts for", () => {
    const groups = groupWikiEvidence(
      [
        { path: "raw/web/a.html", blockId: "b1" },
        { path: "raw/web/a.html", blockId: "b2" },
      ],
      [collection()],
    );
    expect(groups).toEqual([
      { id: "c1", title: "Page A", path: "raw/web/a.html", blockIds: ["b1", "b2"] },
    ]);
  });

  it("shows a cited passage the app has no selection for, grouped by file", () => {
    // Selections are discarded when a round ends, so a page can cite passages
    // there is no selection for; hiding them would misrepresent the page.
    const groups = groupWikiEvidence(
      [
        { path: "raw/web/a.html", blockId: "b1" },
        { path: "raw/web/a.html", blockId: "b9" },
      ],
      [collection()],
    );
    expect(groups.map((group) => group.id)).toEqual(["c1", "file:raw/web/a.html"]);
    expect(groups[1].blockIds).toEqual(["b9"]);
    expect(groups[1].title).toBe("a.html");
  });

  it("does not claim a passage the selection does not list", () => {
    const groups = groupWikiEvidence(
      [{ path: "raw/web/a.html", blockId: "b7" }],
      [collection()],
    );
    expect(groups).toHaveLength(1);
    expect(groups[0].id).toBe("file:raw/web/a.html");
  });

  it("gives a whole-file locator its own group", () => {
    expect(groupWikiEvidence([{ path: "raw/pdf/a.pdf", blockId: null }], [])).toEqual([
      {
        id: "file:raw/pdf/a.pdf",
        title: "a.pdf",
        path: "raw/pdf/a.pdf",
        blockIds: [],
      },
    ]);
  });

  it("does not add a second group for a file that already has one", () => {
    const groups = groupWikiEvidence(
      [
        { path: "raw/web/a.html", blockId: null },
        { path: "raw/web/a.html", blockId: "b1" },
      ],
      [collection()],
    );
    // The whole-file locator adds nothing: the reader already sees that file,
    // and the passages shown are the ones the selection claims.
    expect(groups.map((group) => group.id)).toEqual(["c1"]);
    expect(groups[0].blockIds).toEqual(["b1"]);
  });

  it("ignores a locator that is not a renderable original", () => {
    expect(groupWikiEvidence([{ path: "wiki/concepts/a.md", blockId: null }], [])).toEqual([]);
    expect(groupWikiEvidence([{ path: "raw/a.md", blockId: null }], [])).toEqual([]);
  });

  it("returns nothing for a page with no locators", () => {
    expect(groupWikiEvidence([], [collection()])).toEqual([]);
  });
});

describe("stripClipMarkers", () => {
  it("removes the marker lines", () => {
    const body = "<!-- na-clip:c1 b1,b2 -->\n## Definition\n\nProse\n";
    expect(stripClipMarkers(body)).toBe("## Definition\n\nProse\n");
  });

  it("removes a marker without a passage list", () => {
    expect(stripClipMarkers("<!-- na-clip:c1 -->\n## H\n")).toBe("## H\n");
  });

  it("leaves unrelated comments alone", () => {
    const body = "<!-- note -->\nProse\n";
    expect(stripClipMarkers(body)).toBe(body);
  });

  it("removes several markers", () => {
    const body = "<!-- na-clip:c1 -->\n## A\n<!-- na-clip:c2 -->\n## B\n";
    expect(stripClipMarkers(body)).toBe("## A\n## B\n");
  });
});

describe("bindWikiSections", () => {
  const withBlocks = (overrides: Partial<EvidenceCollection> = {}) =>
    collection({ blocks: KL_BLOCKS, ...overrides });

  it("binds a section by its marker", () => {
    const binds = bindWikiSections("<!-- na-clip:c1 b2 -->\n## Definition\n\nProse\n", [
      withBlocks(),
    ]);
    expect(binds).toHaveLength(1);
    expect(binds[0]).toMatchObject({
      heading: "Definition",
      level: 2,
      groupId: "c1",
      title: "Page A",
      confidence: "marked",
      blockIds: ["b2"],
    });
  });

  it("falls back to the whole selection when the marker names no passage", () => {
    const binds = bindWikiSections("<!-- na-clip:c1 -->\n## Definition\n\nProse\n", [
      withBlocks(),
    ]);
    expect(binds[0].blockIds).toEqual(["b1", "b2"]);
    expect(binds[0].confidence).toBe("marked");
  });

  it("marks every section of the page", () => {
    const binds = bindWikiSections(
      [
        "<!-- na-clip:c1 -->",
        "## Definition",
        "",
        "Prose",
        "<!-- na-clip:c1 -->",
        "### Detail",
        "",
        "More prose",
      ].join("\n"),
      [withBlocks()],
    );
    expect(binds.map((bind) => bind.heading)).toEqual(["Definition", "Detail"]);
    expect(binds.map((bind) => bind.level)).toEqual([2, 3]);
    expect(binds.every((bind) => bind.confidence === "marked")).toBe(true);
  });

  it("matches on content when there is no marker", () => {
    const binds = bindWikiSections(
      "## Definition\n\nThe KL divergence measures how one distribution differs from a reference.\n",
      [withBlocks()],
    );
    expect(binds[0].confidence).toBe("matched");
    expect(binds[0].groupId).toBe("c1");
    expect(binds[0].blockIds).toContain("b1");
  });

  it("refuses to guess when two selections fit equally well", () => {
    const binds = bindWikiSections(
      "## Definition\n\nThe KL divergence measures how one distribution differs from a reference.\n",
      [
        withBlocks({ id: "c1", title: "One" }),
        withBlocks({ id: "c2", title: "Two" }),
      ],
    );
    // A wrong binding would point the reader at material it was not written
    // from, so an ambiguous one is reported as unmatched instead.
    expect(binds[0].confidence).toBe("unmatched");
    expect(binds[0].groupId).toBeNull();
  });

  it("reports a section with nothing in common as unmatched", () => {
    const binds = bindWikiSections(
      "## Appendix\n\nCompletely unrelated prose about something else entirely.\n",
      [withBlocks()],
    );
    expect(binds[0]).toMatchObject({
      groupId: null,
      path: null,
      blockIds: [],
      confidence: "unmatched",
    });
  });

  it("ignores a marker naming a selection that is not around", () => {
    const binds = bindWikiSections(
      "<!-- na-clip:gone -->\n## Appendix\n\nUnrelated prose here.\n",
      [withBlocks()],
    );
    expect(binds[0].confidence).toBe("unmatched");
  });

  // A marker owns the heading directly below it: blank lines may separate the
  // two, anything else may not. The kernel reads it the same way, and the two
  // sides have to answer alike or a page would mean different things in the two
  // readers.

  it("lets blank lines sit between a marker and its heading", () => {
    const binds = bindWikiSections("<!-- na-clip:c1 -->\n\n\n## Definition\n\nProse\n", [
      withBlocks(),
    ]);
    expect(binds[0].confidence).toBe("marked");
    expect(binds[0].heading).toBe("Definition");
  });

  it("does not bind across a comment that is not a marker", () => {
    const binds = bindWikiSections(
      "<!-- na-clip:c1 -->\n<!-- note -->\n## Definition\n\nProse\n",
      [withBlocks()],
    );
    expect(binds[0].confidence).toBe("unmatched");
    expect(binds[0].heading).toBe("Definition");
  });

  it("does not read on to a later heading when prose stands in the way", () => {
    const binds = bindWikiSections(
      [
        "## First",
        "",
        "<!-- na-clip:c1 -->",
        "Some prose that comes first.",
        "",
        "## Second",
        "",
        "Unrelated prose about something else entirely.",
      ].join("\n"),
      [withBlocks()],
    );
    expect(binds.map((bind) => bind.confidence)).toEqual(["unmatched", "unmatched"]);
  });

  it("needs the marker to hold its own line", () => {
    const binds = bindWikiSections(
      "Prose first <!-- na-clip:c1 -->\n## Definition\n\nProse\n",
      [withBlocks()],
    );
    expect(binds[0].confidence).toBe("unmatched");
  });

  it("does not hand a marker to the page title", () => {
    const binds = bindWikiSections(
      "<!-- na-clip:c1 -->\n# Page title\n\n## Definition\n\nProse\n",
      [withBlocks()],
    );
    expect(binds).toHaveLength(1);
    expect(binds[0].confidence).toBe("unmatched");
  });

  it("gives the heading to the marker nearest it", () => {
    const binds = bindWikiSections(
      "<!-- na-clip:c1 -->\n<!-- na-clip:c2 -->\n## Definition\n\nProse\n",
      [withBlocks(), withBlocks({ id: "c2", title: "Page C" })],
    );
    expect(binds).toHaveLength(1);
    expect(binds[0].groupId).toBe("c2");
    expect(binds[0].confidence).toBe("marked");
  });

  it("reads the marker the way the kernel does, ignoring case", () => {
    const binds = bindWikiSections("<!-- NA-CLIP:c1 B2 -->\n## Definition\n", [withBlocks()]);
    expect(binds[0].confidence).toBe("marked");
    expect(binds[0].blockIds).toEqual(["b2"]);
  });

  it("returns nothing for a body with no headings", () => {
    expect(bindWikiSections("Just prose, no headings.", [withBlocks()])).toEqual([]);
  });

  it("keeps a section's own text out of the next section's match", () => {
    const binds = bindWikiSections(
      [
        "## First",
        "",
        "The KL divergence measures how one distribution differs from a reference.",
        "",
        "## Second",
        "",
        "Unrelated prose about something else entirely.",
      ].join("\n"),
      [withBlocks()],
    );
    expect(binds[0].confidence).toBe("matched");
    expect(binds[1].confidence).toBe("unmatched");
  });
});
