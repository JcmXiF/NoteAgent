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
import type { ChatSummary } from "@/lib/api/types";
import { useChatStore } from "@/stores/chat-store";

/**
 * Confirms deleting one conversation.
 *
 * Deleting the conversation in front also swaps in the next one, so this asks in
 * front of the list rather than acting from a row that is about to change. It stays
 * open when the delete did not happen — closing would read as "done" — and the list
 * behind it says why, with a way to read the list again.
 */
export function DeleteChatDialog({
  conversation,
  onClose,
}: {
  /** The conversation being asked about, or null when nothing is. */
  conversation: ChatSummary | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const remove = useChatStore((state) => state.remove);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  /**
   * The name to say while the dialog is closing.
   *
   * A dialog stays drawn for its closing animation, after the conversation it was
   * asking about has been let go of; reading the name from that would name the
   * unnamed conversation for a moment, which looks like a second ask.
   */
  const [name, setName] = useState("");

  useEffect(() => {
    if (conversation) setName(conversation.title);
  }, [conversation]);

  const close = () => {
    if (busy) return;
    setFailed(false);
    onClose();
  };

  const submit = async () => {
    if (!conversation) return;
    setBusy(true);
    setFailed(false);
    const done = await remove(conversation.id);
    setBusy(false);
    if (done) onClose();
    else setFailed(true);
  };

  return (
    <Dialog
      open={conversation !== null}
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {t("chat.delete.title", { title: conversation?.title || name || t("chat.untitled") })}
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
