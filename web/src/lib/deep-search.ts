import type { DeepSearchActBody } from "@/lib/api/deep-search";
import type { DeepSearchBlock, DeepSearchSession } from "@/lib/api/types";
import type { Message } from "@/lib/message";
import { message } from "@/lib/message";
import { parseSourceLocator } from "@/lib/wiki-locators";

export type { DeepSearchActBody };

/**
 * A deep search round, as the reader sees it.
 *
 * A round is a small state machine: the core holds which step it is on, and this
 * turns that into the steps a reader is shown, which step they are on, which
 * action moves it on, and what can be taken back. Keeping the arithmetic here
 * means the panel that draws it stays about drawing.
 *
 * Pure: no fetching, no React, no i18n — labels are keys, and the caller
 * translates them.
 */

/** The steps of a round, in order. Explore also proposes pages to write. */
const STEPS_EXPLORE = [
  "deepSearch.step.range",
  "deepSearch.step.pages",
  "deepSearch.step.material",
  "deepSearch.step.plan",
  "deepSearch.step.wrapUp",
];

/** Chew starts from what was left over, so it has no page-proposing step. */
const STEPS_CHEW = [
  "deepSearch.step.range",
  "deepSearch.step.material",
  "deepSearch.step.plan",
  "deepSearch.step.wrapUp",
];

export function stepLabels(mode: string): string[] {
  return mode === "chew" ? STEPS_CHEW : STEPS_EXPLORE;
}

/**
 * Which of the shown steps a round is on.
 *
 * The core counts the steps it takes — a Chew round starts at its third — while a
 * reader counts the steps they are shown, so the two are mapped rather than kept
 * the same. Only the core's numbering is stored, since it is the one the actions
 * are guarded by.
 */
export function uiStepOf(mode: string, step: number): number {
  if (mode !== "chew") return step;
  if (step <= 1) return 1;
  if (step === 3) return 2;
  if (step === 4) return 3;
  return 4;
}

type RoundStep = {
  /** Matches the shown position, which is also what an action's step id uses. */
  id: string;
  labelKey: string;
  state: "pending" | "current" | "done";
};

/**
 * The steps to show, with the one being worked on marked.
 *
 * Nothing is being worked on once the round has been written: every step is behind
 * the reader, and leaving one marked as current would say the round is still going.
 */
export function roundSteps(mode: string, step: number, written = false): RoundStep[] {
  const labels = stepLabels(mode);
  const current = uiStepOf(mode, step);
  return labels.map((labelKey, index) => {
    const n = index + 1;
    return {
      id: String(n),
      labelKey,
      state: written || current > n ? "done" : current === n ? "current" : "pending",
    };
  });
}

/**
 * What clicking an earlier step would ask for, or nothing when it cannot be taken
 * back.
 *
 * Going back to the first step starts the round over; any later one rewinds to
 * the step it names. Once pages have been written nothing is taken back, because
 * the pages are on disk and a round that has ended is not a thing that can be
 * un-ended.
 */
export function rewindAction(
  mode: string,
  target: number,
  current: number,
  labelCount: number,
  written: boolean,
): DeepSearchActBody | null {
  if (written) return null;
  if (target < 1 || target >= current || target >= labelCount) return null;
  if (target === 1) return { action: "restart" };
  if (mode === "chew") {
    // Chew's shown steps 2 and 3 are the core's 3 and 4.
    if (target === 2) return { action: "redo", step: 3 };
    if (target === 3) return { action: "redo", step: 4 };
    return null;
  }
  return { action: "redo", step: target };
}

/**
 * Which action moves the round on, or what the reader is expected to do instead.
 *
 * The point of a round is the pages it produces, and writing them is not part of
 * the round: it goes through the compile path, which is why a round that is ready
 * to write says so rather than naming an action.
 *
 * `roundCollections` is how many selections the round has gathered so far. Planning
 * needs material, and the round's own state does not say whether it has any — the
 * selections are made where material is gathered, and only the caller can count
 * them.
 */
export type PendingAction =
  | { kind: "none"; reason: "idle" }
  | { kind: "start" }
  | { kind: "action"; action: string; step?: number }
  | { kind: "gather" }
  | { kind: "write" }
  | { kind: "end" };

export function pendingActionOf(
  session: DeepSearchSession | null,
  roundCollections = 0,
): PendingAction {
  if (!session || !session.roundId) return { kind: "start" };
  const mode = session.mode;
  if (session.step === 2) {
    return session.whitelist.length > 0
      ? { kind: "action", action: "acceptWhitelist" }
      : { kind: "action", action: "proposeWhitelist" };
  }
  if (session.step === 3) {
    if (mode !== "chew") {
      // An explore round searched for material on the way here; what is left is for
      // the reader to pick some and bring back selections, which is not a button of
      // the round's own.
      return roundCollections > 0 ? { kind: "action", action: "enterPlan" } : { kind: "gather" };
    }
    return session.proposedBlocks.length > 0
      ? { kind: "action", action: "acceptBlocks" }
      : { kind: "action", action: "proposeBlocks" };
  }
  if (session.step === 4) {
    // The plan is made; what is left is writing the pages it lists, then saying so.
    return session.ingestedIds.length > 0 ? { kind: "end" } : { kind: "write" };
  }
  if (session.step >= 5) return { kind: "end" };
  return { kind: "none", reason: "idle" };
}

type RoundCounts = {
  pages: number;
  pagesAccepted: number;
  blocks: number;
  blocksAccepted: number;
  cards: number;
  cardsPicked: number;
};

export function roundCounts(session: DeepSearchSession | null): RoundCounts {
  const whitelist = session?.whitelist ?? [];
  const blocks = session?.proposedBlocks ?? [];
  const cards = session?.cards ?? [];
  return {
    pages: whitelist.length,
    pagesAccepted: whitelist.filter((page) => page.accepted).length,
    blocks: blocks.length,
    blocksAccepted: blocks.filter((block) => block.accepted).length,
    cards: cards.length,
    cardsPicked: cards.filter((card) => card.picked).length,
  };
}

/**
 * The round a new selection belongs to, or nothing when it belongs to no round.
 *
 * While a round is gathering material, what the reader picks is the round's: the
 * core plans it as part of that round and leaves it out of the everyday pass. Before
 * the round needs material and after its pages are written, a selection is an
 * everyday one — the round is not asking for anything any more.
 */
export function roundIdForSelection(session: DeepSearchSession | null): string {
  const id = (session?.roundId ?? "").trim();
  if (!id) return "";
  const step = session?.step ?? 0;
  return step >= 3 && step <= 4 ? id : "";
}

/**
 * What the reader should be told about material that was left out.
 *
 * A truncated working set must be visible, never silently passed over: a reader
 * who is shown eight passages out of thirty and told nothing will believe those
 * eight are all there is.
 *
 * The core's own wording for this is not shown — it is written for whoever reads
 * the log — so the sentences here are the frontend's, and the log keeps the rest.
 */
export function truncationMessages(session: DeepSearchSession | null): Message[] {
  if (!session) return [];
  const out: Message[] = [];
  if (session.whitelistOmitted > 0) {
    out.push(message("deepSearch.omitted.pages", { count: session.whitelistOmitted }));
  }
  if (session.cardsOmitted > 0) {
    out.push(message("deepSearch.omitted.cards", { count: session.cardsOmitted }));
  }
  if (session.truncated) out.push(message("deepSearch.omitted.truncated"));
  return out;
}

/**
 * The line that keeps a reader oriented: how much the round holds, and how much of
 * it has been taken.
 *
 * Counts rather than lists, because the lists are the step's own content. Shown at
 * every step so the reader can tell whether accepting anything actually happened.
 */
export function roundSummary(session: DeepSearchSession | null): Message[] {
  if (!session?.roundId) return [];
  const counts = roundCounts(session);
  const out: Message[] = [];
  if (counts.pages > 0) {
    out.push(
      message("deepSearch.summary.pages", { taken: counts.pagesAccepted, total: counts.pages }),
    );
  }
  if (counts.blocks > 0) {
    out.push(
      message("deepSearch.summary.blocks", { taken: counts.blocksAccepted, total: counts.blocks }),
    );
  }
  if (counts.cards > 0) {
    out.push(
      message("deepSearch.summary.cards", { taken: counts.cardsPicked, total: counts.cards }),
    );
  }
  return [...out, ...truncationMessages(session)];
}

/** One file's proposed passages, in the order the round proposed them. */
type BlockGroup = { path: string; items: DeepSearchBlock[] };

/**
 * Groups passages by the file they came from.
 *
 * A reader judges a passage against the ones around it, so passages from one file
 * belong together; a flat list would put two passages from one page either side of
 * one from another.
 */
export function groupBlocksByFile(blocks: DeepSearchBlock[]): BlockGroup[] {
  const order: string[] = [];
  const buckets = new Map<string, DeepSearchBlock[]>();
  for (const block of blocks) {
    const path = parseSourceLocator(block.locator)?.path ?? "";
    const list = buckets.get(path);
    if (list) {
      list.push(block);
    } else {
      order.push(path);
      buckets.set(path, [block]);
    }
  }
  return order.map((path) => ({ path, items: buckets.get(path) ?? [] }));
}

/**
 * As much of a passage as fits on one line, so it can be recognised at a glance.
 *
 * Empty when the passage has no text of its own — a figure or a table cell. The
 * caller says what to show instead, because a blank row would look like a defect,
 * and naming it by its locator would not be something a reader could use.
 */
export function blockPreview(block: DeepSearchBlock, max = 56): string {
  const text = block.preview.replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

/**
 * The label of the step a round is on, or nothing when it is not in one.
 *
 * Used to say where a round now stands once an action has finished, which is the
 * part a finished job cannot say on its own: the job is over, the round is not.
 */
export function currentStepKey(session: DeepSearchSession | null): string | null {
  if (!session?.roundId) return null;
  const steps = roundSteps(session.mode, session.step, hasWritten(session));
  return steps.find((item) => item.state === "current")?.labelKey ?? null;
}

/** Whether the round has written its pages, which is what stops rewinding. */
export function hasWritten(session: DeepSearchSession | null): boolean {
  return (session?.ingestedIds.length ?? 0) > 0;
}

/**
 * What to say while an action runs, and which step of the round it belongs to.
 *
 * The backend reports which step it is on, and its own wording is written for the
 * log rather than the screen, so the sentence a reader sees is chosen here from
 * the action that was asked for. Which action is running is something this side
 * knows exactly, where the step alone would leave two actions sharing a wording.
 *
 * The phase names are the steps themselves, so where an action belongs on the
 * shown track follows from the mode: chew does not have the page step, which
 * shifts everything after it.
 */
const ACTION_PROGRESS: Record<string, { key: string; phase: string }> = {
  start: { key: "deepSearch.progress.start", phase: "range" },
  proposeWhitelist: { key: "deepSearch.progress.proposePages", phase: "pages" },
  acceptWhitelist: { key: "deepSearch.progress.acceptPages", phase: "pages" },
  proposeBlocks: { key: "deepSearch.progress.proposeBlocks", phase: "material" },
  acceptBlocks: { key: "deepSearch.progress.acceptBlocks", phase: "material" },
  enterPlan: { key: "deepSearch.progress.plan", phase: "plan" },
  markIngested: { key: "deepSearch.progress.ingest", phase: "plan" },
  end: { key: "deepSearch.progress.end", phase: "wrapUp" },
  // Rewinding lands the round back at its first step, whatever it was doing.
  restart: { key: "deepSearch.progress.restart", phase: "range" },
  redo: { key: "deepSearch.progress.redo", phase: "range" },
};

export function progressForAction(action: string): { key: string; phase: string } | null {
  return ACTION_PROGRESS[action] ?? null;
}
