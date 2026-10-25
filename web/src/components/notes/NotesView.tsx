import { lazy, Suspense, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Check, Loader2, Pencil, Plus, StickyNote } from "lucide-react";
import { DeleteNoteDialog } from "@/components/notes/DeleteNoteDialog";
import { NotePreview } from "@/components/notes/NotePreview";
import { PlacementPicker } from "@/components/notes/PlacementPicker";
import { placeLabel } from "@/components/notes/place-label";
import { useTargetPage } from "@/components/notes/use-target-page";
import { Button } from "@/components/ui/button";
import { orderedNotes } from "@/lib/note-list";
import type { PaneSlot } from "@/stores/layout-store";
import { useNavStore } from "@/stores/nav-store";
import { useNotePageStore } from "@/stores/note-page-store";
import { useNotesStore } from "@/stores/notes-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * The editor carries the app's second-largest dependency, and it is only needed
 * while something is being written, so it is fetched when a remark is opened
 * rather than with the app.
 */
const NoteEditor = lazy(() =>
  import("@/components/notes/NoteEditor").then((mod) => ({ default: mod.NoteEditor })),
);

/**
 * How long a writer is left alone before what they wrote is kept.
 *
 * Long enough that a sentence is not written a word at a time, short enough that
 * walking away from the machine loses nothing.
 */
const AUTOSAVE_MS = 1000;

/**
 * One remark, read or written.
 *
 * Reading and writing are the same screen, because they are the same remark: what
 * is being read becomes what is being written in place, and what was written comes
 * back in the shape it will be read in. A remark's links lead to the page they
 * name, beside this screen, so following one does not take the remark away.
 *
 * Nothing here decides what writing means — an empty remark, a changed one, one
 * that was already there. That is one rule, and it lives with the draft; what this
 * screen does is put the writer in front of it and say how it stands.
 */
export function NotesView({ slot }: { slot: PaneSlot }) {
  const { t } = useTranslation();
  const project = useWorkspaceStore((state) => state.current);

  const target = useNotePageStore((state) => state.target);
  const selectedId = useNotePageStore((state) => state.selectedId);
  const draft = useNotePageStore((state) => state.draft);
  const error = useNotePageStore((state) => state.error);
  const openFor = useNotePageStore((state) => state.openFor);
  const startNew = useNotePageStore((state) => state.startNew);
  const startEdit = useNotePageStore((state) => state.startEdit);
  const clearError = useNotePageStore((state) => state.clearError);

  const page = useTargetPage(target);
  const read = useNotesStore((state) => (target ? state.pages[target] : undefined));
  const load = useNotesStore((state) => state.read);

  useEffect(() => {
    if (project && target) void load(project, target);
  }, [project, target, load]);

  // Another view asking for a page's remarks, or for one of them.
  const navSeq = useNavStore((state) => state.seq);
  useEffect(() => {
    const intent = useNavStore.getState().intent;
    if (!intent || intent.kind !== "notes") return;
    void openFor(intent.path, intent.noteId ?? null);
    // Keyed on the request: the intent is read as it arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navSeq]);

  const note = (read?.notes ?? []).find((item) => item.id === selectedId) ?? null;

  if (!target) {
    return (
      <Empty
        icon={<StickyNote className="size-8 text-muted-foreground/25" />}
        title={t("remark.pickPage.title")}
        body={t("remark.pickPage.body")}
      />
    );
  }

  if (draft) return <Writer body={page.body} slot={slot} />;

  if (!note) {
    return (
      <Empty
        icon={<StickyNote className="size-8 text-muted-foreground/25" />}
        title={t("remark.pick.title")}
        body={t("remark.pick.body")}
        action={
          <Button variant="outline" size="sm" onClick={() => void startNew()}>
            <Plus /> {t("remark.new")}
          </Button>
        }
      />
    );
  }

  const place = orderedNotes([note], page.body, page.split.sections)[0]?.place ?? null;

  return (
    <article className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center gap-2 border-b px-3 py-1.5">
        <span className="truncate text-[11px] text-muted-foreground">
          {place && page.status === "ready"
            ? placeLabel(place, t)
            : t("remark.place.unknown")}
        </span>
        <div className="ml-auto flex shrink-0 items-center gap-0.5">
          <Button variant="ghost" size="sm" onClick={() => void startEdit(note)}>
            <Pencil /> {t("remark.edit")}
          </Button>
          <DeleteNoteDialog note={note} />
        </div>
      </header>
      {error ? (
        <button
          type="button"
          className="shrink-0 border-b bg-muted/50 px-3 py-1.5 text-left text-[11px] text-destructive"
          onClick={clearError}
        >
          {error}
        </button>
      ) : null}
      <div className="min-h-0 flex-1 overflow-y-auto scroll-thin px-3 py-2">
        <NotePreview body={note.body} slot={slot} />
      </div>
    </article>
  );
}

/**
 * The remark being written, beside itself as it will read.
 *
 * Both halves are always there: writing markdown without seeing what it becomes
 * means guessing, and the point of a remark is that it says what its author meant.
 * What is written stays where it is — this is the same remark, half written and
 * half read — so nothing has to be switched to see the other.
 */
function Writer({ body, slot }: { body: string; slot: PaneSlot }) {
  const { t } = useTranslation();
  const draft = useNotePageStore((state) => state.draft);
  const saving = useNotePageStore((state) => state.saving);
  const error = useNotePageStore((state) => state.error);
  const setBody = useNotePageStore((state) => state.setBody);
  const setPlace = useNotePageStore((state) => state.setPlace);
  const save = useNotePageStore((state) => state.save);
  const finish = useNotePageStore((state) => state.finish);
  const discard = useNotePageStore((state) => state.discard);

  // Keeping what is written while it is still being written: a pause writes it,
  // and looking away writes it. Neither removes it — that waits until the writer
  // has actually left, because a pause can be the middle of replacing every word.
  const text = draft?.body ?? "";
  const place = `${draft?.anchor ?? ""}\u0000${draft?.quote ?? ""}`;
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void save(), AUTOSAVE_MS);
    return () => window.clearTimeout(timer.current);
  }, [text, place, save]);

  if (!draft) return null;

  return (
    <section className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center gap-2 border-b px-3 py-1.5">
        <span className="truncate text-[11px] text-muted-foreground">
          {t("remark.writing")}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground">
          {saving ? (
            <>
              <Loader2 className="size-3 animate-spin" />
              {t("remark.saving")}
            </>
          ) : null}
          <Button variant="ghost" size="sm" onClick={discard}>
            {t("remark.cancel")}
          </Button>
          <Button variant="outline" size="sm" onClick={() => void finish()}>
            <Check /> {t("remark.done")}
          </Button>
        </span>
      </header>

      {error ? (
        <p className="shrink-0 border-b bg-muted/50 px-3 py-1.5 text-[11px] text-destructive">
          {error}
        </p>
      ) : null}

      <div className="grid min-h-0 flex-1 grid-cols-2 divide-x divide-border">
        <div className="flex min-h-0 flex-col">
          <Suspense
            fallback={
              <div className="flex min-h-32 flex-1 items-center justify-center">
                <Loader2 className="size-4 animate-spin text-muted-foreground" />
              </div>
            }
          >
            <NoteEditor
              className="min-h-32 flex-1"
              value={text}
              label={t("remark.bodyLabel")}
              placeholder={t("remark.bodyPlaceholder")}
              autoFocus
              onChange={setBody}
              onBlur={() => void save()}
            />
          </Suspense>
          <div className="max-h-[50%] shrink-0 overflow-y-auto scroll-thin border-t p-2">
            <PlacementPicker
              body={body}
              anchor={draft.anchor}
              onPick={(chosen) => setPlace(chosen)}
            />
            <p className="mt-2 text-[11px] text-muted-foreground">{t("remark.autosave")}</p>
          </div>
        </div>
        <div className="min-h-0 overflow-y-auto scroll-thin px-3 py-2">
          <NotePreview body={text} slot={slot} />
        </div>
      </div>
    </section>
  );
}

function Empty({
  icon,
  title,
  body,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      {icon}
      <p className="text-sm font-medium">{title}</p>
      <p className="max-w-sm text-xs text-muted-foreground">{body}</p>
      {action}
    </div>
  );
}
