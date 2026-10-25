import type { ObservationPayload } from "@/lib/api/client";

/**
 * What the assistant looked at, as something the reader can act on.
 *
 * An observation is the core's record of one tool result: what was touched, where it
 * is, and what happened to it. This turns that into a row worth showing — a name, a
 * place, and the ways in — so the answer's mentions of a page are openable rather
 * than only readable.
 *
 * What a card may do is deliberately narrow. It opens something that exists, or hands
 * a search result to where results are ticked; it never fetches, and it never makes
 * anything into a source on its own. The reader does that, by the same road as always.
 */

/** Where a card's action leads. */
export type CardAction = "doc" | "browse" | "source";

export type LookedCard = {
  /** Stable within a turn: the same page looked at twice is one card. */
  key: string;
  /** What it is, which decides the icon and, in part, what can be done with it. */
  kind: "page" | "web" | "check" | "skill" | "write" | "other";
  /** The name to show. */
  label: string;
  /** Where it is, when that is worth a second line. */
  where: string | null;
  /** The address, for the actions that need one. */
  url: string | null;
  /** The page, for the action that needs one. */
  path: string | null;
  /** The ways in, in the order they are offered. */
  actions: CardAction[];
  /** Something the reader should know about it, as an i18n key. */
  note: string | null;
};

const WIKI_LINK = /^\[\[(.+?)\]\]$/;
const WEB_SEARCH = "search";
const WIKI_READ = "wiki_read";
const WRITE = "wiki_write";
const RUN = "run";
const SKILL = "skill";

function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

/** The page a link-shaped target names, or the path it gives. */
function pageOf(observation: ObservationPayload): string | null {
  const path = observation.path?.trim();
  if (path) return path;
  const target = observation.target?.trim() ?? "";
  const link = WIKI_LINK.exec(target);
  if (link) return link[1]?.trim() ?? null;
  return null;
}

function addressOf(observation: ObservationPayload): string | null {
  const url = observation.url?.trim();
  if (url) return url;
  const target = observation.target?.trim() ?? "";
  return /^https?:\/\//i.test(target) ? target : null;
}

/**
 * One tool result, as a card — or null when nothing about it belongs in a row.
 *
 * Writing is the exception: it is not a place, its own name for what it did
 * ("propose_topic_map") is not the reader's word for it, and whether it may write at
 * all is settled somewhere else — so the gate speaks for it and there is no row here.
 */
export function cardOf(observation: ObservationPayload): LookedCard | null {
  const tool = observation.tool;
  if (tool === WRITE) return null;
  const label = (observation.title || observation.target || observation.url || "").trim();

  if (observation.missing) {
    // A page the assistant tried to open and did not find. Saying so is worth it —
    // it is why the answer may be thinner than it looks — and there is nothing to
    // open, which is the point.
    const path = pageOf(observation) ?? label;
    return {
      key: `missing:${path}`,
      kind: "page",
      label: label || path,
      where: null,
      url: null,
      path: null,
      actions: [],
      note: "chat.looked.missing",
    };
  }

  if (tool === WIKI_READ) {
    const path = pageOf(observation);
    if (!path) return null;
    return {
      key: `page:${path}`,
      kind: "page",
      label: label || path,
      where: null,
      url: null,
      path,
      actions: ["doc"],
      note: null,
    };
  }

  if (tool === WEB_SEARCH) {
    const url = addressOf(observation);
    if (observation.scope === "wiki") {
      // A hit inside the notes: it is a page, and it opens like one.
      const path = pageOf(observation);
      if (!path) return null;
      return {
        key: `page:${path}`,
        kind: "page",
        label: label || path,
        where: null,
        url: null,
        path,
        actions: ["doc"],
        note: null,
      };
    }
    if (!url) return null;
    return {
      key: `web:${url}`,
      kind: "web",
      label: label || url,
      where: hostOf(url),
      url,
      path: null,
      // Opening it and handing it to the workbench to tick: the same two roads as
      // anywhere else, and a fetch is still the reader's to ask for.
      actions: ["browse", "source"],
      note: "chat.looked.notSource",
    };
  }

  if (tool === RUN) {
    if (observation.job === "fetch") {
      const url = addressOf(observation);
      if (!url) return null;
      return {
        key: `fetch:${url}`,
        kind: "web",
        label: label || url,
        where: hostOf(url),
        url,
        path: null,
        actions: ["browse"],
        note: "chat.looked.notSource",
      };
    }
    if (observation.job === "doctor") {
      return {
        key: "check:doctor",
        kind: "check",
        label: label || "doctor",
        where: null,
        url: null,
        path: null,
        actions: [],
        note: null,
      };
    }
  }

  if (tool === SKILL) {
    return {
      key: `skill:${label}`,
      kind: "skill",
      label,
      where: null,
      url: null,
      path: null,
      actions: [],
      note: null,
    };
  }

  // A write is not a place, and its own name for what it did ("propose_topic_map")
  // is not the reader's word for it. The gate says what happened.

  // A tool this version has never heard of still reports itself: showing the name it
  // gave is better than dropping it, and there is nothing to open.
  if (!label) return null;
  return {
    key: `other:${tool}:${label}`,
    kind: "other",
    label,
    where: null,
    url: null,
    path: null,
    actions: [],
    note: observation.error ? "chat.looked.failed" : null,
  };
}

/**
 * The cards for a turn, in the order things were looked at.
 *
 * The same thing looked at twice is one card: a search finds a page and the next
 * round reads it, which is one place the reader may want, not two. The first sighting
 * keeps its place, and a later sighting with more to say replaces it.
 */
export function lookedCards(observations: ObservationPayload[]): LookedCard[] {
  const cards: LookedCard[] = [];
  const at = new Map<string, number>();
  for (const observation of observations) {
    const card = cardOf(observation);
    if (!card) continue;
    const seen = at.get(card.key);
    if (seen === undefined) {
      at.set(card.key, cards.length);
      cards.push(card);
      continue;
    }
    // A later sighting knows more — a page that was read after being found has a
    // path the search result may not have had.
    const previous = cards[seen] as LookedCard;
    cards[seen] = {
      ...card,
      label: card.label || previous.label,
      path: card.path ?? previous.path,
      url: card.url ?? previous.url,
      where: card.where ?? previous.where,
      actions: card.actions.length > 0 ? card.actions : previous.actions,
      note: card.note ?? previous.note,
    };
  }
  return cards;
}
