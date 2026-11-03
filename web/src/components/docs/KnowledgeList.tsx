import { useTranslation } from "react-i18next";
import { ChevronRight, FileText } from "lucide-react";
import { RowDeleteButton } from "@/components/docs/DeleteFileDialog";
import { buildKnowledgeGroups, type KnowledgeGroup } from "@/lib/project-tree";
import { isGeneratedWikiPage } from "@/lib/source-kind";
import { cn } from "@/lib/utils";
import { useDocsStore } from "@/stores/docs-store";
import { useActivePaneSlot } from "@/stores/layout-store";

/**
 * What the project holds, listed by what each thing is.
 *
 * The counterpart to the file tree. There, the folders are the project's own and
 * say where a file lives; here, the headings are kinds — notes, the summaries of
 * sources, and the stored originals by what they are — so the two lists answer
 * different questions about the same files.
 */
export function KnowledgeList({ onDelete }: { onDelete: (path: string) => void }) {
  const { t } = useTranslation();
  const files = useDocsStore((state) => state.files);
  const groups = buildKnowledgeGroups(files);

  if (groups.length === 0) {
    return (
      <div className="flex flex-col gap-1 p-3">
        <p className="text-[11px] font-medium">{t("docs.knowledge.empty")}</p>
        <p className="text-[11px] text-muted-foreground">{t("docs.knowledge.emptyHint")}</p>
      </div>
    );
  }

  return (
    <ul className="flex flex-col pb-2 text-[11px]">
      {groups.map((group) => (
        <Group key={group.id} group={group} onDelete={onDelete} />
      ))}
    </ul>
  );
}

function Group({
  group,
  onDelete,
}: {
  group: KnowledgeGroup;
  onDelete: (path: string) => void;
}) {
  const { t } = useTranslation();
  const slot = useActivePaneSlot();
  const folded = useDocsStore((state) => state.foldedPaths.knowledge);
  const toggleFold = useDocsStore((state) => state.toggleFold);
  // The sidebar belongs to the focused pane, so its highlight and its clicks are
  // that pane's.
  const selected = useDocsStore((state) => state.paths[slot]);
  const openDocument = useDocsStore((state) => state.openDocument);

  const expanded = !folded.includes(group.id);

  return (
    <li>
      <button
        type="button"
        onClick={() => toggleFold("knowledge", group.id)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-1 py-1 pr-2 pl-2 text-left font-semibold tracking-wide text-muted-foreground uppercase"
      >
        <ChevronRight
          className={cn(
            "size-3 shrink-0 text-muted-foreground transition-transform",
            expanded ? "rotate-90" : "",
          )}
        />
        <span className="min-w-0 truncate">{t(group.labelKey)}</span>
        <span className="ml-auto shrink-0 tabular-nums">{group.files.length}</span>
      </button>
      {expanded ? (
        <ul>
          {group.files.map((file) => {
            const active = selected === file.path;
            return (
              <li key={file.path}>
                <div
                  className={cn(
                    "group flex w-full items-center gap-1 py-1 pr-2 pl-8",
                    active ? "bg-accent text-accent-foreground" : "hover:bg-accent/40",
                  )}
                >
                  <button
                    type="button"
                    title={file.path}
                    onClick={() => openDocument(file.path)}
                    className="flex min-w-0 flex-1 items-center gap-1 text-left"
                  >
                    <FileText className="size-3 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 truncate">{file.title || file.name}</span>
                  </button>
                  {isGeneratedWikiPage(file.path) ? null : (
                    <RowDeleteButton
                      path={file.path}
                      name={file.title || file.name}
                      onDelete={onDelete}
                    />
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}
    </li>
  );
}
