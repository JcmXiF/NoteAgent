import { useTranslation } from "react-i18next";
import { FileText } from "lucide-react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import type { SourceCitationBundle } from "@/lib/api/types";

/**
 * How many notes were written from a stored source, and which ones.
 *
 * A source's worth is not visible from its file, so the count is what tells a
 * reader whether a page they are looking at has followers. The count opens the
 * list, and a note in it can be put beside the source, which is the reason to
 * know the names at all.
 */
export function CitationBadge({
  bundle,
  onOpenNote,
}: {
  bundle: SourceCitationBundle | undefined;
  /** Puts a note beside this source. */
  onOpenNote: (path: string) => void;
}) {
  const { t } = useTranslation();
  const notes = bundle?.notes ?? [];
  if (notes.length === 0) return null;

  return (
    <Popover>
      <PopoverTrigger
        aria-label={t("docs.citations.noteCount", { count: notes.length })}
        title={t("docs.citations.noteCount", { count: notes.length })}
        className="shrink-0 rounded px-1 text-[10px] tabular-nums bg-cite/15 text-cite hover:bg-cite/25"
        onClick={(event) => event.stopPropagation()}
      >
        {notes.length}
      </PopoverTrigger>
      <PopoverContent aria-label={t("docs.citations.noteList")} className="w-56">
        <ul className="flex flex-col">
          {notes.map((note) => (
            <li key={note.path}>
              <button
                type="button"
                title={note.path}
                onClick={() => onOpenNote(note.path)}
                className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-[11px] hover:bg-accent/50"
              >
                <FileText className="size-3 shrink-0 text-muted-foreground" />
                <span className="min-w-0 truncate">{note.title || note.path}</span>
              </button>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}
