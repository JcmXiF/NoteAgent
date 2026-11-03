import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, MoveRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { errorMessage } from "@/lib/api/client";
import { renamePage } from "@/lib/api/pages";
import type { PaneSlot } from "@/stores/layout-store";
import { useDocsStore } from "@/stores/docs-store";

/** `wiki/concepts/kl.md` → `concepts/kl`, which is what a person names a page. */
function editableName(path: string): string {
  const clean = path.replace(/\\/g, "/");
  const withoutFolder = clean.startsWith("wiki/") ? clean.slice(5) : clean;
  return withoutFolder.replace(/\.md$/i, "");
}

/**
 * Renaming or moving a page.
 *
 * A page's name is written into every link that points at it, so this is not a
 * file rename: the core moves the page, rewrites the links, moves the remarks
 * about it, and rebuilds the index. What the dialog asks for is the name the
 * reader wants, not the plumbing that makes it true.
 */
export function RenamePageDialog({
  project,
  path,
  slot,
  onClose,
}: {
  project: string | null;
  path: string | null;
  slot: PaneSlot;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const refresh = useDocsStore((state) => state.refresh);
  const setPanePath = useDocsStore((state) => state.setPanePath);

  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!path) return;
    setName(editableName(path));
    setError(null);
  }, [path]);

  const submit = async () => {
    if (!project || !path) return;
    const wanted = name.trim();
    if (!wanted) return;
    setBusy(true);
    setError(null);
    try {
      const result = await renamePage(project, path, wanted);
      await refresh();
      // The pane was reading the old name, which no longer exists; it follows the
      // page to where it now sits rather than being left empty.
      setPanePath(slot, result.path);
      onClose();
    } catch (err) {
      setError(errorMessage(err, t("docs.page.rename.failed")));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={path !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("docs.page.rename.title")}</DialogTitle>
          <DialogDescription>{t("docs.page.rename.body")}</DialogDescription>
        </DialogHeader>
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void submit();
          }}
          aria-label={t("docs.page.rename.label")}
          placeholder={t("docs.page.rename.placeholder")}
          autoFocus
        />
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </Button>
          <Button onClick={() => void submit()} disabled={busy || !name.trim()}>
            {busy ? <Loader2 className="animate-spin" /> : <MoveRight />}
            {t("docs.page.rename.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
