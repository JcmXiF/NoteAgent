import type { WikiGraphEdge, WikiGraphNode } from "@/lib/api/types";
import { isGhost } from "@/lib/wiki-graph";
import type { ForceNode } from "@/lib/wiki-force";

/**
 * Moving around and pointing at a graph.
 *
 * The arithmetic of panning, zooming and deciding what was clicked, kept apart
 * from the drawing so it can be reasoned about on its own. The view is stored the
 * way the browser thinks about it — a scale and an offset — and the conversion to
 * and from where a page actually sits is the whole of this module.
 */

export type GraphPoint = { x: number; y: number };
export type GraphViewTransform = { x: number; y: number; k: number };

/** How far in and out the view may be zoomed. */
export const MIN_ZOOM = 0.35;
export const MAX_ZOOM = 3;

export function clampZoom(k: number): number {
  if (!Number.isFinite(k)) return 1;
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, k));
}

/** Where a point on the screen is on the canvas. */
export function toCanvas(
  point: GraphPoint,
  view: GraphViewTransform,
): GraphPoint {
  return { x: (point.x - view.x) / view.k, y: (point.y - view.y) / view.k };
}

/** Where a point on the canvas is on the screen. */
export function toScreen(
  point: GraphPoint,
  view: GraphViewTransform,
): GraphPoint {
  return { x: point.x * view.k + view.x, y: point.y * view.k + view.y };
}

export function panBy(view: GraphViewTransform, dx: number, dy: number): GraphViewTransform {
  return { ...view, x: view.x + dx, y: view.y + dy };
}

/**
 * Zooms in or out while keeping whatever is under the pointer where it is.
 *
 * Zooming towards the middle of the screen instead would feel like the graph is
 * running away from the pointer, which is why the point is passed in.
 */
export function zoomAt(
  view: GraphViewTransform,
  point: GraphPoint,
  factor: number,
): GraphViewTransform {
  const k = clampZoom(view.k * factor);
  if (k === view.k) return view;
  return {
    k,
    x: point.x - ((point.x - view.x) / view.k) * k,
    y: point.y - ((point.y - view.y) / view.k) * k,
  };
}

/**
 * The page under a point on the canvas, or nothing.
 *
 * Searched from the end, so the page drawn last — the one on top — is the one
 * picked when they overlap. A little slack is allowed, because a small circle is
 * hard to hit exactly.
 */
export function hitNode(
  point: GraphPoint,
  nodes: Pick<ForceNode, "id" | "x" | "y" | "radius">[],
  slack = 4,
): string | null {
  for (let i = nodes.length - 1; i >= 0; i -= 1) {
    const node = nodes[i];
    if (Math.hypot(point.x - node.x, point.y - node.y) <= node.radius + slack) {
      return node.id;
    }
  }
  return null;
}

/**
 * The graph with the hidden categories left out.
 *
 * A link goes when either end does: a line to a page that is not drawn has
 * nowhere to point. "Not written yet" counts as a category of its own, because a
 * reader looking at what exists usually wants those out of the way.
 */
export function filterGraph(
  nodes: WikiGraphNode[],
  edges: WikiGraphEdge[],
  hidden: string[],
): { nodes: WikiGraphNode[]; edges: WikiGraphEdge[] } {
  if (hidden.length === 0) return { nodes, edges };
  const hiddenSet = new Set(hidden);
  const kept = nodes.filter(
    (node) => !hiddenSet.has(isGhost(node) ? "ghost" : node.type),
  );
  const keptIds = new Set(kept.map((node) => node.id));
  return {
    nodes: kept,
    edges: edges.filter(
      (edge) => keptIds.has(edge.source) && keptIds.has(edge.target),
    ),
  };
}

/**
 * What stays at full strength when a page is being looked at.
 *
 * The page itself and the pages it links to, so a reader can see what one page is
 * about without losing the rest of the graph entirely. Asking about no page
 * leaves everything as it is.
 */
export function highlightOf(
  id: string | null | undefined,
  neighbors: (id: string) => string[],
): Set<string> | null {
  if (!id) return null;
  return new Set([id, ...neighbors(id)]);
}
