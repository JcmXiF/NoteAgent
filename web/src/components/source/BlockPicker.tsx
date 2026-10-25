import { useCallback, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import {
  applyFrameStyle,
  bandsFor,
  blockRects,
  drawBands,
  ensureLayer,
  HIT_LAYER_ID,
  hostColors,
  leafBlocks,
  MARQUEE_ID,
  paintSelection,
  scrollToBlock,
  BLOCK_ATTR,
} from "@/lib/block-frame";
import {
  hitTest,
  isDragSelection,
  normalizeBox,
  scrollDelta,
  toDocPoint,
  type Box,
  type Point,
} from "@/lib/selection";
import { roundIdForSelection } from "@/lib/deep-search";
import { useClipsStore } from "@/stores/clips-store";
import { useDeepSearchStore } from "@/stores/deep-search-store";

/**
 * Shows a stored page and lets passages be selected by dragging a box over them.
 *
 * The drag is handled imperatively: pointer moves move the marquee and update the
 * highlighting directly, and the store is told once, when the drag ends. A React
 * render per pointer move would be wasted, and on a long page it would be visible
 * as lag.
 */
export function BlockPicker({
  html,
  title,
  blocks,
  sourceKey,
  sourceLabel,
  selectedIds,
  focusId,
}: {
  html: string;
  title: string;
  /** The page's blocks in document order; a selection records their text. */
  blocks: { id: string; tag: string; text: string; preview: string }[];
  sourceKey: string;
  sourceLabel: string;
  /** Blocks that belong to a selection; drawn with an outline. */
  selectedIds: string[];
  /** Block to bring into view, when the user arrives from a selection. */
  focusId?: string | null;
}) {
  const { t } = useTranslation();
  const hostRef = useRef<HTMLDivElement>(null);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const cleanupRef = useRef<(() => void) | null>(null);
  const dragRef = useRef<{ originView: Point; ids: string[] } | null>(null);

  // The handlers below are attached once per document and then live on, so they
  // read through refs instead of closing over the values of the render that
  // bound them. Otherwise a selection made later would use an older `blocks`
  // list, and a drag that selects nothing would repaint from stale state.
  const latest = useRef({ blocks, sourceKey, sourceLabel });
  latest.current = { blocks, sourceKey, sourceLabel };

  const paint = useCallback(() => {
    const frame = frameRef.current;
    const host = hostRef.current;
    const doc = frame?.contentDocument;
    const win = frame?.contentWindow;
    if (!doc?.body || !win || !host) return;
    paintSelection(doc, win, selectedIds, focusId);
  }, [selectedIds, focusId]);

  const paintRef = useRef<() => void>(() => {});
  paintRef.current = paint;

  const bind = useCallback(() => {
    const frame = frameRef.current;
    const host = hostRef.current;
    const doc = frame?.contentDocument;
    const win = frame?.contentWindow;
    if (!frame || !doc?.body || !win || !host) return undefined;

    applyFrameStyle(doc, hostColors(host), true);

    const hitLayer = ensureLayer(doc, HIT_LAYER_ID);
    const marquee = doc.createElement("div");
    marquee.id = MARQUEE_ID;
    marquee.hidden = true;
    doc.body.appendChild(marquee);

    /**
     * A pointer position in the frame's viewport coordinates.
     *
     * The two listeners below live in different documents and therefore measure
     * `clientX/Y` differently, so each converts its own coordinates instead of
     * asking the event which document it came from.
     */
    const fromFrameEvent = (event: MouseEvent): Point => ({
      x: event.clientX,
      y: event.clientY,
    });
    const fromWindowEvent = (event: MouseEvent): Point => {
      const rect = frame.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    };

    const docBoxFrom = (from: Point, to: Point): Box =>
      normalizeBox(
        toDocPoint(from, { x: win.scrollX, y: win.scrollY }),
        toDocPoint(to, { x: win.scrollX, y: win.scrollY }),
      );

    let lastView: Point = { x: 0, y: 0 };
    let ticking = false;

    /**
     * Scrolls while the pointer sits near an edge, and keeps the selection current.
     *
     * The loop is driven by the host window, not the frame's. A frame whose
     * document is reported as hidden gets its animation frames suppressed, which
     * would stop the scrolling without any visible cause; the host window is the
     * one actually painting the screen.
     */
    const tick = () => {
      if (!dragRef.current) {
        ticking = false;
        return;
      }
      const dx = scrollDelta(lastView.x, frame.clientWidth);
      const dy = scrollDelta(lastView.y, frame.clientHeight);
      if (dx || dy) {
        win.scrollBy(dx, dy);
        refreshSelection(lastView);
      }
      window.requestAnimationFrame(tick);
    };

    /**
     * Stops the scroll loop for this drag.
     *
     * The flag is cleared here as well as inside the loop: a frame callback may
     * never arrive, and waiting for one would leave the loop latched off.
     */
    const stopTick = () => {
      ticking = false;
    };

    const refreshSelection = (view: Point) => {
      const drag = dragRef.current;
      if (!drag) return;
      const box = normalizeBox(drag.originView, view);
      marquee.hidden = false;
      marquee.style.left = `${box.left}px`;
      marquee.style.top = `${box.top}px`;
      marquee.style.width = `${Math.max(0, box.right - box.left)}px`;
      marquee.style.height = `${Math.max(0, box.bottom - box.top)}px`;

      const rects = blockRects(doc, win);
      drag.ids = hitTest(docBoxFrom(drag.originView, view), rects);
      const wanted = new Set(drag.ids);
      for (const el of leafBlocks(doc)) {
        const id = el.getAttribute(BLOCK_ATTR);
        el.classList.toggle("na-hit", Boolean(id && wanted.has(id)));
      }
      const order = rects.map((item) => item.id);
      drawBands(hitLayer, bandsFor(drag.ids, rects, order), ["na-hit-band"]);
    };

    const clearDrag = () => {
      stopTick();
      doc.body.classList.remove("na-dragging");
      marquee.hidden = true;
      drawBands(hitLayer, [], ["na-hit-band"]);
      for (const el of leafBlocks(doc)) el.classList.remove("na-hit");
    };

    const onDown = (event: MouseEvent) => {
      if (event.button !== 0) return;
      const target = event.target as Node | null;
      // A link would navigate the frame away from the captured page, leaving
      // nothing to pick from.
      if (target && target.nodeType === 1 && (target as Element).closest("a[href]")) {
        event.preventDefault();
      }
      event.preventDefault();
      lastView = fromFrameEvent(event);
      dragRef.current = { originView: lastView, ids: [] };
      doc.body.classList.add("na-dragging");
      if (!ticking) {
        ticking = true;
        window.requestAnimationFrame(tick);
      }
    };

    const onMove = (event: MouseEvent) => {
      if (!dragRef.current) return;
      lastView = fromFrameEvent(event);
      refreshSelection(lastView);
    };

    const onUp = (event: MouseEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const view = fromFrameEvent(event);
      const box = docBoxFrom(drag.originView, view);
      // A click, not a drag: the page was not asking for a selection.
      const ids = isDragSelection(box) ? hitTest(box, blockRects(doc, win)) : [];
      dragRef.current = null;
      clearDrag();

      if (ids.length > 0) {
        useClipsStore.getState().addCollection({
          sourceKey: latest.current.sourceKey,
          sourceLabel: latest.current.sourceLabel,
          blockIds: ids,
          blocks: latest.current.blocks,
          roundId: roundIdForSelection(useDeepSearchStore.getState().session),
        });
      } else {
        paintRef.current();
      }
    };

    const onDragStart = (event: Event) => event.preventDefault();
    /** A drag can finish outside the frame; treat that as finishing it. */
    const onWindowMove = (event: MouseEvent) => {
      if (!dragRef.current) return;
      lastView = fromWindowEvent(event);
      refreshSelection(lastView);
    };
    const onWindowUp = (event: MouseEvent) => {
      if (!dragRef.current) return;
      const drag = dragRef.current;
      const box = docBoxFrom(drag.originView, fromWindowEvent(event));
      const ids = isDragSelection(box) ? hitTest(box, blockRects(doc, win)) : [];
      dragRef.current = null;
      clearDrag();
      if (ids.length > 0) {
        useClipsStore.getState().addCollection({
          sourceKey: latest.current.sourceKey,
          sourceLabel: latest.current.sourceLabel,
          blockIds: ids,
          blocks: latest.current.blocks,
          roundId: roundIdForSelection(useDeepSearchStore.getState().session),
        });
      } else {
        paintRef.current();
      }
    };

    doc.addEventListener("mousedown", onDown, true);
    doc.addEventListener("mousemove", onMove, true);
    doc.addEventListener("mouseup", onUp, true);
    doc.addEventListener("dragstart", onDragStart, true);
    window.addEventListener("mousemove", onWindowMove, true);
    window.addEventListener("mouseup", onWindowUp, true);
    paintRef.current();

    return () => {
      dragRef.current = null;
      stopTick();
      doc.removeEventListener("mousedown", onDown, true);
      doc.removeEventListener("mousemove", onMove, true);
      doc.removeEventListener("mouseup", onUp, true);
      doc.removeEventListener("dragstart", onDragStart, true);
      window.removeEventListener("mousemove", onWindowMove, true);
      window.removeEventListener("mouseup", onWindowUp, true);
      marquee.remove();
      hitLayer.remove();
    };
  }, []);

  const bindRef = useRef(bind);
  bindRef.current = bind;

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return undefined;
    const run = () => {
      cleanupRef.current?.();
      cleanupRef.current = bindRef.current() ?? null;
    };
    frame.addEventListener("load", run);
    // A srcDoc document is often already complete before React attaches.
    if (frame.contentDocument?.readyState === "complete") run();
    return () => {
      frame.removeEventListener("load", run);
      cleanupRef.current?.();
      cleanupRef.current = null;
    };
  }, [html, title]);

  // Selection changes repaint without rebinding.
  useEffect(() => {
    paint();
  }, [paint]);

  // Bring the focused block into view, so arriving from a selection shows where.
  useEffect(() => {
    const doc = frameRef.current?.contentDocument;
    if (doc) scrollToBlock(doc, focusId);
  }, [focusId]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b px-3 py-1 text-[11px] text-muted-foreground">
        <span className="truncate">{title}</span>
        <span className="ml-auto shrink-0 tabular-nums">
          {t("sources.picker.selectedCount", { count: selectedIds.length })}
        </span>
      </div>
      <div ref={hostRef} className="relative min-h-0 flex-1 bg-white">
        <iframe
          ref={frameRef}
          title={title}
          // Same origin so the page can be inspected and styled, but scripts are
          // never allowed: the page is shown, not run.
          sandbox="allow-same-origin"
          referrerPolicy="no-referrer"
          srcDoc={html}
          className="size-full border-0"
        />
      </div>
      <p className="shrink-0 border-t px-3 py-1 text-[11px] text-muted-foreground">
        {t("sources.picker.dragHint")}
      </p>
    </div>
  );
}
