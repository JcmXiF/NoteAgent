import { message, type Message } from "@/lib/message";
import type {
  ClipsPayload,
  DerivedBlock,
  TopicAction,
  TopicEdge,
  TopicMap,
  TopicNode,
} from "@/lib/api/types";

/**
 * Logic for the passages a user has selected, before anything is compiled.
 *
 * A collection is a group of blocks taken from one source. The selection is what
 * the user chose, so nothing here invents, drops or reorders material on its
 * own — blocks keep document order, and the source of each block is preserved.
 */

export type BlockCollection = {
  id: string;
  project: string;
  sourceKey: string;
  sourceLabel: string;
  blockIds: string[];
  blocks: DerivedBlock[];
  /** Set while the selection belongs to a deep search round. */
  roundId?: string;
  /** Page the selection is meant to extend, when the user picked one. */
  targetSlug?: string;
};

export type TopicDecision = {
  slug: string;
  action: TopicAction;
  sectionIds: string[];
};

/* --------------------------------------------------------------- locators */

/**
 * A locator used before the original file has been stored.
 *
 * Until a capture is written to `raw/`, its blocks have no stable file
 * path, so the URL carries the identity. Compilation replaces these with real
 * paths, which is why the prefix has to be recognisable everywhere.
 */
export function previewSourceKey(url: string): string {
  return `preview:${url}`;
}

export function isPreviewSource(key: string): boolean {
  return key.startsWith("preview:");
}

export function previewUrlFromKey(sourceKey: string): string | null {
  return isPreviewSource(sourceKey) ? sourceKey.slice("preview:".length) : null;
}

/** How many selections are still previews, i.e. not yet stored on disk. */
export function countPreviewCollections(
  collections: { sourceKey: string }[],
): number {
  return collections.filter((item) => isPreviewSource(item.sourceKey)).length;
}

/**
 * Which of a source's passages are already in a selection, and which one to put
 * in front of the reader.
 *
 * A selection made before its page was stored is keyed by the page's URL, and
 * afterwards by the stored path, so every name the source answers to is tried —
 * including the preview form of each. Passing the names in rather than deriving
 * them here keeps this usable for a source in the workbench and for a stored
 * file, which know different things about themselves.
 */
export function sourceMarkup(
  collections: BlockCollection[],
  names: Iterable<string>,
  focusedId?: string | null,
): { selectedIds: string[]; focusId: string | null } {
  const keys = [...names].filter(Boolean);
  const mine = collections.filter((collection) =>
    keys.some(
      (key) =>
        collection.sourceKey === key || collection.sourceKey === previewSourceKey(key),
    ),
  );
  const focused = mine.find((collection) => collection.id === focusedId);
  return {
    selectedIds: uniqueBlockIds(mine.flatMap((collection) => collection.blockIds)),
    focusId: focused?.blockIds[0] ?? null,
  };
}

/* ------------------------------------------------------------ text output */

/**
 * Blocks by id, looked up across several block lists. Later lists win, because
 * callers pass the freshest extraction last.
 */
export function blocksById(
  ids: string[],
  sources: DerivedBlock[][],
): DerivedBlock[] {
  const byId = new Map<string, DerivedBlock>();
  for (const list of sources) {
    for (const block of list) {
      byId.set(block.id, block);
    }
  }
  return ids
    .map((id) => byId.get(id))
    .filter((block): block is DerivedBlock => Boolean(block));
}

/**
 * The text handed to planning and compilation: one `[blockId] text` line per
 * selected block, in the order the user selected them.
 */
export function formatCollectionText(collection: BlockCollection): string {
  const byId = new Map(collection.blocks.map((block) => [block.id, block]));
  return collection.blockIds
    .map((id) => byId.get(id))
    .filter((block): block is DerivedBlock => Boolean(block?.text.trim()))
    .map((block) => `[${block.id}] ${block.text}`)
    .join("\n\n");
}

/** Compact "which blocks are these" label, e.g. `p3–p7 · 5 blocks`. */
export function collectionRangeLabel(collection: BlockCollection): Message {
  const ids = collection.blockIds;
  if (ids.length === 0) return message("clips.range.empty");
  if (ids.length === 1) return message("clips.range.single", { id: ids[0] });
  return message("clips.range.span", {
    from: ids[0],
    to: ids[ids.length - 1],
    count: ids.length,
  });
}

const HEADING_TAGS = new Set(["h1", "h2", "h3", "h4", "h5", "h6"]);
/**
 * Wikis render their "edit this section" affordance as a heading in some
 * layouts. Compared case-insensitively, so "Edit" is filtered like "edit".
 */
const SKIP_TEXT = new Set(["编辑", "编辑 ]", "edit", "[edit]"]);

function isSkippable(text: string): boolean {
  return SKIP_TEXT.has(text.toLowerCase());
}

function shorten(text: string, maxChars: number): string {
  const compact = text.replace(/\s+/g, " ").trim();
  if (compact.length <= maxChars) return compact;
  return `${compact.slice(0, Math.max(0, maxChars - 1)).trimEnd()}…`;
}

/**
 * The best short title for a selection: its first real heading, else its first
 * paragraph. Used for chips in the plan, not as a file name.
 */
export function collectionHeadline(
  collection: BlockCollection,
  maxChars = 42,
): string {
  const byId = new Map(collection.blocks.map((block) => [block.id, block]));
  let fallback = "";

  for (const id of collection.blockIds) {
    const block = byId.get(id);
    if (!block) continue;
    const text = block.text.trim().replace(/\s+/g, " ");
    if (!text) continue;

    const tag = (block.tag || "").toLowerCase();
    if (HEADING_TAGS.has(tag) && !isSkippable(text)) return shorten(text, maxChars);
    // Figure captions are useful but never make a good title.
    if (!fallback && !isSkippable(text) && !text.startsWith("[图")) fallback = text;
  }

  if (fallback) return shorten(fallback, maxChars);
  // Nothing in the selection works as a title; the caller shows its own
  // placeholder (see `clips.untitled`) when this comes back empty.
  return shorten(collection.sourceLabel || "", maxChars);
}

/** The paragraph after the title, used as a one-line summary. */
export function collectionLead(collection: BlockCollection, maxChars = 72): string {
  const byId = new Map(collection.blocks.map((block) => [block.id, block]));
  let skipFirst = false;

  for (const id of collection.blockIds) {
    const block = byId.get(id);
    if (!block) continue;
    const text = block.text.trim().replace(/\s+/g, " ");
    if (!text || isSkippable(text) || text.startsWith("[图")) continue;

    // The first readable block is the headline, so the lead starts after it.
    if (!skipFirst) {
      skipFirst = true;
      continue;
    }
    if (HEADING_TAGS.has((block.tag || "").toLowerCase())) continue;
    return shorten(text, maxChars);
  }

  return "";
}

/** Prefers the plan's own title for a selection, then its headline, then its id. */
export function collectionTitleForId(
  id: string,
  collections: BlockCollection[],
  summaries?: { id: string; title: string }[],
): string {
  const fromPlan = summaries?.find((item) => item.id === id)?.title.trim();
  if (fromPlan) return fromPlan;
  const collection = collections.find((item) => item.id === id);
  if (collection) {
    const headline = collectionHeadline(collection);
    if (headline) return headline;
  }
  return id;
}

/* ------------------------------------------------------------- selection */

/**
 * Which selections belong to the round being run, and which do not.
 *
 * Selections made during a deep search are kept apart from the everyday ones: the
 * regular planning pass does not touch a round's selections, and a round's material
 * is not compiled as if the reader had chosen it. Three groups rather than two,
 * because a selection can name a round that is no longer running — the round ends
 * without it, and something has to be said about it rather than shown as if it
 * still belonged to work in progress.
 */
type SelectionScope = {
  /** Selections of the round being run, empty when there is none. */
  mine: BlockCollection[];
  /** Selections naming a round that is not the one being run. */
  leftover: BlockCollection[];
  /** Selections that are not part of any round. */
  everyday: BlockCollection[];
};

export function collectionsByScope(
  collections: BlockCollection[],
  roundId: string | null | undefined,
): SelectionScope {
  const live = (roundId ?? "").trim();
  const scope: SelectionScope = { mine: [], leftover: [], everyday: [] };
  for (const collection of collections) {
    const owner = (collection.roundId ?? "").trim();
    if (!owner) scope.everyday.push(collection);
    else if (live && owner === live) scope.mine.push(collection);
    else scope.leftover.push(collection);
  }
  return scope;
}

export function uniqueBlockIds(incoming: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of incoming) {
    const id = raw.trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
}

function sameIdSet(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false;
  const other = new Set(right);
  return left.every((id) => other.has(id));
}

/**
 * Merges block id groups into document order, then appends anything the
 * document order did not know about. Keeps the reading order stable no matter
 * which order the user dragged.
 */
export function orderedBlockUnion(groups: string[][], documentOrder: string[]): string[] {
  const wanted = new Set<string>();
  for (const group of groups) {
    for (const raw of group) {
      const id = raw.trim();
      if (id) wanted.add(id);
    }
  }

  const out: string[] = [];
  const seen = new Set<string>();
  for (const id of documentOrder) {
    if (!wanted.has(id) || seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  for (const group of groups) {
    for (const raw of group) {
      const id = raw.trim();
      if (!id || seen.has(id)) continue;
      seen.add(id);
      out.push(id);
    }
  }
  return out;
}

type MarqueeClipPlan =
  | { action: "empty" }
  | { action: "noop"; focusId: string }
  | { action: "create"; blockIds: string[] }
  | { action: "merge"; keepId: string; dropIds: string[]; blockIds: string[] };

/**
 * Decides what a new selection gesture means.
 *
 * Overlapping an existing selection of the same source unions into it — which
 * may fold several together — so overlapping drags accumulate instead of
 * producing near-duplicate entries. Selecting exactly what one entry already
 * holds changes nothing.
 */
export function planMarqueeClip(
  incoming: string[],
  existing: { id: string; sourceKey: string; blockIds: string[]; roundId?: string }[],
  sourceKey: string,
  documentOrder: string[],
  roundId = "",
): MarqueeClipPlan {
  const incomingIds = uniqueBlockIds(incoming);
  if (incomingIds.length === 0) return { action: "empty" };

  const hit = new Set(incomingIds);
  const overlapping = existing.filter(
    (item) =>
      item.sourceKey === sourceKey &&
      (item.roundId || "") === roundId &&
      item.blockIds.some((id) => hit.has(id)),
  );

  const blockIds = orderedBlockUnion(
    [incomingIds, ...overlapping.map((item) => item.blockIds)],
    documentOrder,
  );

  if (overlapping.length === 0) return { action: "create", blockIds };
  if (overlapping.length === 1 && sameIdSet(overlapping[0].blockIds, blockIds)) {
    return { action: "noop", focusId: overlapping[0].id };
  }

  return {
    action: "merge",
    keepId: overlapping[0].id,
    dropIds: overlapping.slice(1).map((item) => item.id),
    blockIds,
  };
}

/* ---------------------------------------------------------------- plan */

export function topicMapNodes(map: TopicMap): TopicNode[] {
  if (map.candidates && map.candidates.length > 0) return map.candidates;
  return [map.primary, ...map.alsoInSources];
}

const PLAN_RELATIONS = new Set(["prerequisite", "used_by", "contrast", "cooccurs"]);

/**
 * Fallback edges when the plan does not carry its own: connect everything to the
 * primary page. Used for older plans and for plans the model returned without
 * relations.
 */
export function starEdgesFromNodes(nodes: TopicNode[]): TopicEdge[] {
  const primary = nodes.find((item) => item.relationToPrimary === "primary") ?? nodes[0];
  if (!primary) return [];

  const out: TopicEdge[] = [];
  for (const node of nodes) {
    if (node.slug === primary.slug) continue;
    out.push({
      from: node.slug,
      to: primary.slug,
      relation:
        node.relationToPrimary && PLAN_RELATIONS.has(node.relationToPrimary)
          ? node.relationToPrimary
          : "cooccurs",
      evidence: node.evidence ?? "",
      collectionId: node.collectionIds?.[0] ?? "",
      blockIds: node.blockIds ?? [],
    });
  }
  return out;
}

export function topicMapEdges(map: TopicMap): TopicEdge[] {
  if (map.edges && map.edges.length > 0) return map.edges;
  return starEdgesFromNodes(topicMapNodes(map));
}

/** The action a page starts with before the user changes it. */
export function defaultActionForNode(node: TopicNode): TopicAction {
  if (
    node.coverage === "write" ||
    node.coverage === "mention" ||
    node.coverage === "skip"
  ) {
    return node.coverage;
  }
  return node.relationToPrimary === "primary" ? "write" : "skip";
}

/**
 * Initial decisions for a plan: every candidate pre-selected per its coverage,
 * with all of its sections. Pre-selection is a suggestion, not consent —
 * nothing is written until the user compiles.
 */
function defaultDecisions(map: TopicMap): Record<string, TopicDecision> {
  const out: Record<string, TopicDecision> = {};
  for (const node of topicMapNodes(map)) {
    out[node.slug] = {
      slug: node.slug,
      action: defaultActionForNode(node),
      sectionIds: (node.outline ?? []).map((section) => section.id),
    };
  }
  return out;
}

/** Restores decisions stored alongside a selection. */
export function decisionsFromPayload(
  raw: Array<{ slug: string; action: TopicAction; sectionIds: string[] }> | undefined,
): Record<string, TopicDecision> {
  const out: Record<string, TopicDecision> = {};
  for (const item of raw ?? []) {
    if (!item.slug) continue;
    out[item.slug] = {
      slug: item.slug,
      action: item.action,
      sectionIds: item.sectionIds ?? [],
    };
  }
  return out;
}

/**
 * The board as it stands: every page the plan has, with the reader's own answer
 * over the plan's suggestion, and the suggestion where they have not answered.
 *
 * An answer for a page the plan no longer has is dropped — the page is not there
 * to answer about.
 */
export function boardDecisions(
  map: TopicMap | null,
  answers: Record<string, TopicDecision>,
): Record<string, TopicDecision> {
  const board = map ? defaultDecisions(map) : {};
  for (const [slug, decision] of Object.entries(answers)) {
    if (slug in board) board[slug] = decision;
  }
  return board;
}

/**
 * Serialises the selection for storage.
 *
 * The board and the gate are two different things and go out as two fields. What
 * the reader answered is always sent, so a later plan can carry it over. The gate
 * — the answers belonging to the pass that was confirmed — is sent only once the
 * user has actually changed one: until then an empty list is sent, which is how the
 * backend distinguishes "nobody has been through the gate yet" from "these pages
 * were confirmed". The defaults shown while planning are a suggestion, not consent.
 */
export function clipsPayloadFrom(input: {
  intent: string;
  collections: BlockCollection[];
  topicMap: TopicMap | null;
  decisions: Record<string, TopicDecision>;
  decisionsTouched: boolean;
}): ClipsPayload {
  const answered = Object.values(input.decisions).map((item) => ({
    slug: item.slug,
    action: item.action,
    sectionIds: item.sectionIds,
  }));
  return {
    intent: input.intent,
    collections: input.collections.map((item) => ({
      id: item.id,
      sourceKey: item.sourceKey,
      sourceLabel: item.sourceLabel,
      blockIds: item.blockIds,
      blocks: item.blocks,
      roundId: item.roundId ?? "",
      targetSlug: item.targetSlug ?? "",
    })),
    topicMap: input.topicMap,
    choices: answered,
    decisions: input.decisionsTouched ? answered : [],
  };
}
