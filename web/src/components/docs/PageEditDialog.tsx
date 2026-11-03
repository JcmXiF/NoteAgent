import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Eye, Loader2, Lock, Save } from "lucide-react";
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
import {
  fetchPageEdit,
  savePageBody,
  type PageEditView,
  type PageSaveResult,
} from "@/lib/api/pages";
import { titleOfPath } from "@/lib/project-tree";
import { useDocsStore } from "@/stores/docs-store";

/**
 * Editing a page's prose, with the change shown before it is made.
 *
 * The frontmatter is read-only here for the same reason the core keeps it: it
 * carries the sources a page was written from and the markers a citation resolves
 * through, and a person fixing a sentence should not have to keep YAML valid.
 * Saving is two steps — ask what would change, then confirm — because the page is
 * shared with whatever the pipeline writes next.
 */
export function PageEditDialog({
  project,
  path,
  onClose,
}: {
  project: string | null;
  /** The page being edited, or null when nothing is being edited. */
  path: string | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const files = useDocsStore((state) => state.files);
  const refresh = useDocsStore((state) => state.refresh);

  const [view, setView] = useState<PageEditView | null>(null);
  const [body, setBody] = useState("");
  const [preview, setPreview] = useState<PageSaveResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!project || !path) return undefined;
    let live = true;
    setView(null);
    setBody("");
    setPreview(null);
    setError(null);
    setSaved(false);
    void (async () => {
      try {
        const opened = await fetchPageEdit(project, path);
        if (!live) return;
        setView(opened);
        setBody(opened.body);
      } catch (err) {
        if (live) setError(errorMessage(err, t("docs.page.edit.loadFailed")));
      }
    })();
    return () => {
      live = false;
    };
  }, [project, path, t]);

  const dirty = view !== null && body !== view.body;

  const check = async (): Promise<PageSaveResult | null> => {
    if (!project || !path || !view) return null;
    setBusy(true);
    setError(null);
    try {
      const result = await savePageBody(project, {
        path,
        body,
        baseRevision: view.revision,
        preview: true,
      });
      setPreview(result);
      return result;
    } catch (err) {
      setError(errorMessage(err, t("docs.page.edit.failed")));
      return null;
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!project || !path || !view) return;
    // The first press says what would change; the second makes it so.
    if (preview === null) {
      await check();
      return;
    }
    if (!preview.changed) {
      setSaved(true);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await savePageBody(project, {
        path,
        body,
        baseRevision: view.revision,
      });
      await refresh();
      onClose();
    } catch (err) {
      setError(errorMessage(err, t("docs.page.edit.failed")));
      // The page moved under the editor: what was open is no longer what is there.
      setPreview(null);
    } finally {
      setBusy(false);
    }
  };

  const close = () => {
    setView(null);
    setPreview(null);
    setError(null);
    setSaved(false);
    onClose();
  };

  const name = path ? titleOfPath(path, files) : "";

  return (
    <Dialog
      open={path !== null}
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t("docs.page.edit.title", { name })}</DialogTitle>
          <DialogDescription>{t("docs.page.edit.body")}</DialogDescription>
        </DialogHeader>

        {view ? (
          <details className="rounded-md border bg-muted/40 px-3 py-2 text-[11px]">
            <summary className="flex cursor-pointer items-center gap-1 font-medium text-muted-foreground">
              <Lock className="size-3" />
              {t("docs.page.edit.frontmatter")}
            </summary>
            <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">
              {view.frontmatter}
            </pre>
          </details>
        ) : null}

        <textarea
          value={body}
          onChange={(event) => {
            setBody(event.target.value);
            // A preview belongs to the text it was made for.
            setPreview(null);
            setSaved(false);
          }}
          disabled={!view || busy}
          spellCheck={false}
          aria-label={t("docs.page.edit.placeholder")}
          placeholder={t("docs.page.edit.placeholder")}
          className="min-h-[45vh] w-full resize-y rounded-md border bg-background px-3 py-2 font-mono text-[13px] leading-relaxed outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
        />

        {preview ? (
          <div className="flex flex-col gap-1">
            <p className="text-[11px] font-medium">
              {preview.changed
                ? t("docs.page.edit.diffTitle", {
                    added: preview.added,
                    removed: preview.removed,
                  })
                : t("docs.page.edit.unchanged")}
            </p>
            {preview.changed ? (
              <pre className="max-h-48 overflow-auto rounded-md border bg-muted/30 p-2 font-mono text-[11px] leading-relaxed">
                {preview.diff}
              </pre>
            ) : null}
          </div>
        ) : null}

        {error ? <p className="text-xs text-destructive">{error}</p> : null}
        {saved ? (
          <p className="text-xs text-muted-foreground">{t("docs.page.edit.saved")}</p>
        ) : null}

        <DialogFooter>
          <Button variant="outline" onClick={close} disabled={busy}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="outline"
            onClick={() => void check()}
            disabled={!view || !dirty || busy}
          >
            {busy ? <Loader2 className="animate-spin" /> : <Eye />}
            {t("docs.page.edit.check")}
          </Button>
          <Button onClick={() => void save()} disabled={!view || !dirty || busy}>
            {busy ? <Loader2 className="animate-spin" /> : <Save />}
            {preview?.changed ? t("docs.page.edit.confirm") : t("docs.page.edit.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
