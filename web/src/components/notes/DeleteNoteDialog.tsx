import { useState } from "react";
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
import type { Note } from "@/lib/api/notes";
import { useNotePageStore } from "@/stores/note-page-store";
import { useNotesStore } from "@/stores/notes-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * Removing one remark, asked about once.
 *
 * Asked about because there is nothing to undo: a remark is not a document anyone
 * keeps a copy of, and the page it was written about will not bring it back.
 * Removing it leaves the screen on the page rather than on the remark, which is
 * where the reader was when they decided it should go.
 */
export function DeleteNoteDialog({ note }: { note: Note }) {
  const { t } = useTranslation();
  const project = useWorkspaceStore((state) => state.current);
  const remove = useNotesStore((state) => state.remove);
  const select = useNotePageStore((state) => state.select);

  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const close = () => {
    setOpen(false);
    setError(null);
  };

  const submit = async () => {
    if (!project) return;
    setBusy(true);
    setError(null);
    try {
      await remove(project, note.page, note.id);
      await select(null);
      close();
    } catch (err) {
      setError(errorMessage(err, t("remark.deleteFailed")));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button variant="ghost" size="sm" onClick={() => setOpen(true)}>
        <Trash2 /> {t("remark.delete")}
      </Button>
      <Dialog open={open} onOpenChange={(next) => (next ? setOpen(true) : close())}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("remark.deleteTitle")}</DialogTitle>
            <DialogDescription>{t("remark.deleteBody")}</DialogDescription>
          </DialogHeader>
          {error ? <p className="text-xs text-destructive">{error}</p> : null}
          <DialogFooter>
            <Button variant="outline" onClick={close}>
              {t("common.cancel")}
            </Button>
            <Button variant="destructive" disabled={busy} onClick={() => void submit()}>
              {busy ? <Loader2 className="animate-spin" /> : <Trash2 />}
              {t("remark.deleteSubmit")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
