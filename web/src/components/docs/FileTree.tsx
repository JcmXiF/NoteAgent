import { useTranslation } from "react-i18next";
import { ChevronRight, FileText, Folder, FolderOpen } from "lucide-react";
import { CitationBadge } from "@/components/docs/CitationBadge";
import { RowDeleteButton } from "@/components/docs/DeleteFileDialog";
import { isGeneratedWikiPage } from "@/lib/source-kind";
import { cn } from "@/lib/utils";
import type { TreeNode } from "@/lib/project-tree";
import { useDocsStore } from "@/stores/docs-store";
import { useActivePaneSlot } from "@/stores/layout-store";

/**
 * The project's documents, in the shape the project has on disk.
 *
 * Two sections, the notes and the stored originals, and below them whatever
 * folders the project actually uses — including the site folders sources were
 * filed under. What a file is belongs to the other list.
 */
export function FileTree({ onDelete }: { onDelete: (path: string) => void }) {
  const tree = useDocsStore((state) => state.tree);

  return (
    <ul className="flex flex-col pb-2 text-[11px]">
      {tree.map((node) => (
        <TreeBranch key={node.name} node={node} depth={0} onDelete={onDelete} />
      ))}
    </ul>
  );
}

/** Sections are named by the app, so they are translated; folders are not. */
function labelOf(node: TreeNode, translate: (key: string) => string): string {
  if (node.kind === "group") return translate(`docs.group.${node.group ?? node.name}`);
  return node.title;
}

function TreeBranch({
  node,
  depth,
  onDelete,
}: {
  node: TreeNode;
  depth: number;
  onDelete: (path: string) => void;
}) {
  const { t } = useTranslation();
  const slot = useActivePaneSlot();
  const foldedPaths = useDocsStore((state) => state.foldedPaths.files);
  const toggleFold = useDocsStore((state) => state.toggleFold);
  // The sidebar belongs to the focused pane, so its highlight and its clicks are
  // that pane's.
  const selected = useDocsStore((state) => state.paths[slot]);
  const openDocument = useDocsStore((state) => state.openDocument);
  const followDocument = useDocsStore((state) => state.followDocument);
  const citations = useDocsStore((state) => state.citations);

  const key = node.path ?? `group:${node.name}`;
  // Sections are always shown; folders are open unless the user closed them.
  const openFolder = node.kind === "group" ? true : !foldedPaths.includes(key);
  const isFile = node.kind === "file";
  const active = isFile && selected === node.file?.path;
  const path = node.file?.path ?? "";

  return (
    <li>
      <div
        className={cn(
          "group flex w-full items-center gap-1 py-1 pr-2",
          node.kind === "group" ? "font-semibold tracking-wide text-muted-foreground uppercase" : "",
          active ? "bg-accent text-accent-foreground" : "hover:bg-accent/40",
        )}
        style={{ paddingLeft: `${8 + depth * 10}px` }}
      >
        <button
          type="button"
          title={node.file?.path}
          onClick={() => {
            if (isFile) openDocument(path);
            else if (node.kind === "folder") toggleFold("files", key);
          }}
          aria-expanded={isFile ? undefined : openFolder}
          className="flex min-w-0 flex-1 items-center gap-1 text-left"
        >
          {isFile ? (
            <FileText className="size-3 shrink-0 text-muted-foreground" />
          ) : node.kind === "folder" ? (
            <ChevronRight
              className={cn(
                "size-3 shrink-0 text-muted-foreground transition-transform",
                openFolder ? "rotate-90" : "",
              )}
            />
          ) : null}
          {node.kind === "folder" ? (
            openFolder ? (
              <FolderOpen className="size-3 shrink-0 text-muted-foreground" />
            ) : (
              <Folder className="size-3 shrink-0 text-muted-foreground" />
            )
          ) : null}
          <span className="min-w-0 truncate">{isFile ? node.title : labelOf(node, t)}</span>
        </button>
        {isFile ? (
          <CitationBadge
            bundle={citations?.sources[path]}
            // The sidebar belongs to the focused pane, so following from here
            // opens beside what that pane is reading.
            onOpenNote={(notePath) => followDocument(notePath, null, slot)}
          />
        ) : null}
        {isFile && !isGeneratedWikiPage(path) ? (
          <RowDeleteButton path={path} name={node.title} onDelete={onDelete} />
        ) : null}
      </div>
      {openFolder && node.children.length > 0 ? (
        <ul>
          {node.children.map((child) => (
            <TreeBranch
              key={child.path ?? `${key}/${child.name}`}
              node={child}
              depth={depth + 1}
              onDelete={onDelete}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}
