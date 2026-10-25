import { describe, expect, it } from "vitest";
import {
  buildOverlay,
  cardOwner,
  cardsToShow,
  matchesNodeId,
  overlayBadge,
  overlayEdges,
  overlayIsEmpty,
  overlayLegend,
  overlayMarkOf,
  overlayNodes,
  overlayPageList,
  pickedCardCount,
} from "@/lib/graph-overlay";
import type {
  DeepSearchCard,
  DeepSearchSession,
  WikiGraphEdge,
  WikiGraphNode,
} from "@/lib/api/types";

function session(over: Partial<DeepSearchSession> = {}): DeepSearchSession {
  return {
    version: 2,
    roundId: "round-1",
    mode: "explore",
    step: 2,
    intent: "",
    seed: null,
    whitelist: [],
    whitelistOmitted: 0,
    overlayEdges: [],
    proposedBlocks: [],
    cards: [],
    cardsOmitted: 0,
    collectionIds: [],
    ingestedIds: [],
    truncated: false,
    notice: "",
    ...over,
  };
}

function page(slug: string, action: "add" | "revise" = "add", accepted = true) {
  return { slug, title: slug, action, plan: "", query: "", section: "", accepted };
}

function node(id: string, over: Partial<WikiGraphNode> = {}): WikiGraphNode {
  return { id, path: `wiki/${id}.md`, title: id, type: "note", ghost: false, degree: 1, ...over };
}

describe("buildOverlay", () => {
  it("reads the pages a round wants to touch, by slug", () => {
    const overlay = buildOverlay(session({ whitelist: [page("concepts/a"), page("concepts/b")] }));
    expect([...overlay.pages.keys()]).toEqual(["concepts/a", "concepts/b"]);
    expect(overlay.pages.get("concepts/a")?.accepted).toBe(true);
  });

  it("keeps the action as revise only when the core said so", () => {
    const overlay = buildOverlay(session({ whitelist: [page("a", "revise"), page("b")] }));
    expect(overlay.pages.get("a")?.action).toBe("revise");
    expect(overlay.pages.get("b")?.action).toBe("add");
  });

  it("keys the proposed links the way edges are keyed", () => {
    const overlay = buildOverlay(
      session({ overlayEdges: [{ source: "a", target: "b", relation: "见" }] }),
    );
    expect([...overlay.edges]).toEqual(["a\u0000b"]);
  });

  it("skips a page with no slug and a link with no end", () => {
    const overlay = buildOverlay(
      session({
        whitelist: [page(""), page("a")],
        overlayEdges: [{ source: "a", target: "", relation: "见" }],
      }),
    );
    expect([...overlay.pages.keys()]).toEqual(["a"]);
    expect(overlay.edges.size).toBe(0);
  });

  it("has nothing to draw when there is no round", () => {
    expect(buildOverlay(null).pages.size).toBe(0);
    expect(buildOverlay(session({ roundId: "" })).edges.size).toBe(0);
    expect(overlayIsEmpty(buildOverlay(null))).toBe(true);
  });
});

describe("overlayNodes", () => {
  it("adds the pages the graph does not have yet, as pages not written", () => {
    const overlay = buildOverlay(session({ whitelist: [page("a"), page("concepts/new")] }));
    const out = overlayNodes([node("a")], overlay);
    expect(out.map((item) => item.id)).toEqual(["a", "concepts/new"]);
    const added = out[1];
    expect(added?.ghost).toBe(true);
    expect(added?.path).toBeNull();
    expect(added?.title).toBe("concepts/new");
  });

  it("does not add a second node for a page that is already there", () => {
    const overlay = buildOverlay(session({ whitelist: [page("a")] }));
    expect(overlayNodes([node("a")], overlay)).toHaveLength(1);
  });

  it("leaves the graph as it is when there is nothing to add", () => {
    const nodes = [node("a")];
    expect(overlayNodes(nodes, buildOverlay(null))).toBe(nodes);
  });
});

describe("overlayEdges", () => {
  const edges: WikiGraphEdge[] = [{ source: "a", target: "b", count: 2 }];

  it("adds a link the graph does not have", () => {
    const overlay = buildOverlay(
      session({ overlayEdges: [{ source: "a", target: "c", relation: "见" }] }),
    );
    const out = overlayEdges(edges, overlay);
    expect(out).toHaveLength(2);
    expect(out[1]).toEqual({ source: "a", target: "c", count: 1 });
  });

  it("does not draw a link the graph already has", () => {
    // The round is adding to the graph, not redrawing it.
    const overlay = buildOverlay(
      session({ overlayEdges: [{ source: "a", target: "b", relation: "见" }] }),
    );
    expect(overlayEdges(edges, overlay)).toBe(edges);
  });

  it("leaves the links as they are when there is nothing to add", () => {
    expect(overlayEdges(edges, buildOverlay(null))).toBe(edges);
  });
});

describe("overlayMarkOf", () => {
  it("says how a proposed page should be drawn", () => {
    const overlay = buildOverlay(
      session({ whitelist: [page("a", "add", false), page("b", "revise")] }),
    );
    expect(overlayMarkOf("a", overlay)).toEqual({ action: "add", accepted: false });
    expect(overlayMarkOf("b", overlay)).toEqual({ action: "revise", accepted: true });
  });

  it("says nothing about a page the round is not touching", () => {
    expect(overlayMarkOf("z", buildOverlay(session({ whitelist: [page("a")] })))).toBeNull();
  });
});

describe("overlayPageList", () => {
  it("keeps the order the core proposed", () => {
    const overlay = buildOverlay(session({ whitelist: [page("b"), page("a")] }));
    expect(overlayPageList(overlay).map((item) => item.slug)).toEqual(["b", "a"]);
  });
});

describe("overlayLegend", () => {
  it("names only what is actually on the graph", () => {
    expect(overlayLegend(buildOverlay(session({ whitelist: [page("a")] })))).toEqual(["add"]);
    expect(
      overlayLegend(buildOverlay(session({ whitelist: [page("a", "revise")] }))),
    ).toEqual(["revise"]);
  });

  it("includes the proposed links when there are any", () => {
    const overlay = buildOverlay(
      session({
        whitelist: [page("a", "revise")],
        overlayEdges: [{ source: "a", target: "b", relation: "见" }],
      }),
    );
    expect(overlayLegend(overlay)).toEqual(["revise", "link"]);
  });

  it("names nothing when there is no overlay", () => {
    expect(overlayLegend(buildOverlay(null))).toEqual([]);
  });
});

function card(pageSlug: string, url: string, picked = false): DeepSearchCard {
  return { pageSlug, title: url, url, snippet: "", site: "example.com", picked };
}

describe("matchesNodeId", () => {  it("reads a page name the way a model writes it", () => {
    // The core uses graph ids; a model writes the name a person would.
    expect(matchesNodeId("wiki/concepts/entropy", "entropy")).toBe(true);
    expect(matchesNodeId("entropy", "concepts/entropy")).toBe(true);
    expect(matchesNodeId("concepts/entropy", "concepts/entropy")).toBe(true);
  });

  it("does not match a different page", () => {
    expect(matchesNodeId("information", "entropy")).toBe(false);
    expect(matchesNodeId("concepts/a", "concepts/b")).toBe(false);
  });

  it("treats an empty name as no match", () => {
    expect(matchesNodeId("", "entropy")).toBe(false);
    expect(matchesNodeId("entropy", "")).toBe(false);
  });
});

describe("cardOwner", () => {
  it("prefers the page the model named exactly", () => {
    // Testing found this: a round proposed both, and the looser comparison put
    // every card on whichever came first.
    const slugs = ["concepts/alpha", "entities/alpha"];
    expect(cardOwner("entities/alpha", slugs)).toBe("entities/alpha");
    expect(cardOwner("concepts/alpha", slugs)).toBe("concepts/alpha");
  });

  it("falls back to the one page that answers to a name written without folders", () => {
    expect(cardOwner("wiki/concepts/entropy", ["concepts/entropy"])).toBe("concepts/entropy");
    expect(cardOwner("entropy", ["concepts/entropy"])).toBe("concepts/entropy");
  });

  it("gives up when a name without folders fits more than one page", () => {
    // `alpha` is not evidence for either of them.
    expect(cardOwner("alpha", ["concepts/alpha", "entities/alpha"])).toBe("");
  });

  it("gives up on a name that fits none of them", () => {
    expect(cardOwner("somewhere/else", ["concepts/alpha"])).toBe("");
    expect(cardOwner("", ["concepts/alpha"])).toBe("");
  });
});

describe("cards and their pages", () => {
  const session_ = (over: Partial<DeepSearchSession>) =>
    buildOverlay(
      session({ whitelist: [page("Alpha"), page("concepts/beta")], ...over }),
    );

  it("gives each card to the page it names", () => {
    const overlay = session_({
      cards: [card("Alpha", "u1"), card("wiki/concepts/beta", "u2")],
    });
    expect([...overlay.cards.keys()]).toEqual(["Alpha", "concepts/beta"]);
    expect(overlay.cards.get("Alpha")?.map((item) => item.url)).toEqual(["u1"]);
    expect(overlay.looseCards).toEqual([]);
  });

  it("keeps two pages apart when a name without folders would fit both", () => {
    // A round that proposes both `concepts/alpha` and `entities/alpha`: the cards
    // name their page exactly, so they must not be pooled onto one of them.
    const overlay = buildOverlay(
      session({
        whitelist: [page("concepts/alpha"), page("entities/alpha")],
        cards: [
          card("concepts/alpha", "u1"),
          card("entities/alpha", "u2"),
          card("entities/alpha", "u3"),
        ],
      }),
    );
    expect(overlay.cards.get("concepts/alpha")?.map((item) => item.url)).toEqual(["u1"]);
    expect(overlay.cards.get("entities/alpha")?.map((item) => item.url)).toEqual(["u2", "u3"]);
  });

  it("keeps material that names no page of this round, rather than dropping it", () => {
    const overlay = session_({ cards: [card("somewhere/else", "u1")] });
    expect(overlay.cards.size).toBe(0);
    expect(overlay.looseCards.map((item) => item.url)).toEqual(["u1"]);
  });

  it("drops a card with no page name at all", () => {
    // Nothing to say about it, and it cannot be shown under a page.
    expect(session_({ cards: [card("", "u1")] }).looseCards).toEqual([]);
  });

  it("counts what was taken out of what the page was given", () => {
    const overlay = session_({
      cards: [card("Alpha", "u1", true), card("Alpha", "u2"), card("Alpha", "u3", true)],
    });
    expect(overlayBadge("Alpha", overlay)).toEqual({ picked: 2, total: 3 });
  });

  it("has no badge for a page with no material", () => {
    const overlay = session_({ cards: [card("Alpha", "u1")] });
    expect(overlayBadge("concepts/beta", overlay)).toBeNull();
    expect(overlayBadge("unknown", overlay)).toBeNull();
  });

  it("counts what was taken across the whole round", () => {
    const overlay = session_({
      cards: [card("Alpha", "u1", true), card("Alpha", "u2"), card("somewhere/else", "u3", true)],
    });
    expect(pickedCardCount(overlay)).toBe(2);
  });
});

describe("cardsToShow", () => {
  const overlay = buildOverlay(
    session({
      whitelist: [page("Alpha"), page("concepts/beta")],
      cards: [
        card("Alpha", "u1"),
        card("Alpha", "u2"),
        card("concepts/beta", "u3"),
        card("somewhere/else", "u4"),
      ],
    }),
  );

  it("shows every page's material when nothing is picked out", () => {
    const groups = cardsToShow(overlay, null);
    expect(groups.map((group) => group.slug)).toEqual(["Alpha", "concepts/beta", ""]);
    expect(groups[2]?.cards.map((item) => item.url)).toEqual(["u4"]);
  });

  it("shows only the page's material while that page is picked out", () => {
    expect(cardsToShow(overlay, "Alpha")).toEqual([
      { slug: "Alpha", cards: overlay.cards.get("Alpha") },
    ]);
  });

  it("shows everything while a page the round is not proposing is picked out", () => {
    // Picking out an ordinary page says nothing about this round's material, so
    // filtering by it would hide cards for no reason.
    expect(cardsToShow(overlay, "somewhere/else").map((group) => group.slug)).toEqual([
      "Alpha",
      "concepts/beta",
      "",
    ]);
  });

  it("shows nothing while a proposed page with no material is picked out", () => {
    const without = buildOverlay(
      session({ whitelist: [page("Alpha"), page("concepts/beta")], cards: [card("Alpha", "u1")] }),
    );
    expect(cardsToShow(without, "concepts/beta")).toEqual([]);
  });
});
