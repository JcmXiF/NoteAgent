import { useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { FileTree } from "@/components/docs/FileTree";
import { KnowledgeList } from "@/components/docs/KnowledgeList";
import { DeleteFileDialog } from "@/components/docs/DeleteFileDialog";
import { DocsMaintenance } from "@/components/docs/DocsMaintenance";
import { cn } from "@/lib/utils";
import { useDocsStore, type SidebarView } from "@/stores/docs-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/** Labels are looked up through constants so the check can see the keys. */
const VIEW_LABEL: Record<SidebarView, string> = {
  files: "docs.tree.view.files",
  knowledge: "docs.tree.view.knowledge",
};

const VIEWS: SidebarView[] = ["files", "knowledge"];

/**
 * The documents sidebar: every file, or just the notes, and what can be done to the
 * project as a whole.
 *
 * The two lists answer different questions — what is in the project, and what has
 * been written — so they are switched between rather than stacked, and each keeps
 * its own folded groups and its own place in the list. Deleting one document is
 * asked about once, here, rather than by a dialog per row.
 */
export function DocsSidebar() {
  const { t } = useTranslation();
  const project = useWorkspaceStore((state) => state.current);
  const tree = useDocsStore((state) => state.tree);
  const status = useDocsStore((state) => state.status);
  const error = useDocsStore((state) => state.error);
  const view = useDocsStore((state) => state.sidebarView);
  const setSidebarView = useDocsStore((state) => state.setSidebarView);
  const refresh = useDocsStore((state) => state.refresh);

  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const scrollerRef = useRef<HTMLDivElement>(null);
  // Where each list was left. Kept in a ref because it is read and written by
  // the switch itself rather than drawn.
  const scrolledTo = useRef<Record<SidebarView, number>>({ files: 0, knowledge: 0 });

  // Restores the list being switched to; without this the browser would keep the
  // other list's offset, or clamp it away when the shorter list is shown.
  useLayoutEffect(() => {
    const node = scrollerRef.current;
    if (node) node.scrollTop = scrolledTo.current[view];
  }, [view]);

  const switchTo = (next: SidebarView) => {
    const node = scrollerRef.current;
    if (node) scrolledTo.current[view] = node.scrollTop;
    setSidebarView(next);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 px-2 pt-1.5 pb-1">
        <div
          role="group"
          aria-label={t("docs.tree.view.label")}
          className="flex gap-0.5 rounded-md bg-muted/60 p-0.5"
        >
          {VIEWS.map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={view === option}
              onClick={() => switchTo(option)}
              className={cn(
                "min-w-0 flex-1 truncate rounded px-2 py-0.5 text-[11px]",
                view === option
                  ? "bg-background font-medium text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t(VIEW_LABEL[option])}
            </button>
          ))}
        </div>
      </div>

      {/* The list scrolls, so a long one cannot spill over the jobs panel below,
          and so the parts above and below it stay put. */}
      <div ref={scrollerRef} className="min-h-0 flex-1 overflow-y-auto scroll-thin">
        {status === "loading" && tree.length === 0 ? (
          <p className="p-3 text-[11px] text-muted-foreground">{t("docs.tree.loading")}</p>
        ) : status === "error" && tree.length === 0 ? (
          <div className="flex flex-col gap-1 p-3">
            <p className="text-[11px] text-destructive">{error ?? t("docs.tree.failed")}</p>
            <p className="text-[11px] text-muted-foreground">{t("docs.tree.failedHint")}</p>
            <Button
              variant="outline"
              size="sm"
              className="mt-1 self-start"
              onClick={() => void refresh()}
            >
              <RotateCcw />
              {t("common.retry")}
            </Button>
          </div>
        ) : tree.length === 0 ? (
          <div className="flex flex-col gap-1 p-3">
            <p className="text-[11px] font-medium">{t("docs.tree.empty")}</p>
            <p className="text-[11px] text-muted-foreground">{t("docs.tree.emptyHint")}</p>
          </div>
        ) : view === "files" ? (
          <FileTree onDelete={setPendingDelete} />
        ) : (
          <KnowledgeList onDelete={setPendingDelete} />
        )}
      </div>

      <DocsMaintenance />
      <DeleteFileDialog
        project={project}
        path={pendingDelete}
        onClose={() => setPendingDelete(null)}
      />
    </div>
  );
}
