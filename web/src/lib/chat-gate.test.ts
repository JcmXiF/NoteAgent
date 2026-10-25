import { describe, expect, it } from "vitest";
import { writeGate } from "@/lib/chat-gate";
import type { ObservationPayload } from "@/lib/api/client";
import type { TopicMap, TopicNode } from "@/lib/api/types";

function node(patch: Partial<TopicNode> = {}): TopicNode {
  return {
    slug: "entropy",
    title: "信息熵",
    wikiPath: "",
    relationToPrimary: "primary",
    coverage: "write",
    outline: [],
    sources: [],
    ...patch,
  } as TopicNode;
}

const map: TopicMap = {
  intent: "整理熵",
  primary: node(),
  alsoInSources: [node({ slug: "kl-divergence", title: "KL散度", coverage: "skip" })],
  notes: "",
};

const write = (patch: Partial<ObservationPayload> = {}): ObservationPayload => ({
  tool: "wiki_write",
  title: "propose_topic_map",
  action: "propose_topic_map",
  wrote_wiki: false,
  needs_human: true,
  ...patch,
});

describe("when writing never came up", () => {
  it("there is no gate to show", () => {
    expect(writeGate([], map)).toBeNull();
    expect(writeGate([{ tool: "search" }], map)).toBeNull();
  });
});

describe("pages waiting to be confirmed", () => {
  it("says the assistant proposed them", () => {
    const gate = writeGate([write()], map);
    expect(gate?.state).toBe("proposed");
  });

  it("lists what is waiting, with what the plan suggests", () => {
    const gate = writeGate([write()], map);
    expect(gate?.pages.map((page) => page.title)).toEqual(["信息熵", "KL散度"]);
    expect(gate?.pages.map((page) => page.suggested)).toEqual(["write", "skip"]);
  });

  it("carries the sections the plan offers, so a choice here does not lose them", () => {
    // The board defaults a page's sections to its outline; a decision recorded
    // without them would come back as a page with nothing to write.
    const withOutline: TopicMap = {
      ...map,
      primary: node({
        outline: [
          { id: "s1", title: "定义", blockIds: [] },
          { id: "s2", title: "性质", blockIds: [] },
        ],
      } as Partial<TopicNode>),
    };
    const gate = writeGate([write()], withOutline);
    expect(gate?.pages[0]?.sectionIds).toEqual(["s1", "s2"]);
  });

  it("has no sections to carry when the plan offers none", () => {
    expect(writeGate([write()], map)?.pages[0]?.sectionIds).toEqual([]);
  });

  it("has nothing to list when no plan is on disk", () => {
    // The gate still says nothing was written; it just cannot show what.
    const gate = writeGate([write()], null);
    expect(gate?.state).toBe("proposed");
    expect(gate?.pages).toEqual([]);
  });

  it("reads a plan whose candidates are given rather than implied", () => {
    const withCandidates: TopicMap = {
      ...map,
      candidates: [node({ slug: "a", title: "甲" }), node({ slug: "b", title: "乙" })],
    };
    expect(writeGate([write()], withCandidates)?.pages.map((page) => page.title)).toEqual([
      "甲",
      "乙",
    ]);
  });
});

describe("a write the gate stopped", () => {
  it("is not the same as one that happened", () => {
    const gate = writeGate([write({ action: "ingest_confirmed" })], map);
    expect(gate?.state).toBe("refused");
  });

  it("still shows what is waiting", () => {
    expect(writeGate([write({ action: "ingest_confirmed" })], map)?.pages).toHaveLength(2);
  });
});

describe("a write that went through", () => {
  it("says so rather than asking for anything", () => {
    const gate = writeGate([write({ action: "ingest_confirmed", wrote_wiki: true })], map);
    expect(gate?.state).toBe("wrote");
  });

  it("is believed even when an earlier round was stopped", () => {
    // Two rounds in one turn: the first was refused, the second went through after
    // the reader ticked. What happened last is what is true.
    const gate = writeGate(
      [
        write({ action: "ingest_confirmed" }),
        write({ action: "ingest_confirmed", wrote_wiki: true }),
      ],
      map,
    );
    expect(gate?.state).toBe("wrote");
  });
});
