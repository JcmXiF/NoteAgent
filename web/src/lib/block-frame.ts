import {
  consecutiveRuns,
  unionBoxes,
  type BlockRect,
  type Box,
} from "@/lib/selection";

/**
 * Drawing on a rendered original.
 *
 * A stored page is shown in a frame, and both the picker and the read-only
 * viewer have to mark the same things in it: which passages are already in a
 * selection, and which one the reader came to see. Only presentation and
 * measurement live here — nothing in this module adds, moves or removes the
 * content itself. The blocks are marked by the backend when it writes the HTML.
 */

/** Marks a leaf block in the rendered document. Written by the backend. */
export const BLOCK_ATTR = "data-na-block";
/** Id prefix the backend gives each block, so `na-b3` can be scrolled to. */
const BLOCK_ID_PREFIX = "na-";

const STYLE_ID = "na-frame-style";
const SET_LAYER_ID = "na-set-bands";
export const HIT_LAYER_ID = "na-hit-bands";
export const MARQUEE_ID = "na-marquee-box";
/** Breathing room around a run of blocks, so its outline is visible. */
const BAND_PAD = 8;

/**
 * Styles for the rendered document.
 *
 * The colours are read from the host document at runtime and passed in: a frame
 * is a separate document and cannot see the parent's custom properties, so
 * hardcoding them would mean a second palette to keep in step — and dark mode
 * would not follow.
 *
 * `interactive` adds what only a pickable document needs: the crosshair, the
 * band drawn while dragging, and the marquee. A reader gets the marks without any
 * sign that the page can be dragged over.
 */
function frameStyle(
  colors: { clip: string; cite: string },
  interactive: boolean,
): string {
  const clip = colors.clip;
  const cite = colors.cite;
  const shared = `
[${BLOCK_ATTR}]{border-radius:4px;transition:background-color .12s ease,outline-color .12s ease,box-shadow .12s ease;}
[${BLOCK_ATTR}].na-in-set{outline:1px solid ${clip};outline-offset:2px;background-color:color-mix(in oklab, ${clip} 10%, transparent);}
[${BLOCK_ATTR}].na-focus{outline:2px solid ${cite};outline-offset:2px;background-color:color-mix(in oklab, ${cite} 18%, transparent);}
figure[${BLOCK_ATTR}].na-in-set,img[${BLOCK_ATTR}].na-in-set,table[${BLOCK_ATTR}].na-in-set,pre[${BLOCK_ATTR}].na-in-set{
  box-shadow:0 0 0 6px color-mix(in oklab, ${clip} 16%, transparent);}
figure[${BLOCK_ATTR}].na-focus,img[${BLOCK_ATTR}].na-focus,table[${BLOCK_ATTR}].na-focus,pre[${BLOCK_ATTR}].na-focus{
  box-shadow:0 0 0 8px color-mix(in oklab, ${cite} 22%, transparent);}
#${SET_LAYER_ID},#${HIT_LAYER_ID}{position:absolute;left:0;top:0;width:0;height:0;overflow:visible;pointer-events:none;z-index:2;}
.na-set-band,.na-hit-band{position:absolute;pointer-events:none;border-radius:8px;}
.na-set-band{background:color-mix(in oklab, ${clip} 10%, transparent);outline:1px solid color-mix(in oklab, ${clip} 32%, transparent);}
.na-set-band-focus{background:color-mix(in oklab, ${clip} 18%, transparent);outline:2px solid color-mix(in oklab, ${clip} 55%, transparent);}
[${BLOCK_ATTR}].na-cited{outline:2px solid ${cite};outline-offset:3px;background-color:color-mix(in oklab, ${cite} 20%, transparent);}
figure[${BLOCK_ATTR}].na-cited,img[${BLOCK_ATTR}].na-cited,table[${BLOCK_ATTR}].na-cited,pre[${BLOCK_ATTR}].na-cited{
  box-shadow:0 0 0 8px color-mix(in oklab, ${cite} 24%, transparent);}
`;
  if (!interactive) return `${shared}
a[href]{pointer-events:none;}
`;
  return `${shared}
[${BLOCK_ATTR}].na-hit{outline:2px solid ${clip};outline-offset:2px;background-color:color-mix(in oklab, ${clip} 18%, transparent);}
figure[${BLOCK_ATTR}].na-hit,img[${BLOCK_ATTR}].na-hit,table[${BLOCK_ATTR}].na-hit,pre[${BLOCK_ATTR}].na-hit{
  box-shadow:0 0 0 8px color-mix(in oklab, ${clip} 22%, transparent);}
.na-hit-band{background:color-mix(in oklab, ${cite} 10%, transparent);outline:1px dashed color-mix(in oklab, ${cite} 55%, transparent);}
#${MARQUEE_ID}{position:fixed;pointer-events:none;z-index:9;border:1px dashed ${cite};background:color-mix(in oklab, ${cite} 14%, transparent);}
body{cursor:crosshair;}
body.na-dragging{user-select:none;}
body.na-dragging *{cursor:crosshair !important;}
`;
}

/**
 * Puts the frame's stylesheet in place, replacing whatever was there.
 *
 * A frame is re-rendered whenever the source changes, so the sheet is written on
 * every bind rather than kept from the last document.
 */
export function applyFrameStyle(
  doc: Document,
  colors: { clip: string; cite: string },
  interactive: boolean,
): void {
  let style = doc.getElementById(STYLE_ID) as HTMLStyleElement | null;
  if (!style) {
    style = doc.createElement("style");
    style.id = STYLE_ID;
    (doc.head ?? doc.documentElement).appendChild(style);
  }
  style.textContent = frameStyle(colors, interactive);
}

/** Reads the two colours a frame needs out of the host document. */
export function hostColors(host: Element): { clip: string; cite: string } {
  const styles = getComputedStyle(host);
  return {
    clip: styles.getPropertyValue("--clip").trim() || "oklch(0.68 0.14 65)",
    cite: styles.getPropertyValue("--cite").trim() || "oklch(0.58 0.13 195)",
  };
}

export function ensureLayer(doc: Document, id: string): HTMLElement {
  const existing = doc.getElementById(id);
  if (existing) return existing;
  const created = doc.createElement("div");
  created.id = id;
  doc.body.appendChild(created);
  return created;
}

/**
 * Positions a layer's children onto the given boxes, reusing nodes so a repaint
 * does not churn the DOM.
 */
export function drawBands(layer: HTMLElement, boxes: Box[], classes: string[]): void {
  const nodes = [...layer.children] as HTMLElement[];
  boxes.forEach((box, index) => {
    let node = nodes[index];
    if (!node) {
      node = layer.ownerDocument.createElement("div");
      layer.appendChild(node);
    }
    node.className = classes[index] ?? classes[0] ?? "";
    node.style.left = `${box.left}px`;
    node.style.top = `${box.top}px`;
    node.style.width = `${Math.max(0, box.right - box.left)}px`;
    node.style.height = `${Math.max(0, box.bottom - box.top)}px`;
  });
  for (let i = boxes.length; i < nodes.length; i += 1) nodes[i].remove();
}

/**
 * The block's rectangle in the frame document's coordinates.
 *
 * Document coordinates rather than the frame's viewport, because the marks are
 * drawn into a layer that is positioned against the document. Measuring from the
 * viewport instead would offset every mark by whatever margin the page puts
 * around its body — and by the scroll offset as the reader moves down.
 */
export function documentBox(element: Element, win: Window): Box {
  const rect = element.getBoundingClientRect();
  return {
    left: rect.left + win.scrollX,
    top: rect.top + win.scrollY,
    right: rect.right + win.scrollX,
    bottom: rect.bottom + win.scrollY,
  };
}

/**
 * Leaf blocks only: a wrapper that contains blocks is not itself selectable.
 *
 * Note the return type: elements inside a frame belong to that frame's realm, so
 * `instanceof HTMLElement` (checked against the parent's constructor) is always
 * false for them. Only `Element` APIs are safe here.
 */
export function leafBlocks(doc: Document): Element[] {
  const found: Element[] = [];
  doc.querySelectorAll(`[${BLOCK_ATTR}]`).forEach((el) => {
    if (el.querySelector(`[${BLOCK_ATTR}]`)) return;
    found.push(el);
  });
  return found;
}

/** Every leaf block's rectangle, in the frame document's coordinates. */
export function blockRects(doc: Document, win: Window): BlockRect[] {
  const rects: BlockRect[] = [];
  for (const el of leafBlocks(doc)) {
    const id = el.getAttribute(BLOCK_ATTR);
    if (!id) continue;
    rects.push({ id, box: documentBox(el, win) });
  }
  return rects;
}

/** Groups the given ids into runs and outlines each run as one band. */
export function bandsFor(ids: string[], rects: BlockRect[], order: string[]): Box[] {
  const byId = new Map(rects.map((item) => [item.id, item.box]));
  const boxes: Box[] = [];
  for (const run of consecutiveRuns(ids, order)) {
    const union = unionBoxes(
      run.map((id) => byId.get(id)).filter((box): box is Box => Boolean(box)),
      BAND_PAD,
    );
    if (union) boxes.push(union);
  }
  return boxes;
}

/**
 * Marks the selected passages and draws a band around each run of them.
 *
 * Returns the document order of the blocks, which callers need for grouping ids
 * into runs and for ordering a selection the way the page reads.
 */
export function paintSelection(
  doc: Document,
  win: Window,
  selectedIds: string[],
  focusId?: string | null,
): string[] {
  const wanted = new Set(selectedIds);
  const order: string[] = [];
  for (const el of leafBlocks(doc)) {
    const id = el.getAttribute(BLOCK_ATTR);
    if (!id) continue;
    order.push(id);
    el.classList.toggle("na-in-set", wanted.has(id));
    el.classList.toggle("na-focus", id === focusId);
  }

  const boxes: Box[] = [];
  const classes: string[] = [];
  for (const run of consecutiveRuns(selectedIds, order)) {
    const elements = run
      .map((id) => doc.querySelector(`#${CSS.escape(`${BLOCK_ID_PREFIX}${id}`)}`))
      .filter((el): el is Element => Boolean(el));
    const union = unionBoxes(
      elements.map((el) => documentBox(el, win)),
      BAND_PAD,
    );
    if (!union) continue;
    boxes.push(union);
    classes.push(
      focusId && run.includes(focusId) ? "na-set-band na-set-band-focus" : "na-set-band",
    );
  }
  drawBands(ensureLayer(doc, SET_LAYER_ID), boxes, classes);
  return order;
}

/**
 * Brings a block into view, so arriving from a selection shows where it is.
 *
 * Scrolling is left to the browser rather than computed here: the frame's layout
 * is its own, and `scrollIntoView` already accounts for whatever is in the way.
 */
export function scrollToBlock(doc: Document, blockId: string | null | undefined): void {
  if (!blockId) return;
  doc
    .getElementById(`${BLOCK_ID_PREFIX}${blockId}`)
    ?.scrollIntoView({ block: "center", behavior: "smooth" });
}
