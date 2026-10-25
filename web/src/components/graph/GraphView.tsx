import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, Maximize2, Network, RotateCcw } from "lucide-react";
import { GraphCanvas } from "@/components/graph/GraphCanvas";
import { DeepSearchPanel } from "@/components/graph/DeepSearchPanel";
import { EmptyState } from "@/components/layout/EmptyState";
import { Button } from "@/components/ui/button";
import { IconTip } from "@/components/ui/tooltip";
import { filterGraph, highlightOf } from "@/lib/graph-interaction";
import { buildAdjacency, ghostNodes, kindsPresent, neighborsOf } from "@/lib/wiki-graph";
import {
  buildOverlay,
  overlayEdges,
  overlayLegend,
  overlayMarkOf,
  overlayNodes,
} from "@/lib/graph-overlay";
import { cn } from "@/lib/utils";
import { useGraphStore } from "@/stores/graph-store";
import { useDeepSearchStore } from "@/stores/deep-search-store";
import { useNavStore } from "@/stores/nav-store";
import type { PaneSlot } from "@/stores/layout-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/** Page kinds, for the legend. Unknown kinds keep their own name. */
const TYPE_LABEL: Record<string, string> = {
  note: "graph.type.note",
  ghost: "graph.type.ghost",
};

/**
 * How each proposed thing is drawn in the legend, matching the graph itself: a
 * page to add is dashed, a page to revise is heavy, a proposed link is a dashed
 * line. They are listed apart from the page kinds because they are not kinds.
 */
const LEGEND_SWATCH: Record<string, string> = {
  add: "size-2 rounded-full border border-dashed border-cite",
  revise: "size-2 rounded-full border-2 border-cite",
  link: "h-px w-3 border-t border-dashed border-cite",
};

/**
 * The link graph.
 *
 * The stage is measured rather than fixed, because the layout needs to know how
 * much room it has: pages are kept inside the view, and the middle it pulls
 * towards is the middle of what is on screen.
 *
 * The stage element is rendered whatever the state is, with the notice inside it.
 * Measuring an element that is only there once there is something to draw would
 * leave the size at nothing for as long as the graph is loading, and the drawing
 * would start from a size that never arrived.
 */
export function GraphView({ slot }: { slot: PaneSlot }) {
  const { t } = useTranslation();
  const status = useGraphStore((state) => state.status);
  const error = useGraphStore((state) => state.error);
  const nodes = useGraphStore((state) => state.nodes);
  const edges = useGraphStore((state) => state.edges);
  const view = useGraphStore((state) => state.view);
  const setView = useGraphStore((state) => state.setView);
  const resetView = useGraphStore((state) => state.resetView);
  const selectedId = useGraphStore((state) => state.selectedId);
  const hoverId = useGraphStore((state) => state.hoverId);
  const select = useGraphStore((state) => state.select);
  const hover = useGraphStore((state) => state.hover);
  const hiddenKinds = useGraphStore((state) => state.hiddenKinds);
  const toggleKind = useGraphStore((state) => state.toggleKind);
  const showAllKinds = useGraphStore((state) => state.showAllKinds);
  const refreshGraph = useGraphStore((state) => state.refresh);
  const go = useNavStore((state) => state.go);
  const project = useWorkspaceStore((state) => state.current);
  const round = useDeepSearchStore((state) => state.session);
  const editRound = useDeepSearchStore((state) => state.edit);

  const stageRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const host = stageRef.current;
    if (!host) return undefined;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (box) setSize({ width: box.width, height: box.height });
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  // What the round is proposing, drawn on top of the graph rather than mixed into
  // it: these pages and links are not in the wiki yet, so the graph's own links
  // stay as they are.
  const overlay = useMemo(() => buildOverlay(round), [round]);
  const withOverlay = useMemo(
    () => ({
      nodes: overlayNodes(nodes, overlay),
      edges: overlayEdges(edges, overlay),
    }),
    [nodes, edges, overlay],
  );
  const shown = useMemo(
    () => filterGraph(withOverlay.nodes, withOverlay.edges, hiddenKinds),
    [withOverlay, hiddenKinds],
  );
  const adjacency = useMemo(
    () => buildAdjacency(shown.nodes, shown.edges),
    [shown.nodes, shown.edges],
  );
  const highlight = useMemo(
    () => highlightOf(selectedId ?? hoverId, (id) => neighborsOf(adjacency, id)),
    [selectedId, hoverId, adjacency],
  );
  const legend = useMemo(() => overlayLegend(overlay), [overlay]);

  const selected = selectedId
    ? (withOverlay.nodes.find((node) => node.id === selectedId) ?? null)
    : null;
  const selectedMark = selectedId ? overlayMarkOf(selectedId, overlay) : null;
  const ready = status !== "error" && withOverlay.nodes.length > 0;

  /** Ticking a proposed page off, which the round is at the step for. */
  const toggleProposed = (slug: string) => {
    const page = overlay.pages.get(slug);
    if (!project || !page) return;
    void editRound(project, { action: "togglePage", slug, accepted: !page.accepted });
  };

  return (
    <div className="flex size-full min-h-0 flex-col">
      <div ref={stageRef} className="relative min-h-0 flex-1 overflow-hidden bg-background">
        {ready ? (
          <GraphCanvas
            nodes={shown.nodes}
            edges={shown.edges}
            width={size.width}
            height={size.height}
            view={view}
            onViewChange={setView}
            selectedId={selectedId}
            hoverId={hoverId}
            highlight={highlight}
            overlay={overlay}
            onSelect={(id) => {
              select(id);
              // At the step where pages are proposed, clicking one of them is how it
              // is ticked off: the node is the control. While material is being
              // gathered it picks the page out instead, so the panel can show what
              // that page was given. Every other page still opens its note, which is
              // why the graph is useful — a link between two notes is easier to
              // trust when the note is beside it.
              const mark = id ? overlayMarkOf(id, overlay) : null;
              if (id && mark && round?.roundId) {
                if (round.step === 2) {
                  toggleProposed(id);
                }
                return;
              }
              const node = id ? withOverlay.nodes.find((item) => item.id === id) : null;
              // Opening a note from here leaves the map in front and shows the note
              // beside it: the link between two notes is what the map is for.
              if (node?.path) go({ kind: "docs", path: node.path }, { placement: "beside" });
            }}
            onHover={hover}
          />
        ) : (
          <EmptyState
            icon={
              status === "error" ? (
                <AlertCircle className="size-8 text-destructive/40" />
              ) : status === "loading" ? undefined : (
                <Network className="size-8 text-muted-foreground/25" />
              )
            }
            title={
              status === "error"
                ? t("graph.view.failed")
                : status === "loading"
                  ? t("graph.view.loading")
                  : t("graph.view.empty")
            }
            body={status === "error" ? (error ?? t("graph.view.failedHint")) : t("graph.view.emptyHint")}
            action={
              status === "error" ? (
                <Button variant="outline" size="sm" onClick={() => void refreshGraph()}>
                  <RotateCcw />
                  {t("common.retry")}
                </Button>
              ) : undefined
            }
          />
        )}

        {ready ? (
          <>
            {/* The legend doubles as the filter: what a reader hides, they hide by
                clicking the thing they are looking at. */}
            <ul
              aria-label={t("graph.legend.label")}
              className="absolute top-2 left-2 flex flex-col gap-0.5 rounded-md border bg-background/85 p-1.5 text-[11px] backdrop-blur-sm"
            >
              {[
                ...kindsPresent(nodes),
                ...(ghostNodes(nodes).length > 0 ? ["ghost"] : []),
              ].map((kind) => {
                const off = hiddenKinds.includes(kind);
                return (
                  <li key={kind}>
                    <button
                      type="button"
                      aria-pressed={!off}
                      onClick={() => toggleKind(kind)}
                      className={cn(
                        "flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left",
                        off ? "text-muted-foreground opacity-60" : "hover:bg-accent/50",
                      )}
                    >
                      <span
                        className={cn(
                          "size-2 shrink-0 rounded-full",
                          kind === "ghost"
                            ? "border border-dashed border-muted-foreground"
                            : "bg-primary",
                          off && "bg-muted-foreground/40",
                        )}
                      />
                      {TYPE_LABEL[kind] ? t(TYPE_LABEL[kind]) : kind}
                    </button>
                  </li>
                );
              })}
              {/* What the round is proposing, listed apart from the page kinds: it is
                  not a kind of page, it is something about to happen to one. */}
              {legend.length > 0 ? (
                <li className="mt-0.5 border-t pt-0.5">
                  <ul className="flex flex-col gap-0.5">
                    {legend.map((item) => (
                      <li
                        key={item}
                        className="flex items-center gap-1.5 px-1 py-0.5 text-cite"
                      >
                        <span
                          className={cn("shrink-0", LEGEND_SWATCH[item])}
                          aria-hidden="true"
                        />
                        {t(`graph.overlay.${item}`)}
                      </li>
                    ))}
                  </ul>
                </li>
              ) : null}
              {hiddenKinds.length > 0 ? (
                <li>
                  <button
                    type="button"
                    onClick={showAllKinds}
                    className="w-full rounded px-1 py-0.5 text-left text-cite underline underline-offset-2"
                  >
                    {t("graph.legend.showAll")}
                  </button>
                </li>
              ) : null}
            </ul>

            <IconTip label={t("graph.resetView")}>
              <Button
                variant="outline"
                size="icon-sm"
                className="absolute top-2 right-2 bg-background/85 backdrop-blur-sm"
                onClick={resetView}
              >
                <Maximize2 />
              </Button>
            </IconTip>

            {selected ? (
              <div className="absolute bottom-2 left-1/2 flex max-w-[80%] -translate-x-1/2 items-center gap-2 rounded-md border bg-background/90 px-2.5 py-1.5 text-[11px] backdrop-blur-sm">
                <span className="min-w-0 truncate font-medium">{selected.title}</span>
                <span className="shrink-0 text-muted-foreground">
                  {t("graph.selected.degree", { count: selected.degree })}
                </span>
                {selected.ghost ? (
                  <span className="shrink-0 text-muted-foreground">
                    {t("graph.selected.ghost")}
                  </span>
                ) : null}
                {selectedMark ? (
                  <span className="shrink-0 text-cite">
                    {t(
                      selectedMark.accepted
                        ? `graph.overlay.${selectedMark.action}`
                        : "graph.overlay.dropped",
                    )}
                  </span>
                ) : null}
                <Button variant="ghost" size="sm" onClick={() => select(null)}>
                  {t("common.close")}
                </Button>
              </div>
            ) : null}
          </>
        ) : null}
        </div>

      <DeepSearchPanel slot={slot} />
    </div>
  );
}

