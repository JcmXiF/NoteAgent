import { describe, expect, it } from "vitest";
import {
  blocksById,
  boardDecisions,
  clipsPayloadFrom,
  collectionHeadline,
  collectionLead,
  collectionRangeLabel,
  collectionTitleForId,
  collectionsByScope,
  countPreviewCollections,
  defaultActionForNode,
  decisionsFromPayload,
  formatCollectionText,
  isPreviewSource,
  orderedBlockUnion,
  planMarqueeClip,
  previewSourceKey,
  previewUrlFromKey,
  sourceMarkup,
  starEdgesFromNodes,
  topicMapEdges,
  topicMapNodes,
  uniqueBlockIds,
  type BlockCollection,
} from "@/lib/clips";
import type { DerivedBlock, TopicMap, TopicNode } from "@/lib/api/types";

function collection(overrides: Partial<BlockCollection> = {}): BlockCollection {
  return {
    id: "col-1",
    project: "demo",
    sourceKey: 'preview:https://example.com/a',
    sourceLabel: "https://example.com/a",
    blockIds: [],
    blocks: [],
    ...overrides,
  };
}

function block(id: string, text: string, tag = "p"): DerivedBlock {
  return { id, tag, text, preview: text.slice(0, 20) };
}

describe("collectionsByScope", () => {
  const mine = collection({ id: "col-round", roundId: "r1" });
  const leftover = collection({ id: "col-old", roundId: "r0" });
  const everyday = collection({ id: "col-day" });
  const all = [mine, leftover, everyday];

  it("keeps the round being run apart from the older ones", () => {
    const scope = collectionsByScope(all, "r1");
    expect(scope.mine.map((item) => item.id)).toEqual(["col-round"]);
    expect(scope.leftover.map((item) => item.id)).toEqual(["col-old"]);
    expect(scope.everyday.map((item) => item.id)).toEqual(["col-day"]);
  });

  it("calls every round's selections leftovers when no round is running", () => {
    // This is what a round that ended without taking its selections leaves behind.
    const scope = collectionsByScope(all, null);
    expect(scope.mine).toEqual([]);
    expect(scope.leftover.map((item) => item.id)).toEqual(["col-round", "col-old"]);
    expect(scope.everyday.map((item) => item.id)).toEqual(["col-day"]);
  });

  it("treats a blank round name as none, on either side", () => {
    const scope = collectionsByScope(
      [collection({ id: "a", roundId: "  " }), collection({ id: "b", roundId: "r1" })],
      "r1",
    );
    expect(scope.everyday.map((item) => item.id)).toEqual(["a"]);
    expect(scope.mine.map((item) => item.id)).toEqual(["b"]);
  });

  it("has nothing in any group when nothing is selected", () => {
    expect(collectionsByScope([], "r1")).toEqual({ mine: [], leftover: [], everyday: [] });
  });
});

describe("locators", () => {
  it("round-trips a preview key", () => {
    const key = previewSourceKey("https://example.com/a");
    expect(key).toBe("preview:https://example.com/a");
    expect(isPreviewSource(key)).toBe(true);
    expect(previewUrlFromKey(key)).toBe("https://example.com/a");
  });

  it("recognises only preview keys", () => {
    expect(isPreviewSource("raw/web/a.html")).toBe(false);
    expect(previewUrlFromKey("raw/web/a.html")).toBeNull();
  });

  it("counts selections that are not stored yet", () => {
    expect(
      countPreviewCollections([
        { sourceKey: previewSourceKey("https://a.com") },
        { sourceKey: "raw/web/a.html" },
        { sourceKey: previewSourceKey("https://b.com") },
      ]),
    ).toBe(2);
  });
});

describe("blocksById", () => {
  it("keeps document order and skips unknown ids", () => {
    const result = blocksById(
      ["b2", "missing", "b1"],
      [[block("b1", "one"), block("b2", "two")]],
    );
    expect(result.map((item) => item.id)).toEqual(["b2", "b1"]);
  });

  it("lets a later list win, because callers pass the freshest extraction last", () => {
    const result = blocksById(
      ["b1"],
      [[block("b1", "stale")], [block("b1", "fresh")]],
    );
    expect(result[0].text).toBe("fresh");
  });
});

describe("formatCollectionText", () => {
  it("emits one tagged line per selected block, in selection order", () => {
    const text = formatCollectionText(
      collection({
        blockIds: ["b2", "b1"],
        blocks: [block("b1", "first"), block("b2", "second")],
      }),
    );
    expect(text).toBe("[b2] second\n\n[b1] first");
  });

  it("drops blocks with no text and ids it cannot resolve", () => {
    const text = formatCollectionText(
      collection({
        blockIds: ["b1", "b2", "b3"],
        blocks: [block("b1", "  "), block("b2", "kept")],
      }),
    );
    expect(text).toBe("[b2] kept");
  });
});

describe("collection labels", () => {
  it("describes an empty, single and multi-block selection", () => {
    expect(collectionRangeLabel(collection()).key).toBe("clips.range.empty");
    expect(collectionRangeLabel(collection({ blockIds: ["b4"] }))).toEqual({
      key: "clips.range.single",
      params: { id: "b4" },
    });
    expect(collectionRangeLabel(collection({ blockIds: ["b4", "b9"] }))).toEqual({
      key: "clips.range.span",
      params: { from: "b4", to: "b9", count: 2 },
    });
  });

  it("prefers the first heading as the headline", () => {
    const headline = collectionHeadline(
      collection({
        blockIds: ["b1", "b2"],
        blocks: [block("b1", "intro"), block("b2", "Real title", "h2")],
      }),
    );
    expect(headline).toBe("Real title");
  });

  it("falls back to the first paragraph, and ignores edit links", () => {
    const headline = collectionHeadline(
      collection({
        blockIds: ["b1", "b2"],
        blocks: [block("b1", "Edit", "h2"), block("b2", "A paragraph")],
      }),
    );
    expect(headline).toBe("A paragraph");
  });

  it("falls back to the source label when only a figure caption was selected", () => {
    const headline = collectionHeadline(
      collection({ blockIds: ["b1"], blocks: [block("b1", "[图 示意]")] }),
    );
    expect(headline).toBe("https://example.com/a");
  });

  it("ignores an edit link whatever its case", () => {
    const headline = collectionHeadline(
      collection({
        blockIds: ["b1", "b2"],
        blocks: [block("b1", "Edit", "h2"), block("b2", "A paragraph")],
      }),
    );
    expect(headline).toBe("A paragraph");
  });

  it("truncates and collapses whitespace", () => {
    const headline = collectionHeadline(
      collection({ blockIds: ["b1"], blocks: [block("b1", "a  b\n c")] }),
      4,
    );
    expect(headline).toBe("a b…");
  });

  it("takes the lead from the paragraph after the headline", () => {
    const lead = collectionLead(
      collection({
        blockIds: ["b1", "b2", "b3"],
        blocks: [
          block("b1", "Title", "h1"),
          block("b2", "Lead paragraph"),
          block("b3", "Body"),
        ],
      }),
    );
    expect(lead).toBe("Lead paragraph");
  });

  it("has no lead when only a heading was selected", () => {
    expect(
      collectionLead(collection({ blockIds: ["b1"], blocks: [block("b1", "Title", "h1")] })),
    ).toBe("");
  });

  it("prefers the plan's own title, then the headline, then the id", () => {
    const col = collection({
      id: "col-1",
      blockIds: ["b1"],
      blocks: [block("b1", "Headline text")],
    });
    expect(
      collectionTitleForId("col-1", [col], [{ id: "col-1", title: " From plan " }]),
    ).toBe("From plan");
    expect(collectionTitleForId("col-1", [col])).toBe("Headline text");
    expect(collectionTitleForId("col-missing", [col])).toBe("col-missing");
  });
});

describe("selection merging", () => {
  it("trims, de-duplicates and keeps order", () => {
    expect(uniqueBlockIds([" b1 ", "b1", "", "b2"])).toEqual(["b1", "b2"]);
  });

  it("unions into document order, then appends anything the document did not know", () => {
    expect(
      orderedBlockUnion([["b3", "b9"], ["b1"]], ["b1", "b2", "b3"]),
    ).toEqual(["b1", "b3", "b9"]);
  });
});

describe("planMarqueeClip", () => {
  const order = ["b1", "b2", "b3", "b4"];
  const existing = [
    { id: "col-1", sourceKey: "s1", blockIds: ["b1", "b2"] },
    { id: "col-2", sourceKey: "s1", blockIds: ["b3"] },
  ];

  it("reports an empty selection", () => {
    expect(planMarqueeClip([], existing, "s1", order)).toEqual({ action: "empty" });
  });

  it("creates a new selection when nothing overlaps", () => {
    expect(planMarqueeClip(["b4"], existing, "s1", order)).toEqual({
      action: "create",
      blockIds: ["b4"],
    });
  });

  it("does nothing when the same blocks are selected again", () => {
    expect(planMarqueeClip(["b2", "b1"], existing, "s1", order)).toEqual({
      action: "noop",
      focusId: "col-1",
    });
  });

  it("merges overlapping selections and keeps the first as the survivor", () => {
    expect(planMarqueeClip(["b2", "b3"], existing, "s1", order)).toEqual({
      action: "merge",
      keepId: "col-1",
      dropIds: ["col-2"],
      blockIds: ["b1", "b2", "b3"],
    });
  });

  it("ignores selections from another source", () => {
    expect(planMarqueeClip(["b1"], existing, "s2", order)).toEqual({
      action: "create",
      blockIds: ["b1"],
    });
  });

  it("keeps deep search rounds isolated from each other and from daily selections", () => {
    const withRound = [
      { id: "col-r1", sourceKey: "s1", blockIds: ["b1"], roundId: "r1" },
    ];
    expect(planMarqueeClip(["b1"], withRound, "s1", order, "r2")).toEqual({
      action: "create",
      blockIds: ["b1"],
    });
    expect(planMarqueeClip(["b1"], withRound, "s1", order, "")).toEqual({
      action: "create",
      blockIds: ["b1"],
    });
    expect(planMarqueeClip(["b1"], withRound, "s1", order, "r1")).toEqual({
      action: "noop",
      focusId: "col-r1",
    });
  });
});

describe("plan helpers", () => {
  function node(overrides: Partial<TopicNode> = {}): TopicNode {
    return {
      title: "Page",
      slug: "page",
      wikiPath: "wiki/concepts/page.md",
      relationToPrimary: "cooccurs",
      collectionIds: ["col-1"],
      blockIds: ["b1"],
      evidence: "because",
      outline: [],
      ...overrides,
    };
  }

  function map(overrides: Partial<TopicMap> = {}): TopicMap {
    return {
      intent: "",
      primary: node({ slug: "main", relationToPrimary: "primary" }),
      alsoInSources: [],
      notes: "",
      ...overrides,
    };
  }

  it("prefers the candidate list, and falls back to primary plus sources", () => {
    const candidates = [node({ slug: "a" }), node({ slug: "b" })];
    expect(topicMapNodes(map({ candidates }))).toHaveLength(2);
    const fallback = topicMapNodes(
      map({ alsoInSources: [node({ slug: "extra" })] }),
    );
    expect(fallback.map((item) => item.slug)).toEqual(["main", "extra"]);
  });

  it("builds star edges onto the primary node, keeping known relations only", () => {
    const edges = starEdgesFromNodes([
      node({ slug: "main", relationToPrimary: "primary" }),
      node({ slug: "dep", relationToPrimary: "prerequisite" }),
      node({ slug: "odd", relationToPrimary: "something-else" }),
    ]);
    expect(edges).toEqual([
      {
        from: "dep",
        to: "main",
        relation: "prerequisite",
        evidence: "because",
        collectionId: "col-1",
        blockIds: ["b1"],
      },
      {
        from: "odd",
        to: "main",
        relation: "cooccurs",
        evidence: "because",
        collectionId: "col-1",
        blockIds: ["b1"],
      },
    ]);
  });

  it("uses the plan's own edges when it has any", () => {
    const own = [{ from: "a", to: "b", relation: "contrast" }];
    expect(topicMapEdges(map({ edges: own }))).toEqual(own);
    expect(topicMapEdges(map()).length).toBe(0);
  });

  it("starts a page at write only when it is the primary or says so", () => {
    expect(defaultActionForNode(node({ relationToPrimary: "primary" }))).toBe("write");
    expect(defaultActionForNode(node({ coverage: "mention" }))).toBe("mention");
    expect(defaultActionForNode(node({ coverage: "skip" }))).toBe("skip");
    expect(defaultActionForNode(node())).toBe("skip");
  });

  it("reads back the decisions stored with a selection", () => {
    expect(
      decisionsFromPayload([
        { slug: "a", action: "write", sectionIds: ["s1"] },
        { slug: "", action: "skip", sectionIds: [] },
      ]),
    ).toEqual({ a: { slug: "a", action: "write", sectionIds: ["s1"] } });
    expect(decisionsFromPayload(undefined)).toEqual({});
  });

  it("puts the reader's own answers over the plan's suggestion", () => {
    const plan = map({
      candidates: [
        node({ slug: "a", outline: [{ id: "s1", title: "One", blockIds: ["b1"] }] }),
        node({ slug: "b" }),
      ],
    });
    expect(
      boardDecisions(plan, {
        a: { slug: "a", action: "mention", sectionIds: ["s1"] },
        gone: { slug: "gone", action: "write", sectionIds: [] },
      }),
    ).toEqual({
      // the answer, not the suggestion — and not the page that is no longer there
      a: { slug: "a", action: "mention", sectionIds: ["s1"] },
      b: { slug: "b", action: "skip", sectionIds: [] },
    });
    expect(boardDecisions(null, {})).toEqual({});
  });
});

describe("sourceMarkup", () => {
  const from = (id: string, sourceKey: string, blockIds: string[]) =>
    collection({ id, sourceKey, sourceLabel: sourceKey, blockIds });

  it("collects the passages of every selection made from the source", () => {
    const markup = sourceMarkup(
      [
        from("a", "raw/web/x.html", ["b1", "b2"]),
        from("b", "raw/web/x.html", ["b2", "b3"]),
        from("c", "raw/web/other.html", ["b9"]),
      ],
      ["raw/web/x.html"],
    );
    expect(markup.selectedIds).toEqual(["b1", "b2", "b3"]);
  });

  it("finds a selection that still carries the page's URL", () => {
    // A selection made before its page was stored is keyed by the URL.
    const markup = sourceMarkup(
      [from("a", "preview:https://example.com/x", ["b1"])],
      ["https://example.com/x", "raw/web/x.html"],
    );
    expect(markup.selectedIds).toEqual(["b1"]);
  });

  it("focuses the first passage of the selection the user was on", () => {
    const markup = sourceMarkup(
      [
        from("a", "raw/web/x.html", ["b1"]),
        from("b", "raw/web/x.html", ["b5", "b6"]),
      ],
      ["raw/web/x.html"],
      "b",
    );
    expect(markup.focusId).toBe("b5");
  });

  it("focuses nothing when that selection is not from this source", () => {
    const markup = sourceMarkup(
      [from("a", "raw/web/x.html", ["b1"])],
      ["raw/web/x.html"],
      "elsewhere",
    );
    expect(markup.focusId).toBeNull();
  });

  it("has nothing to say about a file no selection mentions", () => {
    expect(sourceMarkup([from("a", "raw/other", ["b1"])], ["raw/web/x.html"]))
      .toEqual({ selectedIds: [], focusId: null });
  });
});

describe("clipsPayloadFrom", () => {
  const col = collection({
    id: "col-1",
    blockIds: ["b1"],
    blocks: [block("b1", "text")],
  });
  const decisions = { page: { slug: "page", action: "write" as const, sectionIds: ["s1"] } };

  it("sends an empty confirmation list until the user changes one", () => {
    // Pre-selection is a suggestion; sending it would look like consent.
    const payload = clipsPayloadFrom({
      intent: "i",
      collections: [col],
      topicMap: null,
      decisions,
      decisionsTouched: false,
    });
    expect(payload.decisions).toEqual([]);
  });

  it("sends the confirmations once they have been changed", () => {
    const payload = clipsPayloadFrom({
      intent: "i",
      collections: [col],
      topicMap: null,
      decisions,
      decisionsTouched: true,
    });
    expect(payload.decisions).toEqual([
      { slug: "page", action: "write", sectionIds: ["s1"] },
    ]);
  });

  it("sends the board's answers whether or not the gate has been confirmed", () => {
    // The answers are about the pages, so a later plan can carry them over; they are
    // not the confirmation, which is what the flag is for.
    const payload = clipsPayloadFrom({
      intent: "i",
      collections: [col],
      topicMap: null,
      decisions,
      decisionsTouched: false,
    });
    expect(payload.choices).toEqual([
      { slug: "page", action: "write", sectionIds: ["s1"] },
    ]);
    expect(payload.decisions).toEqual([]);
  });

  it("normalises the optional collection fields the backend expects as strings", () => {
    const payload = clipsPayloadFrom({
      intent: "i",
      collections: [col],
      topicMap: null,
      decisions: {},
      decisionsTouched: false,
    });
    expect(payload.collections[0].roundId).toBe("");
    expect(payload.collections[0].targetSlug).toBe("");
  });

  it("passes the round id through when the selection belongs to a deep search round", () => {
    const payload = clipsPayloadFrom({
      intent: "i",
      collections: [collection({ roundId: "r1", targetSlug: "page" })],
      topicMap: null,
      decisions: {},
      decisionsTouched: false,
    });
    expect(payload.collections[0].roundId).toBe("r1");
    expect(payload.collections[0].targetSlug).toBe("page");
  });
});
