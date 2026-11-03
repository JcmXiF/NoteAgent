import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { errorMessage } from "@/lib/api/client";
import { deleteFile } from "@/lib/api/files";
import { fetchPageBacklinks, type PageBacklinks } from "@/lib/api/pages";
import { titleOfPath } from "@/lib/project-tree";
import { isGeneratedWikiPage } from "@/lib/source-kind";
import { cn } from "@/lib/utils";
import { useClipsStore } from "@/stores/clips-store";
import { useDocsStore } from "@/stores/docs-store";

/**
 * The control that asks to delete one document.
 *
 * Kept quiet until the row is hovered or reached by keyboard, because deleting is
 * not what a list of documents is for — but it is always in the tab order, so it
 * is reachable without a pointer.
 */
export function RowDeleteButton({
  path,
  name,
  onDelete,
}: {
  path: string;
  name: string;
  onDelete: (path: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      title={t("docs.delete.row", { name })}
      aria-label={t("docs.delete.row", { name })}
      onClick={(event) => {
        event.stopPropagation();
        onDelete(path);
      }}
      className={cn(
        "shrink-0 rounded p-0.5 text-muted-foreground opacity-0 transition-opacity",
        "group-hover:opacity-100 focus-visible:opacity-100 hover:bg-destructive/15 hover:text-destructive",
      )}
    >
      <Trash2 className="size-3" />
    </button>
  );
}

/**
 * Confirms deleting one document and does it.
 *
 * It says what else goes with the file — how many notes cite it, and whether any
 * selections were folded away — because that is the part a reader cannot see from
 * the file list. The file itself is kept in the trash and can be restored for
 * thirty days, so the sentence says that rather than warning about a loss that can
 * be undone.
 */
export function DeleteFileDialog({
  project,
  path,
  onClose,
}: {
  project: string | null;
  /** The document to delete, or null when nothing is being asked about. */
  path: string | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dropped, setDropped] = useState<number | null>(null);
  const [backlinks, setBacklinks] = useState<PageBacklinks | null>(null);

  const citations = useDocsStore((state) => state.citations);
  const files = useDocsStore((state) => state.files);
  const refresh = useDocsStore((state) => state.refresh);

  const name = path ? titleOfPath(path, files) : "";
  const cited = path ? (citations?.sources[path]?.notes.length ?? 0) : 0;

  // A page is pointed at by other pages, which is the part of deleting one that
  // cannot be seen from the file list. Source citations answer a different
  // question, so a page is asked about separately.
  useEffect(() => {
    if (!project || !path) {
      setBacklinks(null);
      return undefined;
    }
    const isPage =
      path.startsWith("wiki/") && path.endsWith(".md") && !isGeneratedWikiPage(path);
    if (!isPage) {
      setBacklinks(null);
      return undefined;
    }
    let live = true;
    void (async () => {
      try {
        const found = await fetchPageBacklinks(project, path);
        if (live) setBacklinks(found);
      } catch {
        // Not knowing what points here should not stop the reader deleting it.
        if (live) setBacklinks(null);
      }
    })();
    return () => {
      live = false;
    };
  }, [project, path]);

  const submit = async () => {
    if (!project || !path) return;
    setBusy(true);
    setError(null);
    try {
      const result = await deleteFile(project, path);
      // The list, the citations and the open tabs all follow from the files that
      // are there, so they are read again rather than patched by hand.
      await refresh();
      // Deleting a stored source also drops the selections made from it, so the
      // selections are read again too: what is on screen must not outlive what is
      // on disk.
      await useClipsStore.getState().reload();
      if (result.droppedCollections > 0) {
        setDropped(result.droppedCollections);
        return;
      }
      onClose();
    } catch (err) {
      setError(errorMessage(err, t("docs.delete.failed")));
    } finally {
      setBusy(false);
    }
  };

  const close = () => {
    setDropped(null);
    setError(null);
    onClose();
  };

  return (
    <Dialog
      open={path !== null}
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("docs.delete.title", { name })}</DialogTitle>
          <DialogDescription>{t("docs.delete.body")}</DialogDescription>
        </DialogHeader>
        {cited > 0 ? (
          <p className="text-[11px] text-warning">
            {t("docs.delete.cited", { count: cited })}
          </p>
        ) : null}
        {backlinks && backlinks.count > 0 ? (
          <p className="text-[11px] text-warning">
            {t("docs.delete.backlinks", { count: backlinks.count })}
          </p>
        ) : null}
        {backlinks && backlinks.notes > 0 ? (
          <p className="text-[11px] text-muted-foreground">
            {t("docs.delete.notes", { count: backlinks.notes })}
          </p>
        ) : null}
        {dropped !== null ? (
          <p className="text-[11px] text-muted-foreground">
            {t("docs.delete.dropped", { count: dropped })}
          </p>
        ) : null}
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
        <DialogFooter>
          <Button variant="outline" onClick={close}>
            {dropped !== null ? t("common.close") : t("common.cancel")}
          </Button>
          {dropped === null ? (
            <Button variant="destructive" disabled={busy} onClick={() => void submit()}>
              {busy ? <Loader2 className="animate-spin" /> : <Trash2 />}
              {t("docs.delete.submit")}
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
