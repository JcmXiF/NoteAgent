import { describe, expect, it } from "vitest";
import { cardOf, lookedCards } from "@/lib/chat-cards";
import type { ObservationPayload } from "@/lib/api/client";

function obs(patch: Partial<ObservationPayload> = {}): ObservationPayload {
  return { tool: "wiki_read", title: "熵", target: "[[concepts/entropy]]", ...patch };
}

describe("a page the assistant opened", () => {
  it("is a card that opens in the reader", () => {
    const card = cardOf(obs({ path: "wiki/concepts/entropy.md" }));
    expect(card?.kind).toBe("page");
    expect(card?.label).toBe("熵");
    expect(card?.path).toBe("wiki/concepts/entropy.md");
    expect(card?.actions).toEqual(["doc"]);
  });

  it("takes the page from the link when no path was given", () => {
    expect(cardOf(obs())?.path).toBe("concepts/entropy");
  });

  it("is not offered when there is no page to open", () => {
    expect(cardOf({ tool: "wiki_read", title: "", target: "" })).toBeNull();
  });

  it("says so when the page was not there, and offers nothing", () => {
    const card = cardOf(obs({ missing: true, path: "wiki/concepts/gone.md" }));
    expect(card?.kind).toBe("page");
    expect(card?.actions).toEqual([]);
    expect(card?.note).toBe("chat.looked.missing");
    expect(card?.path).toBeNull();
  });
});

describe("a search inside the notes", () => {
  it("is a page card, because a hit is a page", () => {
    const card = cardOf({
      tool: "search",
      scope: "wiki",
      title: "互信息",
      path: "wiki/concepts/互信息.md",
      target: "[[concepts/互信息]]",
    });
    expect(card?.kind).toBe("page");
    expect(card?.actions).toEqual(["doc"]);
  });
});

describe("a search on the web", () => {
  const hit = {
    tool: "search",
    scope: "web",
    title: "Mutual information - Wikipedia",
    url: "https://en.wikipedia.org/wiki/Mutual_information",
    target: "https://en.wikipedia.org/wiki/Mutual_information",
    not_source: true,
  };

  it("offers the two roads there are: open it, or hand it in to be ticked", () => {
    const card = cardOf(hit);
    expect(card?.kind).toBe("web");
    expect(card?.where).toBe("en.wikipedia.org");
    expect(card?.url).toBe(hit.url);
    expect(card?.actions).toEqual(["browse", "source"]);
  });

  it("says it is a result rather than a source", () => {
    // The whole point of the constraint: a card is not material until the reader
    // makes it so.
    expect(cardOf(hit)?.note).toBe("chat.looked.notSource");
  });

  it("is a card even when the title is the address", () => {
    const card = cardOf({ ...hit, title: "" });
    expect(card?.label).toBe(hit.url);
  });

  it("is not offered without an address", () => {
    expect(cardOf({ tool: "search", scope: "web", title: "x" })).toBeNull();
  });
});

describe("a page the assistant fetched a preview of", () => {
  it("can be opened, and is not handed to the workbench", () => {
    // It was already fetched once; making it a card to fetch again would be a second
    // request the reader did not ask for.
    const card = cardOf({
      tool: "run",
      job: "fetch",
      title: "相对熵",
      url: "https://example.com/kl",
      not_source: true,
    });
    expect(card?.kind).toBe("web");
    expect(card?.actions).toEqual(["browse"]);
  });
});

describe("things that are not places", () => {
  it("shows a wiki check as a row with nothing to open", () => {
    const card = cardOf({ tool: "run", job: "doctor", title: "检查笔记库" });
    expect(card?.kind).toBe("check");
    expect(card?.actions).toEqual([]);
  });

  it("shows a skill it read", () => {
    expect(cardOf({ tool: "skill", title: "summarise" })?.kind).toBe("skill");
  });

  it("leaves writing to the gate, which says it in the reader's words", () => {
    // A write's own name for what it did is not a place and not a name for a reader.
    expect(cardOf({ tool: "wiki_write", title: "propose_topic_map", needs_human: true })).toBeNull();
  });

  it("keeps a tool it has never heard of, because it still happened", () => {
    const card = cardOf({ tool: "brand_new", title: "什么东西" });
    expect(card?.kind).toBe("other");
    expect(card?.label).toBe("什么东西");
    expect(card?.actions).toEqual([]);
  });

  it("drops an unknown tool that said nothing at all", () => {
    expect(cardOf({ tool: "brand_new" })).toBeNull();
  });

  it("says a failed one failed", () => {
    expect(cardOf({ tool: "brand_new", title: "x", error: true })?.note).toBe(
      "chat.looked.failed",
    );
  });
});

describe("a turn's cards", () => {
  it("keeps the order things were looked at", () => {
    const cards = lookedCards([
      obs({ tool: "search", scope: "web", title: "A", url: "https://a.example/1" }),
      obs({ path: "wiki/concepts/entropy.md" }),
      obs({ tool: "search", scope: "web", title: "B", url: "https://b.example/1" }),
    ]);
    expect(cards.map((card) => card.label)).toEqual(["A", "熵", "B"]);
  });

  it("shows a page found and then read as one card", () => {
    // Which is what usually happens: the search finds it, the next round reads it.
    const cards = lookedCards([
      { tool: "search", scope: "wiki", title: "熵", path: "wiki/concepts/entropy.md" },
      obs({ path: "wiki/concepts/entropy.md" }),
    ]);
    expect(cards).toHaveLength(1);
    expect(cards[0]?.path).toBe("wiki/concepts/entropy.md");
  });

  it("keeps the fuller sighting of a page", () => {
    // The search result carried no path; the read did.
    const cards = lookedCards([
      { tool: "search", scope: "wiki", title: "熵" },
      obs({ title: "信息熵", path: "wiki/concepts/entropy.md" }),
    ]);
    expect(cards).toHaveLength(1);
    expect(cards[0]?.path).toBe("wiki/concepts/entropy.md");
    expect(cards[0]?.actions).toEqual(["doc"]);
  });

  it("keeps the place a page was first shown in", () => {
    const cards = lookedCards([
      { tool: "search", scope: "web", title: "A", url: "https://a.example/1" },
      obs({ path: "wiki/concepts/entropy.md" }),
      { tool: "search", scope: "web", title: "A again", url: "https://a.example/1" },
    ]);
    expect(cards.map((card) => card.label)).toEqual(["A again", "熵"]);
  });

  it("tells two pages apart by their path", () => {
    const cards = lookedCards([
      obs({ title: "熵", path: "wiki/concepts/entropy.md" }),
      obs({ title: "相对熵", path: "wiki/concepts/kl.md" }),
    ]);
    expect(cards).toHaveLength(2);
  });

  it("is empty when there is nothing worth showing", () => {
    expect(lookedCards([])).toEqual([]);
    expect(lookedCards([{ tool: "wiki_read" }])).toEqual([]);
  });
});
