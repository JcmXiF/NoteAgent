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
import type { SessionSummary } from "@/lib/api/types";

/**
 * Confirms deleting one session.
 *
 * Deleting the session in front also swaps in the next one, so this asks in
 * front of the list rather than acting from a row that is about to change. It
 * stays open when the delete did not happen — closing would read as "done" —
 * and the list behind it says why, with a way to read the list again.
 */
export function DeleteSessionDialog({
  session,
  onClose,
  onDelete,
}: {
  /** The session being asked about, or null when nothing is. */
  session: SessionSummary | null;
  onClose: () => void;
  /** Who removes it; a delete that fails leaves the dialog open. */
  onDelete: (sessionId: string) => Promise<boolean>;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  /**
   * The name to say while the dialog is closing.
   *
   * A dialog stays drawn for its closing animation, after the session it was
   * asking about has been let go of; reading the name from that would name the
   * unnamed session for a moment, which looks like a second ask.
   */
  const [name, setName] = useState("");

  useEffect(() => {
    if (session) setName(session.title);
  }, [session]);

  const close = () => {
    if (busy) return;
    setFailed(false);
    onClose();
  };

  const submit = async () => {
    if (!session) return;
    setBusy(true);
    setFailed(false);
    const done = await onDelete(session.id);
    setBusy(false);
    if (done) onClose();
    else setFailed(true);
  };

  return (
    <Dialog
      open={session !== null}
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {t("chat.delete.title", { title: session?.title || name || t("chat.untitled") })}
          </DialogTitle>
          <DialogDescription>{t("chat.delete.body")}</DialogDescription>
        </DialogHeader>
        {failed ? <p className="text-xs text-destructive">{t("chat.deleteFailed")}</p> : null}
        <DialogFooter>
          <Button variant="outline" onClick={close} disabled={busy}>
            {t("common.cancel")}
          </Button>
          <Button variant="destructive" disabled={busy} onClick={() => void submit()}>
            {busy ? <Loader2 className="animate-spin" /> : <Trash2 />}
            {t("chat.delete.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
