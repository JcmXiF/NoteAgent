import { useCallback, useEffect, useRef } from "react";
import type { WikiGraphEdge, WikiGraphNode } from "@/lib/api/types";
import { countRender } from "@/lib/dev-counters";
import {
  hitNode,
  panBy,
  toCanvas,
  zoomAt,
  type GraphPoint,
  type GraphViewTransform,
} from "@/lib/graph-interaction";
import {
  averageSpeed,
  nodeRadiusForDegree,
  seedForceNodes,
  stepForceLayout,
  type ForceNode,
} from "@/lib/wiki-force";
import { cn } from "@/lib/utils";
import { edgeKey } from "@/lib/wiki-graph";
import { overlayBadge, overlayMarkOf, type GraphOverlay } from "@/lib/graph-overlay";

/** The loop gives up once the graph is moving less than this, on average. */
const SETTLED = 0.35;
/** And at this many steps regardless, so a busy graph cannot animate forever. */
const MAX_STEPS = 600;
/** How far the pointer may move before a press counts as a drag rather than a click. */
const DRAG_SLOP = 4;
/** Zoom committed to the store once this long passes without another wheel turn. */
const ZOOM_COMMIT_MS = 200;
/**
 * Room the drawing needs around a page beyond its circle: the name sits under it
 * and the count of material beside it. Held here because this is the file that
 * draws them, and handed to the layout so a page against an edge keeps its marks.
 */
const NODE_Y_MARGIN = 26;

type Drag =
  | { kind: "pan"; start: GraphPoint; origin: GraphViewTransform; moved: boolean }
  | { kind: "node"; id: string; start: GraphPoint; moved: boolean };

/**
 * Draws the link graph, settles it, and lets a reader move around it.
 *
 * Everything that happens between frames stays out of React: the simulation lives
 * in a ref, and panning, zooming and dragging a page write straight to the
 * elements that were rendered once. React renders the structure when the data or
 * the highlighting changes — not while the picture is moving.
 */
export function GraphCanvas({
  nodes,
  edges,
  width,
  height,
  view,
  onViewChange,
  selectedId,
  hoverId,
  highlight,
  overlay,
  onSelect,
  onHover,
}: {
  nodes: WikiGraphNode[];
  edges: WikiGraphEdge[];
  width: number;
  height: number;
  /** Where the view is panned and zoomed to, as the store remembers it. */
  view: GraphViewTransform;
  /** Called with the view once a pan or zoom has finished. */
  onViewChange: (view: GraphViewTransform) => void;
  selectedId: string | null;
  hoverId: string | null;
  /** The pages that stay at full strength, or null when nothing is being looked at. */
  highlight: Set<string> | null;
  /** What a round is proposing, or null when no round is proposing anything. */
  overlay: GraphOverlay | null;
  onSelect: (id: string | null) => void;
  onHover: (id: string | null) => void;
}) {
  if (import.meta.env.DEV) countRender("GraphCanvas");

  const hostRef = useRef<SVGSVGElement>(null);
  const worldRef = useRef<SVGGElement>(null);
  const simRef = useRef<ForceNode[]>([]);
  const dragRef = useRef<Drag | null>(null);
  const pinnedRef = useRef<string | null>(null);

  // The view as it is right now, which during a gesture is ahead of the store.
  const viewRef = useRef(view);
  // The loop, kept so a gesture can start it again after it has settled.
  const loopRef = useRef<(() => void) | null>(null);
  const commitTimer = useRef<number | undefined>(undefined);
  // Read inside the handlers, which are bound once and must not close over a
  // stale selection.
  const latest = useRef({ onSelect, onHover, onViewChange, width, height });
  latest.current = { onSelect, onHover, onViewChange, width, height };

  const writeView = useCallback(() => {
    const world = worldRef.current;
    const current = viewRef.current;
    world?.setAttribute(
      "transform",
      `translate(${current.x} ${current.y}) scale(${current.k})`,
    );
  }, []);

  const commitView = useCallback(() => {
    latest.current.onViewChange(viewRef.current);
  }, []);

  // A view that changes from outside — a reset, or a restored one — is applied.
  useEffect(() => {
    if (
      view.x === viewRef.current.x &&
      view.y === viewRef.current.y &&
      view.k === viewRef.current.k
    ) {
      return;
    }
    viewRef.current = view;
    writeView();
  }, [view, writeView]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || width < 8 || height < 8 || nodes.length === 0) {
      simRef.current = [];
      loopRef.current = null;
      return undefined;
    }

    simRef.current = seedForceNodes(
      nodes.map((node) => ({ id: node.id, radius: nodeRadiusForDegree(node.degree) })),
      width,
      height,
    );

    // The elements to move, looked up once per render rather than per frame.
    const marks = new Map<string, SVGGElement>();
    for (const element of host.querySelectorAll<SVGGElement>("[data-node]")) {
      const id = element.dataset.node;
      if (id) marks.set(id, element);
    }
    const lines: { element: SVGLineElement; source: string; target: string }[] = [];
    for (const element of host.querySelectorAll<SVGLineElement>("[data-edge]")) {
      const source = element.dataset.source;
      const target = element.dataset.target;
      if (source && target) lines.push({ element, source, target });
    }

    const write = () => {
      const byId = new Map(simRef.current.map((node) => [node.id, node]));
      for (const node of simRef.current) {
        marks.get(node.id)?.setAttribute("transform", `translate(${node.x} ${node.y})`);
      }
      for (const line of lines) {
        const a = byId.get(line.source);
        const b = byId.get(line.target);
        if (!a || !b) continue;
        line.element.setAttribute("x1", String(a.x));
        line.element.setAttribute("y1", String(a.y));
        line.element.setAttribute("x2", String(b.x));
        line.element.setAttribute("y2", String(b.y));
      }
    };

    const links = edges.map((edge) => ({ source: edge.source, target: edge.target }));
    let frame = 0;
    let steps = 0;

    const tick = () => {
      steps += 1;
      stepForceLayout(simRef.current, links, width, height, pinnedRef.current, {
        x: 0,
        y: NODE_Y_MARGIN,
      });
      write();
      if (averageSpeed(simRef.current) > SETTLED && steps < MAX_STEPS) {
        frame = window.requestAnimationFrame(tick);
      }
    };

    // A gesture can restart the loop; the step count starts over so a page being
    // dragged always has time to push its neighbours out of the way.
    loopRef.current = () => {
      steps = 0;
      frame = window.requestAnimationFrame(tick);
    };

    // Drawn once before the loop starts, so the graph appears in place rather
    // than piled up at the middle.
    write();
    writeView();
    loopRef.current();
    return () => window.cancelAnimationFrame(frame);
  }, [nodes, edges, width, height, writeView]);

  // Pointing, panning and zooming. Bound to the host as native listeners rather
  // than through React props: the wheel listener has to be able to stop the page
  // from scrolling, which a passive one cannot.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;

    const localPoint = (event: PointerEvent | WheelEvent): GraphPoint => {
      const rect = host.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    };

    const canvasPoint = (event: PointerEvent | WheelEvent): GraphPoint =>
      toCanvas(localPoint(event), viewRef.current);

    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return;
      const point = localPoint(event);
      const hit = hitNode(canvasPoint(event), simRef.current);
      dragRef.current = hit
        ? { kind: "node", id: hit, start: point, moved: false }
        : { kind: "pan", start: point, origin: viewRef.current, moved: false };
      host.setPointerCapture(event.pointerId);
    };

    const onPointerMove = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag) {
        // Not dragging: say what is under the pointer, so a page and the pages it
        // links to can be picked out before anything is clicked.
        latest.current.onHover(hitNode(canvasPoint(event), simRef.current));
        return;
      }
      const point = localPoint(event);
      const dx = point.x - drag.start.x;
      const dy = point.y - drag.start.y;
      if (Math.hypot(dx, dy) > DRAG_SLOP) drag.moved = true;

      if (drag.kind === "pan") {
        viewRef.current = panBy(drag.origin, dx, dy);
        writeView();
        return;
      }

      // A page being dragged follows the pointer and is held still by the
      // simulation, so it does not fight the hand that is moving it.
      const node = simRef.current.find((item) => item.id === drag.id);
      if (!node) return;
      const canvas = canvasPoint(event);
      node.x = canvas.x;
      node.y = canvas.y;
      node.vx = 0;
      node.vy = 0;
      pinnedRef.current = drag.id;
      const mark = host.querySelector<SVGGElement>(`[data-node="${CSS.escape(drag.id)}"]`);
      mark?.setAttribute("transform", `translate(${node.x} ${node.y})`);
    };

    const onPointerUp = () => {
      const drag = dragRef.current;
      dragRef.current = null;
      if (!drag) return;

      if (drag.kind === "pan") {
        if (drag.moved) {
          commitView();
          return;
        }
        // A press on the open canvas that did not move is a click on nothing,
        // which clears whatever was being looked at.
        latest.current.onSelect(null);
        return;
      }

      pinnedRef.current = null;
      // Let go: the graph starts moving again, so the page settles where it was
      // put and its neighbours make room.
      loopRef.current?.();
      if (drag.moved) return;
      // A press that did not move is a click: on a page it selects, on the open
      // canvas it clears.
      latest.current.onSelect(drag.id);
    };

    const onPointerLeave = () => {
      if (!dragRef.current) latest.current.onHover(null);
    };

    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const factor = event.deltaY > 0 ? 0.92 : 1.08;
      viewRef.current = zoomAt(viewRef.current, localPoint(event), factor);
      writeView();
      // Committed once the wheel stops, so a run of turns is one store update
      // rather than one each.
      window.clearTimeout(commitTimer.current);
      commitTimer.current = window.setTimeout(commitView, ZOOM_COMMIT_MS);
    };

    host.addEventListener("pointerdown", onPointerDown);
    host.addEventListener("pointermove", onPointerMove);
    host.addEventListener("pointerup", onPointerUp);
    host.addEventListener("pointerleave", onPointerLeave);
    host.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      window.clearTimeout(commitTimer.current);
      host.removeEventListener("pointerdown", onPointerDown);
      host.removeEventListener("pointermove", onPointerMove);
      host.removeEventListener("pointerup", onPointerUp);
      host.removeEventListener("pointerleave", onPointerLeave);
      host.removeEventListener("wheel", onWheel);
    };
  }, [commitView, writeView]);

  return (
    <svg ref={hostRef} className="size-full touch-none" role="img" style={{ cursor: "grab" }}>
      <g ref={worldRef}>
        <g data-layer="edges">
          {edges.map((edge) => {
            const dim = highlight ? !(highlight.has(edge.source) && highlight.has(edge.target)) : false;
            const proposed = overlay?.edges.has(edgeKey(edge)) ?? false;
            return (
              <line
                key={`${edge.source}\u0000${edge.target}`}
                data-edge="true"
                data-source={edge.source}
                data-target={edge.target}
                // A proposed link is drawn dashed in the accent colour: it is a
                // claim about the pages, not something the pages say yet.
                className={cn(
                  proposed ? "stroke-cite" : "stroke-border",
                  dim && "opacity-20",
                )}
                strokeDasharray={proposed ? "5 4" : undefined}
                strokeWidth={proposed ? 1.6 : 1 + Math.min(3, edge.count - 1) * 0.4}
              />
            );
          })}
        </g>
        <g data-layer="nodes">
          {nodes.map((node) => {
            const dim = highlight ? !highlight.has(node.id) : false;
            const isSelected = node.id === selectedId;
            const isHovered = node.id === hoverId;
            const mark = overlay ? overlayMarkOf(node.id, overlay) : null;
            const badge = overlay ? overlayBadge(node.id, overlay) : null;
            return (
              <g
                key={node.id}
                data-node={node.id}
                className={cn(
                  mark ? "text-cite" : node.ghost ? "text-muted-foreground" : "text-primary",
                  dim && "opacity-25",
                  // A proposal the reader has ticked off stays visible but recedes,
                  // so the shape of the whole proposal is still readable.
                  mark && !mark.accepted && "opacity-40",
                  isHovered && !isSelected && "text-cite",
                )}
              >
                {(isSelected || isHovered) && (
                  // A ring rather than a colour change, so it reads the same on
                  // both kinds of page and on either theme.
                  <circle
                    r={nodeRadiusForDegree(node.degree) + 4}
                    fill="none"
                    className="stroke-cite"
                    strokeWidth={isSelected ? 2 : 1.5}
                  />
                )}
                <circle
                  r={nodeRadiusForDegree(node.degree)}
                  fill="currentColor"
                  // A page that does not exist yet is drawn unfilled, so it reads
                  // as something missing rather than something written.
                  fillOpacity={node.ghost ? 0.08 : 0.85}
                  stroke="currentColor"
                  // Adding a page: dashed, like any page that is not written yet.
                  // Revising one: drawn heavy, because it is already there.
                  strokeDasharray={mark?.action === "add" || (!mark && node.ghost) ? "3 3" : undefined}
                  strokeWidth={mark?.action === "revise" ? 2.8 : 1.5}
                />
                {/* A circle on its own says nothing, so each carries the page's
                    name. It takes no pointer events, so what a click means is
                    decided by the node and not by its label. */}
                <text
                  y={nodeRadiusForDegree(node.degree) + 12}
                  textAnchor="middle"
                  className="fill-current text-[10px]"
                  pointerEvents="none"
                >
                  {node.title}
                </text>
                {/* How much material this page was given, and how much of it was
                    taken. Drawn inside the node's own group so it travels with the
                    page for free: positioning it from outside would lag behind
                    every pan and every frame of settling. It is a reading, not a
                    control — the panel is where material is ticked. */}
                {badge ? <BadgeCircle radius={nodeRadiusForDegree(node.degree)} {...badge} /> : null}
              </g>
            );
          })}
        </g>
      </g>
    </svg>
  );
}

/** The count of material taken for a page, drawn beside its node. */
function BadgeCircle({
  radius,
  picked,
  total,
}: {
  radius: number;
  picked: number;
  total: number;
}) {
  const text = `${picked}/${total}`;
  // Wide enough for the longest count this round can carry, and wider still when
  // the numbers are two digits.
  const width = 8 + text.length * 4.2;
  const x = radius * 0.7;
  const y = -radius - 13;
  return (
    <g data-badge="true" pointerEvents="none">
      <rect
        x={x}
        y={y}
        width={width}
        height={11}
        rx={5.5}
        className="fill-background stroke-cite"
        strokeWidth={1}
      />
      <text
        x={x + width / 2}
        y={y + 8}
        textAnchor="middle"
        className="fill-current text-[8px] tabular-nums"
      >
        {text}
      </text>
    </g>
  );
}
