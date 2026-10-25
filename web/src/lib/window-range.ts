/**
 * Which rows of a long list are worth having in the document.
 *
 * A rendered row costs memory and layout whether or not it is on screen, and a
 * list can be long enough that rendering all of it is the slowest thing on the
 * page — a PDF with hundreds of pages gives one row per page. Only the rows near
 * the viewport are drawn, with a little either side so a fast scroll does not
 * show a gap.
 */

type WindowRange = {
  /** First row to draw. */
  start: number;
  /** One past the last row to draw. */
  end: number;
};

type WindowInput = {
  /** Rows in the whole list. */
  total: number;
  /** Height of one row; every row must be this tall for the arithmetic to hold. */
  rowHeight: number;
  /** How tall the scroller is. */
  viewport: number;
  /** How far it has been scrolled. */
  scrollTop: number;
  /** Rows drawn beyond each edge. */
  overscan?: number;
};

/** The rows to draw, clamped to the list. */
export function windowRange(input: WindowInput): WindowRange {
  const { total, rowHeight, viewport, scrollTop } = input;
  const overscan = input.overscan ?? 6;
  if (total <= 0) return { start: 0, end: 0 };
  if (!Number.isFinite(rowHeight) || rowHeight <= 0) return { start: 0, end: total };

  // A scroller that has not been measured yet gets the whole list rather than
  // nothing: an empty list is worse than a heavy one, and the measurement
  // arrives on the next frame.
  if (!Number.isFinite(viewport) || viewport <= 0) return { start: 0, end: total };

  const first = Math.floor(scrollTop / rowHeight) - overscan;
  const last = Math.ceil((scrollTop + viewport) / rowHeight) + overscan;
  return {
    start: Math.max(0, Math.min(first, total - 1)),
    end: Math.max(0, Math.min(last, total)),
  };
}

/** How tall the spacer behind the drawn rows has to be. */
export function windowHeight(total: number, rowHeight: number): number {
  return Math.max(0, total) * Math.max(0, rowHeight);
}
