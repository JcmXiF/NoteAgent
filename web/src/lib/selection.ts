/**
 * Geometry for picking passages out of a rendered document.
 *
 * The pieces here are pure so they can be reasoned about — and tested — without
 * a browser. Reading rectangles out of the document is the caller's job; this
 * module only decides what a rectangle means.
 */

export type Point = { x: number; y: number };

export type Box = {
  left: number;
  top: number;
  right: number;
  bottom: number;
};

/** A block's rectangle, in the coordinate space of the caller's choice. */
export type BlockRect = {
  id: string;
  box: Box;
};

type Size = { width: number; height: number };

/** Distance from an edge at which dragging starts scrolling. */
const EDGE_ZONE = 40;
/** Most a single animation frame will scroll, in pixels. */
const MAX_SCROLL_STEP = 28;

export function normalizeBox(a: Point, b: Point): Box {
  return {
    left: Math.min(a.x, b.x),
    top: Math.min(a.y, b.y),
    right: Math.max(a.x, b.x),
    bottom: Math.max(a.y, b.y),
  };
}

/**
 * Converts a point in a frame's viewport to the document inside it.
 *
 * Pointer positions arrive in viewport coordinates, while block rectangles have
 * to be compared in document coordinates once the page has scrolled. Keeping the
 * conversion in one place means the two conventions cannot drift apart.
 */
export function toDocPoint(view: Point, scroll: Point): Point {
  return { x: view.x + scroll.x, y: view.y + scroll.y };
}

/** The reverse of `toDocPoint`. */
export function toViewPoint(doc: Point, scroll: Point): Point {
  return { x: doc.x - scroll.x, y: doc.y - scroll.y };
}

/** A drag shorter than this is treated as a click, not a selection. */
const MIN_DRAG_PX = 8;

/** Whether a drag box is large enough to count as a selection. */
export function isDragSelection(box: Box, min = MIN_DRAG_PX): boolean {
  const size = boxSize(box);
  return size.width >= min || size.height >= min;
}

export function boxSize(box: Box): Size {
  return {
    width: box.right - box.left,
    height: box.bottom - box.top,
  };
}

/**
 * Whether two rectangles overlap.
 *
 * Touching edges do not count: a drag that stops exactly at a block's top edge
 * should not pick it up.
 */
export function intersects(a: Box, b: Box): boolean {
  return (
    a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top
  );
}

/**
 * The blocks a selection rectangle covers, in document order.
 *
 * The order comes from the input, so callers pass blocks in the order the
 * document reads and the result follows the page rather than the drag.
 */
export function hitTest(selection: Box, blocks: BlockRect[]): string[] {
  const hits: string[] = [];
  for (const block of blocks) {
    if (intersects(selection, block.box)) hits.push(block.id);
  }
  return hits;
}

/**
 * How far to scroll while the pointer is near an edge.
 *
 * The step grows as the pointer approaches the edge (up to twice the base step),
 * so a drag that reaches the very edge keeps moving.
 */
export function scrollDelta(
  position: number,
  size: number,
  edge = EDGE_ZONE,
  maxStep = MAX_SCROLL_STEP,
): number {
  if (size <= 0 || edge <= 0) return 0;
  if (position < edge) {
    const closeness = Math.min((edge - position) / edge, 2);
    return -maxStep * closeness;
  }
  const far = size - edge;
  if (position > far) {
    const closeness = Math.min((position - far) / edge, 2);
    return maxStep * closeness;
  }
  return 0;
}

/**
 * Splits a set of selected ids into runs that are adjacent in the document.
 *
 * Runs are what get an outline drawn around them, so a selection spanning three
 * consecutive paragraphs is boxed as one region, while the same ids scattered
 * across the page are boxed separately.
 */
export function consecutiveRuns(ids: string[], documentOrder: string[]): string[][] {
  const wanted = new Set(ids);
  const runs: string[][] = [];
  let current: string[] = [];

  for (const id of documentOrder) {
    if (wanted.has(id)) {
      current.push(id);
      wanted.delete(id);
    } else if (current.length > 0) {
      runs.push(current);
      current = [];
    }
  }
  if (current.length > 0) runs.push(current);

  // Ids the document did not know about still belong somewhere; appending them
  // keeps them visible instead of silently dropping part of a selection.
  if (wanted.size > 0) runs.push(ids.filter((id) => wanted.has(id)));

  return runs;
}

/** The smallest box containing all of the given boxes. */
export function unionBoxes(boxes: Box[], pad = 0): Box | null {
  if (boxes.length === 0) return null;
  let left = Infinity;
  let top = Infinity;
  let right = -Infinity;
  let bottom = -Infinity;
  for (const box of boxes) {
    left = Math.min(left, box.left);
    top = Math.min(top, box.top);
    right = Math.max(right, box.right);
    bottom = Math.max(bottom, box.bottom);
  }
  if (!Number.isFinite(left)) return null;
  return {
    left: left - pad,
    top: top - pad,
    right: right + pad,
    bottom: bottom + pad,
  };
}
