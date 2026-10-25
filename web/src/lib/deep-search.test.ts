import { describe, expect, it } from "vitest";
import {
  blockPreview,
  currentStepKey,
  groupBlocksByFile,
  hasWritten,
  pendingActionOf,
  progressForAction,
  rewindAction,
  roundCounts,
  roundIdForSelection,
  roundSteps,
  roundSummary,
  stepLabels,
  truncationMessages,
  uiStepOf,
} from "@/lib/deep-search";
import type { DeepSearchSession } from "@/lib/api/types";

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

function page(accepted: boolean) {
  return { slug: "a", title: "A", action: "add" as const, plan: "", query: "", section: "", accepted };
}

describe("stepLabels", () => {
  it("gives explore five steps and chew four", () => {
    expect(stepLabels("explore")).toHaveLength(5);
    expect(stepLabels("chew")).toHaveLength(4);
  });

  it("treats an unset mode as explore", () => {
    expect(stepLabels("")).toHaveLength(5);
  });
});

describe("uiStepOf", () => {
  it("counts a step at a time for explore", () => {
    expect(uiStepOf("explore", 2)).toBe(2);
    expect(uiStepOf("explore", 4)).toBe(4);
  });

  it("maps a chew round, which starts at the core's third step", () => {
    // Shown steps: range / material / plan / wrap-up.
    expect(uiStepOf("chew", 3)).toBe(2);
    expect(uiStepOf("chew", 4)).toBe(3);
  });

  it("keeps a chew round on its first shown step until it has started", () => {
    expect(uiStepOf("chew", 0)).toBe(1);
    expect(uiStepOf("chew", 1)).toBe(1);
  });

  it("puts anything past the core's steps on the last shown one", () => {
    expect(uiStepOf("chew", 9)).toBe(4);
    expect(uiStepOf("explore", 9)).toBe(9);
  });
});

describe("roundSteps", () => {
  it("marks the step being worked on and the ones behind it", () => {
    const steps = roundSteps("explore", 3);
    expect(steps.map((item) => item.state)).toEqual([
      "done",
      "done",
      "current",
      "pending",
      "pending",
    ]);
  });

  it("marks every step done once the pages are written", () => {
    // A written round has nothing ahead of the reader.
    expect(roundSteps("explore", 4, true).every((item) => item.state === "done")).toBe(true);
  });

  it("numbers the steps as the reader counts them", () => {
    expect(roundSteps("chew", 4).map((item) => item.id)).toEqual(["1", "2", "3", "4"]);
  });
});

describe("rewindAction", () => {
  it("starts the round over when the first step is clicked", () => {
    expect(rewindAction("explore", 1, 4, 5, false)).toEqual({ action: "restart" });
  });

  it("rewinds to the step clicked", () => {
    expect(rewindAction("explore", 3, 4, 5, false)).toEqual({ action: "redo", step: 3 });
  });

  it("maps a chew round's shown steps back to the core's numbering", () => {
    expect(rewindAction("chew", 2, 4, 4, false)).toEqual({ action: "redo", step: 3 });
    expect(rewindAction("chew", 3, 4, 4, false)).toEqual({ action: "redo", step: 4 });
  });

  it("takes nothing back once pages are written", () => {
    expect(rewindAction("explore", 2, 4, 5, true)).toBeNull();
  });

  it("takes nothing back from the step the reader is on, or the one ahead", () => {
    expect(rewindAction("explore", 4, 4, 5, false)).toBeNull();
    expect(rewindAction("explore", 5, 4, 5, false)).toBeNull();
  });

  it("has nothing to rewind to when a chew round is on its last step", () => {
    expect(rewindAction("chew", 4, 5, 4, false)).toBeNull();
  });
});

describe("pendingActionOf", () => {
  it("asks to start when there is no round", () => {
    expect(pendingActionOf(null)).toEqual({ kind: "start" });
    expect(pendingActionOf(session({ roundId: "" }))).toEqual({ kind: "start" });
  });

  it("proposes the pages, then accepts them", () => {
    expect(pendingActionOf(session({ step: 2 }))).toEqual({
      kind: "action",
      action: "proposeWhitelist",
    });
    expect(pendingActionOf(session({ step: 2, whitelist: [page(true)] }))).toEqual({
      kind: "action",
      action: "acceptWhitelist",
    });
  });

  it("proposes the leftover blocks, then accepts them", () => {
    const chew = { mode: "chew", step: 3 };
    expect(pendingActionOf(session(chew))).toEqual({ kind: "action", action: "proposeBlocks" });
    const withBlocks = session({
      ...chew,
      proposedBlocks: [{ pageSlug: "a", locator: "a#b1", preview: "x", accepted: true }],
    });
    expect(pendingActionOf(withBlocks)).toEqual({ kind: "action", action: "acceptBlocks" });
  });

  it("goes to the plan once an explore round has material to plan from", () => {
    // An explore round searched for material on the way here, so the step needs a
    // count the round itself does not carry: the round has nothing to plan from
    // until the reader has picked some of it.
    expect(pendingActionOf(session({ step: 3 }))).toEqual({ kind: "gather" });
    expect(pendingActionOf(session({ step: 3 }), 2)).toEqual({
      kind: "action",
      action: "enterPlan",
    });
  });

  it("says the pages are to be written, not which action writes them", () => {
    // Writing goes through the compile path rather than the round.
    expect(pendingActionOf(session({ step: 4 }))).toEqual({ kind: "write" });
  });

  it("asks to end the round once its pages are written", () => {
    expect(pendingActionOf(session({ step: 4, ingestedIds: ["c1"] }))).toEqual({ kind: "end" });
    expect(pendingActionOf(session({ step: 5 }))).toEqual({ kind: "end" });
  });
});

describe("roundCounts", () => {
  it("counts what the round holds and what was accepted", () => {
    const counts = roundCounts(
      session({
        whitelist: [page(true), page(false)],
        proposedBlocks: [
          { pageSlug: "a", locator: "a#b1", preview: "", accepted: true },
        ],
        cards: [
          { pageSlug: "a", title: "t", url: "u", snippet: "", site: "", picked: true },
          { pageSlug: "a", title: "t2", url: "u2", snippet: "", site: "", picked: false },
        ],
      }),
    );
    expect(counts).toEqual({
      pages: 2,
      pagesAccepted: 1,
      blocks: 1,
      blocksAccepted: 1,
      cards: 2,
      cardsPicked: 1,
    });
  });

  it("counts nothing for a round that is not there", () => {
    expect(roundCounts(null).pages).toBe(0);
  });
});

describe("truncationMessages", () => {
  it("says what was left out, per kind", () => {
    const told = truncationMessages(
      session({ whitelistOmitted: 3, cardsOmitted: 2, truncated: true }),
    );
    expect(told.map((item) => item.key)).toEqual([
      "deepSearch.omitted.pages",
      "deepSearch.omitted.cards",
      "deepSearch.omitted.truncated",
    ]);
    expect(told[0]?.params).toEqual({ count: 3 });
  });

  it("says nothing when nothing was left out", () => {
    expect(truncationMessages(session())).toEqual([]);
    expect(truncationMessages(null)).toEqual([]);
  });
});

describe("progressForAction", () => {
  it("says what is happening for every action a round can take", () => {
    for (const action of [
      "start",
      "proposeWhitelist",
      "acceptWhitelist",
      "proposeBlocks",
      "acceptBlocks",
      "enterPlan",
      "markIngested",
      "end",
      "restart",
      "redo",
    ]) {
      const found = progressForAction(action);
      expect(found, action).not.toBeNull();
      expect(found?.key.startsWith("deepSearch.progress.")).toBe(true);
    }
  });

  it("gives the two page actions their own wording", () => {
    // They are the same step of the round but not the same thing to say.
    expect(progressForAction("proposeWhitelist")?.key).not.toBe(
      progressForAction("acceptWhitelist")?.key,
    );
  });

  it("has nothing to say about an action it does not know", () => {
    expect(progressForAction("clear")).toBeNull();
    expect(progressForAction("")).toBeNull();
  });
});

describe("roundIdForSelection", () => {
  it("gives new selections to the round that is gathering material", () => {
    expect(roundIdForSelection(session({ step: 3 }))).toBe("round-1");
    expect(roundIdForSelection(session({ step: 4 }))).toBe("round-1");
  });

  it("leaves a selection out of a round that is not gathering", () => {
    // Before the round wants material, and after its pages are written, what the
    // reader picks is theirs rather than the round's.
    expect(roundIdForSelection(session({ step: 2 }))).toBe("");
    expect(roundIdForSelection(session({ step: 5 }))).toBe("");
  });

  it("gives nothing to a round that is not there", () => {
    expect(roundIdForSelection(null)).toBe("");
    expect(roundIdForSelection(session({ roundId: "  " }))).toBe("");
  });
});

describe("roundSummary", () => {
  it("says how much was taken out of how much is there, per kind", () => {
    const told = roundSummary(
      session({
        whitelist: [page(true), page(false)],
        proposedBlocks: [
          { pageSlug: "a", locator: "raw/web/a.html#b1", preview: "", accepted: true },
          { pageSlug: "a", locator: "raw/web/a.html#b2", preview: "", accepted: false },
        ],
        cards: [
          { pageSlug: "a", title: "t", url: "u", snippet: "", site: "", picked: true },
        ],
      }),
    );
    expect(told.map((item) => item.key)).toEqual([
      "deepSearch.summary.pages",
      "deepSearch.summary.blocks",
      "deepSearch.summary.cards",
    ]);
    expect(told[1]?.params).toEqual({ taken: 1, total: 2 });
  });

  it("leaves out the kinds the round has none of", () => {
    expect(roundSummary(session({ whitelist: [page(true)] })).map((item) => item.key)).toEqual([
      "deepSearch.summary.pages",
    ]);
  });

  it("says what was left out as well", () => {
    const told = roundSummary(session({ whitelist: [page(true)], whitelistOmitted: 2 }));
    expect(told.map((item) => item.key)).toEqual([
      "deepSearch.summary.pages",
      "deepSearch.omitted.pages",
    ]);
  });

  it("says nothing about a round that is not there", () => {
    expect(roundSummary(null)).toEqual([]);
    expect(roundSummary(session({ roundId: "" }))).toEqual([]);
  });
});

describe("groupBlocksByFile", () => {
  const block = (locator: string) => ({
    pageSlug: "",
    locator,
    preview: locator,
    accepted: false,
  });

  it("keeps passages from one file together, in the order proposed", () => {
    const groups = groupBlocksByFile([
      block("raw/web/a.html#b1"),
      block("raw/pdf/b.pdf#b2"),
      block("raw/web/a.html#b3"),
    ]);
    expect(groups.map((group) => group.path)).toEqual([
      "raw/web/a.html",
      "raw/pdf/b.pdf",
    ]);
    expect(groups[0]?.items.map((item) => item.locator)).toEqual([
      "raw/web/a.html#b1",
      "raw/web/a.html#b3",
    ]);
  });

  it("puts a passage with no file of its own in its own group", () => {
    // There is nothing to name it by, so it cannot be merged with a real file.
    const groups = groupBlocksByFile([block("#b1"), block("raw/web/a.html#b2")]);
    expect(groups.map((group) => group.path)).toEqual(["", "raw/web/a.html"]);
  });

  it("has no groups for no passages", () => {
    expect(groupBlocksByFile([])).toEqual([]);
  });
});

describe("blockPreview", () => {
  it("collapses whitespace, so a passage reads as one line", () => {
    expect(blockPreview({ pageSlug: "", locator: "x#b1", preview: " a\n\n  b ", accepted: false })).toBe(
      "a b",
    );
  });

  it("cuts a long passage and says it did", () => {
    const preview = blockPreview(
      { pageSlug: "", locator: "x#b1", preview: "y".repeat(100), accepted: false },
      10,
    );
    expect(preview).toBe(`${"y".repeat(10)}…`);
    expect(preview).toHaveLength(11);
  });

  it("says nothing when the passage has no text of its own", () => {
    // A figure or an empty cell: the caller says what to show instead.
    expect(blockPreview({ pageSlug: "", locator: "x#b1", preview: "  \n ", accepted: false })).toBe(
      "",
    );
  });
});

describe("currentStepKey", () => {
  it("names the step the round is on", () => {
    expect(currentStepKey(session({ step: 2 }))).toBe("deepSearch.step.pages");
    expect(currentStepKey(session({ mode: "chew", step: 4 }))).toBe("deepSearch.step.plan");
  });

  it("names no step once the pages are written", () => {
    // Nothing is current any more, so there is no step to point at.
    expect(currentStepKey(session({ step: 4, ingestedIds: ["c1"] }))).toBeNull();
  });

  it("names no step when there is no round", () => {
    expect(currentStepKey(null)).toBeNull();
    expect(currentStepKey(session({ roundId: "" }))).toBeNull();
  });
});

describe("hasWritten", () => {
  it("knows when a round has written its pages", () => {
    expect(hasWritten(session())).toBe(false);
    expect(hasWritten(session({ ingestedIds: ["c1"] }))).toBe(true);
    expect(hasWritten(null)).toBe(false);
  });
});
