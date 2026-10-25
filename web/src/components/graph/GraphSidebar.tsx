import { useTranslation } from "react-i18next";
import { useGraphStore } from "@/stores/graph-store";
import { ghostNodes, realNodes } from "@/lib/wiki-graph";

/**
 * What the graph contains, so the sidebar is worth the room it takes.
 *
 * Counts only, for now: which pages there are and how they relate is a reading of
 * the graph, and the ways to narrow it down arrive with the rest of the graph's
 * controls.
 */
export function GraphSidebar() {
  const { t } = useTranslation();
  const nodes = useGraphStore((state) => state.nodes);
  const edges = useGraphStore((state) => state.edges);
  const status = useGraphStore((state) => state.status);

  if (status === "loading" && nodes.length === 0) {
    return (
      <p className="p-3 text-[11px] text-muted-foreground">{t("graph.sidebar.loading")}</p>
    );
  }

  const written = realNodes(nodes).length;
  const waiting = ghostNodes(nodes).length;

  return (
    <ul className="flex flex-col gap-1 p-3 text-[11px]">
      <li className="flex items-baseline gap-1.5">
        <span className="tabular-nums font-medium">{written}</span>
        <span className="text-muted-foreground">{t("graph.sidebar.pages")}</span>
      </li>
      <li className="flex items-baseline gap-1.5">
        <span className="tabular-nums font-medium">{edges.length}</span>
        <span className="text-muted-foreground">{t("graph.sidebar.links")}</span>
      </li>
      {waiting > 0 ? (
        <li className="flex items-baseline gap-1.5">
          <span className="tabular-nums font-medium">{waiting}</span>
          <span className="text-muted-foreground">{t("graph.sidebar.waiting")}</span>
        </li>
      ) : null}
    </ul>
  );
}
