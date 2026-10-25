import type {
  DeepSearchCard,
  DeepSearchSession,
  WikiGraphEdge,
  WikiGraphNode,
} from "@/lib/api/types";
import { edgeKey } from "@/lib/wiki-graph";

/**
 * What a deep search round wants to do to the graph, drawn on top of it.
 *
 * A round proposes pages to add and pages to revise, links between them, and
 * material it found for those pages. None of it is written yet, so it is an
 * overlay: shown on the graph, and kept out of the graph's own links. A page the
 * round wants to add that does not exist yet is shown the same way the graph shows
 * any page that has not been written.
 *
 * Pure: no fetching, no React.
 */

type OverlayPage = {
  slug: string;
  title: string;
  action: "add" | "revise";
  accepted: boolean;
  plan: string;
};

export type GraphOverlay = {
  /** The pages this round wants to touch, by the node they belong to. */
  pages: Map<string, OverlayPage>;
  /** Links the round proposes, keyed the way edges are keyed. */
  edges: Set<string>;
  /** The material found for each proposed page, by that page's node. */
  cards: Map<string, DeepSearchCard[]>;
  /** Material the round did not tie to any of its pages, so it is not lost. */
  looseCards: DeepSearchCard[];
};

/**
 * Whether a page name a model wrote could refer to a node.
 *
 * Page names are proposed by the core, which uses the graph's own ids, but a
 * card's page name comes from a model, which writes it the way a person would:
 * with or without the folders in front. Comparing the last part is what makes
 * `wiki/concepts/entropy` and `entropy` the same page.
 */
export function matchesNodeId(pageName: string, nodeId: string): boolean {
  const left = pageName.trim();
  const right = nodeId.trim();
  if (!left || !right) return false;
  if (left === right) return true;
  const tail = (value: string) => value.slice(value.lastIndexOf("/") + 1);
  return tail(left) === tail(right);
}

/**
 * Which of a round's pages a card was found for, or nothing when that cannot be
 * told apart.
 *
 * Exact first, because that is the model saying precisely which page it means. The
 * looser comparison is for the case where it wrote the name without its folders —
 * and it only counts when one page answers to that name: a round can propose both
 * `concepts/alpha` and `entities/alpha`, and a card that says `alpha` is not
 * evidence for either of them.
 */
export function cardOwner(pageName: string, pageSlugs: string[]): string {
  const name = pageName.trim();
  if (!name) return "";
  const exact = pageSlugs.find((slug) => slug === name);
  if (exact) return exact;
  const loose = pageSlugs.filter((slug) => matchesNodeId(name, slug));
  return loose.length === 1 ? (loose[0] ?? "") : "";
}

export function buildOverlay(session: DeepSearchSession | null): GraphOverlay {
  const pages = new Map<string, OverlayPage>();
  const edges = new Set<string>();
  const cards = new Map<string, DeepSearchCard[]>();
  const looseCards: DeepSearchCard[] = [];
  if (!session?.roundId) return { pages, edges, cards, looseCards };
  for (const page of session.whitelist) {
    if (!page.slug) continue;
    pages.set(page.slug, {
      slug: page.slug,
      title: page.title,
      action: page.action === "revise" ? "revise" : "add",
      accepted: page.accepted ?? true,
      plan: page.plan ?? "",
    });
  }
  for (const edge of session.overlayEdges) {
    if (!edge.source || !edge.target) continue;
    edges.add(edgeKey(edge));
  }
  for (const card of session.cards) {
    const slug = cardOwner(card.pageSlug, [...pages.keys()]);
    if (!slug) {
      if (card.pageSlug) looseCards.push(card);
      continue;
    }
    const list = cards.get(slug);
    if (list) list.push(card);
    else cards.set(slug, [card]);
  }
  return { pages, edges, cards, looseCards };
}

export function overlayIsEmpty(overlay: GraphOverlay): boolean {
  return overlay.pages.size === 0 && overlay.edges.size === 0;
}

/**
 * How much material a page has been given, and how much of it the reader took.
 *
 * Nothing when the page has none: a badge reading `0/0` would be a mark on the
 * graph that says nothing.
 */
export function overlayBadge(
  nodeId: string,
  overlay: GraphOverlay,
): { picked: number; total: number } | null {
  const list = overlay.cards.get(nodeId);
  if (!list || list.length === 0) return null;
  return { picked: list.filter((card) => card.picked).length, total: list.length };
}

/** How many cards the reader has taken, across the whole round. */
export function pickedCardCount(overlay: GraphOverlay): number {
  let picked = 0;
  for (const list of overlay.cards.values()) {
    picked += list.filter((card) => card.picked).length;
  }
  return picked + overlay.looseCards.filter((card) => card.picked).length;
}

/**
 * The material to show: only what belongs to the page being looked at, when there
 * is one. A reader who has picked out a page on the graph is asking about that
 * page, and a round can carry more material than fits on a screen.
 */
export function cardsToShow(
  overlay: GraphOverlay,
  selectedId: string | null,
): { slug: string; cards: DeepSearchCard[] }[] {
  const groups: { slug: string; cards: DeepSearchCard[] }[] = [];
  if (selectedId && overlay.pages.has(selectedId)) {
    const list = overlay.cards.get(selectedId);
    return list ? [{ slug: selectedId, cards: list }] : [];
  }
  for (const [slug, list] of overlay.cards) groups.push({ slug, cards: list });
  if (overlay.looseCards.length > 0) groups.push({ slug: "", cards: overlay.looseCards });
  return groups;
}

/**
 * The graph with the round's proposed links added.
 *
 * A link the graph already has is left alone: the round is adding to what is
 * there, not redrawing it, and a duplicate would be drawn twice.
 */
export function overlayEdges(
  edges: WikiGraphEdge[],
  overlay: GraphOverlay,
): WikiGraphEdge[] {
  if (overlay.edges.size === 0) return edges;
  const known = new Set(edges.map((edge) => edgeKey(edge)));
  const extra: WikiGraphEdge[] = [];
  for (const edge of overlay.edges) {
    if (known.has(edge)) continue;
    const [source, target] = edge.split("\u0000");
    if (!source || !target) continue;
    extra.push({ source, target, count: 1 });
  }
  return extra.length === 0 ? edges : [...edges, ...extra];
}

/**
 * The graph with the round's proposed pages added.
 *
 * Only pages the graph does not already have: a page that exists is the node that
 * is already there, and two nodes for one page would be two places to click.
 */
export function overlayNodes(
  nodes: WikiGraphNode[],
  overlay: GraphOverlay,
): WikiGraphNode[] {
  if (overlay.pages.size === 0) return nodes;
  const known = new Set(nodes.map((node) => node.id));
  const extra: WikiGraphNode[] = [];
  for (const page of overlay.pages.values()) {
    if (known.has(page.slug)) continue;
    extra.push({
      id: page.slug,
      // Nothing to open yet: this page has not been written. It gets a path when
      // the compile writes it, which is also when the graph will have it.
      path: null,
      title: page.title,
      type: "ghost",
      ghost: true,
      degree: 1,
    });
  }
  return extra.length === 0 ? nodes : [...nodes, ...extra];
}

/** How a node is drawn while a round is proposing it, or nothing if it is not. */
export function overlayMarkOf(
  nodeId: string,
  overlay: GraphOverlay,
): { action: "add" | "revise"; accepted: boolean } | null {
  const page = overlay.pages.get(nodeId);
  return page ? { action: page.action, accepted: page.accepted } : null;
}

/** The pages of a round, in the order the core proposed them. */
export function overlayPageList(overlay: GraphOverlay): OverlayPage[] {
  return [...overlay.pages.values()];
}

/**
 * The labels the overlay needs in the graph's legend, and only those that occur:
 * a key entry for something not on the graph is noise.
 */
export function overlayLegend(overlay: GraphOverlay): string[] {
  if (overlay.pages.size === 0) return [];
  const out: string[] = [];
  const actions = new Set([...overlay.pages.values()].map((page) => page.action));
  if (actions.has("add")) out.push("add");
  if (actions.has("revise")) out.push("revise");
  if (overlay.edges.size > 0) out.push("link");
  return out;
}
