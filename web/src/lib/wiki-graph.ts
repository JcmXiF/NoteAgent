import type { WikiGraphEdge, WikiGraphNode } from "@/lib/api/types";

/**
 * Reading the wiki's link graph.
 *
 * The backend returns the pages it knows and the links between them, including
 * pages that are linked to but not written yet. Everything here is about asking
 * that shape questions — who is next to whom, how connected each page is, which
 * kinds of page there are — so the view can stay about drawing.
 *
 * Pure: no fetching, no React.
 */

/** A stable name for a link, since an edge has no id of its own. */
export function edgeKey(edge: { source: string; target: string }): string {
  return `${edge.source}\u0000${edge.target}`;
}

/**
 * Who each page links to, and who links to it.
 *
 * Links are read as undirected: a graph a reader explores is about how pages
 * relate, not about which one happened to name the other. Self-links are left out
 * — a page naming itself is not a relationship, and counting it would make that
 * page look better connected than it is.
 */
export function buildAdjacency(
  nodes: WikiGraphNode[],
  edges: WikiGraphEdge[],
): Map<string, Set<string>> {
  const adjacency = new Map<string, Set<string>>();
  for (const node of nodes) adjacency.set(node.id, new Set());
  const known = new Set(adjacency.keys());
  for (const edge of edges) {
    if (edge.source === edge.target) continue;
    // Both ends have to be pages in the graph: a link to something the graph does
    // not list has nothing to be drawn to, and inventing a neighbour for it would
    // give the view an id it cannot look up.
    if (!known.has(edge.source) || !known.has(edge.target)) continue;
    adjacency.get(edge.source)?.add(edge.target);
    adjacency.get(edge.target)?.add(edge.source);
  }
  return adjacency;
}

/** The pages on the other end of a link, or nothing for a page with none. */
export function neighborsOf(
  adjacency: Map<string, Set<string>>,
  id: string | null | undefined,
): string[] {
  if (!id) return [];
  return [...(adjacency.get(id) ?? [])];
}

/** How many distinct pages each one is linked to. */
export function degreesFrom(adjacency: Map<string, Set<string>>): Map<string, number> {
  const degrees = new Map<string, number>();
  for (const [id, neighbours] of adjacency) degrees.set(id, neighbours.size);
  return degrees;
}

/** Whether a page is only linked to, and has not been written yet. */
export function isGhost(node: WikiGraphNode): boolean {
  return node.ghost || node.path === null;
}

/**
 * The kinds of page present, most common first.
 *
 * Ghosts are not a kind of page: they are pages that do not exist, so they are
 * reported separately rather than mixed in with the kinds a reader can filter by.
 */
export function kindsPresent(nodes: WikiGraphNode[]): string[] {
  const counts = new Map<string, number>();
  for (const node of nodes) {
    if (isGhost(node)) continue;
    counts.set(node.type, (counts.get(node.type) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([type]) => type);
}

/** The pages written, in a stable order. */
export function realNodes(nodes: WikiGraphNode[]): WikiGraphNode[] {
  return nodes.filter((node) => !isGhost(node));
}

/** The pages linked to but not written, in a stable order. */
export function ghostNodes(nodes: WikiGraphNode[]): WikiGraphNode[] {
  return nodes.filter(isGhost);
}

/**
 * The pages reachable from one by following links, nearest first.
 *
 * Used to show what a page is connected to, so the walk stops at the distance it
 * was asked for; a page can otherwise reach most of the graph.
 */
export function pagesWithin(
  adjacency: Map<string, Set<string>>,
  id: string | null | undefined,
  depth = 1,
): string[] {
  if (!id || depth < 1) return [];
  const seen = new Set<string>([id]);
  let frontier = [id];
  const out: string[] = [];
  for (let step = 0; step < depth; step += 1) {
    const next: string[] = [];
    for (const current of frontier) {
      for (const neighbour of adjacency.get(current) ?? []) {
        if (seen.has(neighbour)) continue;
        seen.add(neighbour);
        next.push(neighbour);
      }
    }
    out.push(...next);
    frontier = next;
  }
  return out;
}
