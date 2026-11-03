import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronRight, FileWarning, PanelRightOpen } from "lucide-react";
import { cn } from "@/lib/utils";
import type { CitationEntry } from "@/lib/wiki-citations";

/**
 * The originals a page was written from.
 *
 * Bringing a note's material into view is the point of the reading workspace, so
 * this sits with the note rather than behind a menu. A row opens the original in
 * this pane as another tab; the button beside it, and holding the modifier key,
 * open it in the pane next door so the two can be read together.
 *
 * Clicking the row is the whole row, not just its text, because the target is
 * small and the passage count is part of what it says.
 */
export function CitationList({
  entries,
  onOpen,
  onOpenBeside,
}: {
  entries: CitationEntry[];
  /** Opens the original here, at the passage named. */
  onOpen: (path: string, blockId: string | null) => void;
  /** Opens it in the pane beside this one. */
  onOpenBeside: (path: string, blockId: string | null) => void;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  if (entries.length === 0) return null;
  const missing = entries.filter((entry) => !entry.exists).length;

  return (
    <div className="shrink-0 border-b">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-1.5 px-3 py-1 text-left text-[11px] text-muted-foreground hover:bg-accent/40"
      >
        <ChevronRight
          className={cn("size-3 shrink-0 transition-transform", expanded ? "rotate-90" : "")}
        />
        <span>{t("docs.citations.summary", { count: entries.length })}</span>
        {missing > 0 ? (
          <span className="text-warning">{t("docs.citations.missing", { count: missing })}</span>
        ) : null}
        <span className="ml-auto shrink-0">{t("docs.citations.hintBeside")}</span>
      </button>

      {expanded ? (
        <ul className="flex flex-col pb-1">
          {entries.map((entry) => (
            <CitationRow
              key={entry.path}
              entry={entry}
              onOpen={onOpen}
              onOpenBeside={onOpenBeside}
            />
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function CitationRow({
  entry,
  onOpen,
  onOpenBeside,
}: {
  entry: CitationEntry;
  onOpen: (path: string, blockId: string | null) => void;
  onOpenBeside: (path: string, blockId: string | null) => void;
}) {
  const { t } = useTranslation();
  const first = entry.blockIds[0] ?? null;
  // A file the project no longer has cannot be opened, so nothing is offered.
  const open = (beside: boolean) => {
    if (!entry.exists) return;
    (beside ? onOpenBeside : onOpen)(entry.path, first);
  };

  return (
    <li className="flex items-center gap-1 px-2 py-0.5">
      <button
        type="button"
        title={entry.path}
        disabled={!entry.exists}
        onClick={(event) => open(event.ctrlKey || event.metaKey)}
        className={cn(
          "flex min-w-0 flex-1 items-baseline gap-1.5 rounded px-1.5 py-1 text-left text-[11px]",
          entry.exists ? "hover:bg-accent/40" : "cursor-default text-muted-foreground",
        )}
      >
        {entry.exists ? null : (
          <FileWarning className="size-3 shrink-0 self-center text-warning" aria-hidden />
        )}
        <span className="min-w-0 shrink-0 truncate font-medium">{entry.name}</span>
        {entry.from ? (
          <span className="min-w-0 truncate text-muted-foreground" title={entry.from}>
            {entry.from}
          </span>
        ) : null}
        {entry.blockIds.length > 0 ? (
          <span className="ml-auto shrink-0 tabular-nums text-muted-foreground">
            {t("docs.citations.passages", { count: entry.blockIds.length })}
          </span>
        ) : null}
        {entry.exists ? null : (
          <span className="ml-auto shrink-0 text-warning">{t("docs.citations.gone")}</span>
        )}
      </button>
      {entry.exists ? (
        <button
          type="button"
          aria-label={t("docs.citations.openBeside", { title: entry.name })}
          onClick={() => open(true)}
          className="shrink-0 rounded p-1 text-muted-foreground hover:bg-accent hover:text-accent-foreground"
        >
          <PanelRightOpen className="size-3.5" />
        </button>
      ) : null}
    </li>
  );
}
