import { Plus, StickyNote } from "lucide-react";
import { useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { placeLabel } from "@/components/notes/place-label";
import { useTargetPage } from "@/components/notes/use-target-page";
import { Button } from "@/components/ui/button";
import { IconTip } from "@/components/ui/tooltip";
import { orderedNotes, orphanNotes, pageName, remarkPages } from "@/lib/note-list";
import { titleOfPath } from "@/lib/project-tree";
import { cn } from "@/lib/utils";
import { useDocsStore } from "@/stores/docs-store";
import { useNotePageStore } from "@/stores/note-page-store";
import { useNotesStore } from "@/stores/notes-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * The remarks about the page being worked on.
 *
 * In the order the page reads, so a remark sits in the list where it sits in the
 * page; the ones the page no longer has a place for are kept at the bottom, said
 * to be unplaced rather than dropped. Picking the page comes first, because
 * everything else is about it.
 */
export function NotesSidebar() {
  const { t } = useTranslation();
  const project = useWorkspaceStore((state) => state.current);
  const files = useDocsStore((state) => state.files);
  const treeReady = useDocsStore((state) => state.status === "ready");

  const target = useNotePageStore((state) => state.target);
  const selectedId = useNotePageStore((state) => state.selectedId);
  const openFor = useNotePageStore((state) => state.openFor);
  const select = useNotePageStore((state) => state.select);
  const startNew = useNotePageStore((state) => state.startNew);

  const page = useTargetPage(target);
  const read = useNotesStore((state) => (target ? state.pages[target] : undefined));
  const load = useNotesStore((state) => state.read);
  const all = useNotesStore((state) => state.all);
  const loadAll = useNotesStore((state) => state.readAll);

  useEffect(() => {
    if (project && target) void load(project, target);
  }, [project, target, load]);

  // Every remark in the project is read for one reason only: finding the ones
  // whose page has gone. It is read once and kept, and a write leaves it in step.
  useEffect(() => {
    if (project) void loadAll(project);
  }, [project, loadAll]);

  const pages = useMemo(() => remarkPages(files), [files]);
  const notes = read?.notes ?? [];
  const listed = useMemo(
    () => orderedNotes(notes, page.body, page.split.sections),
    [notes, page.body, page.split],
  );
  // Only once the pages are known: before that every remark looks orphaned.
  const orphans = useMemo(
    () => (treeReady ? orphanNotes(all?.notes ?? [], files) : []),
    [treeReady, all, files],
  );
  // Which places a page has is known only from its text, so a page whose text
  // could not be read is not told which of its remarks have nowhere to go.
  const places = page.status === "ready";

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-1 px-2 pt-1.5 pb-1">
        <select
          className="h-7 min-w-0 flex-1 truncate rounded-lg border border-input bg-transparent px-2 text-xs outline-none focus-visible:border-ring"
          aria-label={t("remark.page.label")}
          value={target ?? ""}
          onChange={(event) => void openFor(event.target.value)}
        >
          <option value="">{t("remark.page.placeholder")}</option>
          {pages.map((file) => (
            <option key={file.path} value={file.path}>
              {pageName(file)}
            </option>
          ))}
          {/* The page being worked on is always offered, even when the project no
              longer lists it: a page can be renamed while its remarks are open. */}
          {target && !pages.some((file) => file.path === target) ? (
            <option value={target}>{titleOfPath(target, files)}</option>
          ) : null}
        </select>
        <IconTip label={t("remark.new")}>
          <Button
            variant="outline"
            size="icon-sm"
            disabled={!target}
            aria-label={t("remark.new")}
            onClick={() => void startNew()}
          >
            <Plus />
          </Button>
        </IconTip>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto scroll-thin">
        {!target ? (
          <Hint title={t("remark.pickPage.title")} body={t("remark.pickPage.body")} />
        ) : read?.status === "error" ? (
          <p className="p-3 text-[11px] text-destructive">
            {read.error ?? t("remark.loadFailed")}
          </p>
        ) : read?.status === "loading" && notes.length === 0 ? (
          <p className="p-3 text-[11px] text-muted-foreground">{t("remark.loading")}</p>
        ) : listed.length === 0 ? (
          <Hint title={t("remark.empty.title")} body={t("remark.empty.body")} />
        ) : (
          <ul className="flex flex-col gap-px py-1">
            {listed.map(({ note, place }) => (
              <li key={note.id}>
                <Row
                  body={note.body}
                  label={places ? placeLabel(place, t) : ""}
                  active={note.id === selectedId}
                  onSelect={() => void select(note.id)}
                />
              </li>
            ))}
          </ul>
        )}

        {orphans.length > 0 ? (
          <div className="border-t">
            <p className="px-2 pt-2 pb-0.5 text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
              {t("remark.orphans.title", { count: orphans.length })}
            </p>
            <p className="px-2 pb-1 text-[11px] text-muted-foreground/80">
              {t("remark.orphans.body")}
            </p>
            <ul className="flex flex-col gap-px pb-1">
              {orphans.map((note) => (
                <li key={note.id}>
                  <Row
                    body={note.body}
                    /* The page it was written about is gone, so what that page was
                       called is the only thing left to say about where it belongs. */
                    label={titleOfPath(note.page, files)}
                    active={note.id === selectedId}
                    onSelect={() => void select(note.id)}
                  />
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** One remark in the list: where it sits, and the first of what it says. */
function Row({
  body,
  label,
  active,
  onSelect,
}: {
  body: string;
  label: string;
  active: boolean;
  onSelect: () => void;
}) {
  const { t } = useTranslation();
  const first = body.split("\n").find((line) => line.trim()) ?? "";
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={active ? "true" : undefined}
      className={cn(
        "flex w-full flex-col gap-0.5 px-2 py-1.5 text-left text-[11px]",
        active ? "bg-accent" : "hover:bg-accent/40",
      )}
    >
      <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
        <StickyNote className="size-3 shrink-0" />
        <span className="truncate">{label}</span>
      </span>
      <span className="line-clamp-2">{first || t("remark.emptyDraft")}</span>
    </button>
  );
}

function Hint({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex flex-col gap-1 p-3">
      <p className="text-[11px] font-medium">{title}</p>
      <p className="text-[11px] text-muted-foreground">{body}</p>
    </div>
  );
}
