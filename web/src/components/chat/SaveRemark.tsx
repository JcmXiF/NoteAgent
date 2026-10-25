import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, NotebookPen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { errorMessage } from "@/lib/api/client";
import { titleOfPath } from "@/lib/project-tree";
import { useDocsStore } from "@/stores/docs-store";
import type { PaneSlot } from "@/stores/layout-store";
import { useNavStore } from "@/stores/nav-store";
import { useNotesStore } from "@/stores/notes-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * An answer, kept as a remark on the page the reader is in.
 *
 * The wiki is written by the agent and read by a person; a remark runs the other
 * way, and an answer worth keeping is the reader's to keep. This is one action
 * rather than a second writer: what was answered becomes a remark on the page in
 * front, quoting the question it came from, and nothing is written into a page.
 *
 * A remark belongs to a page, so with no page open there is nowhere for it to go.
 * The action stays in place and says why, rather than disappearing and leaving the
 * reader to wonder whether the answer could be kept at all.
 */
export function SaveRemark({
  answer,
  question,
  slot,
}: {
  answer: string;
  /** The question this answers, quoted in the remark so it reads on its own. */
  question: string | null;
  slot: PaneSlot;
}) {
  const { t } = useTranslation();
  const project = useWorkspaceStore((state) => state.current);
  const path = useDocsStore((state) => state.paths.main);
  const files = useDocsStore((state) => state.files);
  const create = useNotesStore((state) => state.create);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<{ id: string; page: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // A remark is written about a wiki page; a stored original is a different kind of
  // document even when it reads as markdown, and nothing can be noted about it.
  const page = path && path.startsWith("wiki/") ? path : null;
  const name = page ? titleOfPath(page, files) : "";

  const keep = async () => {
    if (!project || !page || busy) return;
    setBusy(true);
    setError(null);
    try {
      const note = await create(project, page, {
        body: answer,
        anchor: "",
        quote: question ?? "",
      });
      setSaved({ id: note.id, page: note.page });
    } catch (err) {
      setError(errorMessage(err, t("chat.remark.saveFailed")));
    } finally {
      setBusy(false);
    }
  };

  const open = () => {
    if (!saved) return;
    // Beside the conversation rather than over it: the answer being kept is still
    // worth reading while the remark is checked.
    useNavStore.getState().go(
      { kind: "notes", path: saved.page, noteId: saved.id },
      { placement: "beside", from: slot },
    );
  };

  return (
    <div className="mb-2 flex flex-wrap items-center gap-1.5">
      {saved ? (
        <>
          <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
            <Check className="size-3 text-success" />
            {t("chat.remark.saved")}
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="h-5 px-1.5 text-[10px]"
            onClick={open}
          >
            {t("chat.remark.open")}
          </Button>
        </>
      ) : (
        <Button
          variant="ghost"
          size="sm"
          className="h-5 px-1.5 text-[10px] text-muted-foreground"
          disabled={!page || busy}
          title={page ? t("chat.remark.hint", { page: name }) : t("chat.remark.needPage")}
          onClick={() => void keep()}
        >
          <NotebookPen className="size-3" />
          {busy ? t("chat.remark.saving") : t("chat.remark.save")}
        </Button>
      )}
      {error ? <span className="text-[10px] text-destructive">{error}</span> : null}
    </div>
  );
}
