import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { History, Loader2, RotateCcw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
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
  fetchPageHistory,
  fetchPageVersion,
  restorePageVersion,
  type PageHistory,
  type PageVersion,
} from "@/lib/api/pages";
import { titleOfPath } from "@/lib/project-tree";
import { cn } from "@/lib/utils";
import { useDocsStore } from "@/stores/docs-store";

/** A version's moment, in the reader's own time; nothing when it was never stamped. */
function when(at: string): string {
  if (!at) return "";
  const moment = new Date(at);
  if (Number.isNaN(moment.getTime())) return at;
  return moment.toLocaleString();
}

/**
 * What a page has been, and how to put one of those back.
 *
 * Every write keeps the text it replaced — the pipeline's writes as well as a
 * person's — so this is a list of the page's own history rather than an undo
 * stack for one session. Restoring is itself a write: what it replaces is kept,
 * so going back is not a one-way door.
 */
export function PageHistoryDialog({
  project,
  path,
  onClose,
}: {
  project: string | null;
  path: string | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const files = useDocsStore((state) => state.files);
  const refresh = useDocsStore((state) => state.refresh);

  const [history, setHistory] = useState<PageHistory | null>(null);
  const [picked, setPicked] = useState<PageVersion | null>(null);
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pick = useCallback(
    async (version: PageVersion) => {
      if (!project || !path) return;
      setPicked(version);
      setContent("");
      try {
        const text = await fetchPageVersion(project, path, version.id);
        setContent(text.content);
      } catch (err) {
        setError(errorMessage(err, t("docs.page.history.readFailed")));
      }
    },
    [project, path, t],
  );

  useEffect(() => {
    if (!project || !path) return undefined;
    let live = true;
    setHistory(null);
    setPicked(null);
    setContent("");
    setError(null);
    void (async () => {
      try {
        const found = await fetchPageHistory(project, path);
        if (!live) return;
        setHistory(found);
        // The newest version is what the reader is looking at; showing it besides
        // the list means the dialog opens with something to read.
        if (found.versions[0]) void pick(found.versions[0]);
      } catch (err) {
        if (live) setError(errorMessage(err, t("docs.page.history.readFailed")));
      }
    })();
    return () => {
      live = false;
    };
  }, [project, path, t, pick]);

  const restore = async () => {
    if (!project || !path || !picked || !history) return;
    const current = history.versions.find((item) => item.current);
    if (!current) return;
    setBusy(true);
    setError(null);
    try {
      await restorePageVersion(project, {
        path,
        id: picked.id,
        baseRevision: current.id,
      });
      await refresh();
      onClose();
    } catch (err) {
      setError(errorMessage(err, t("docs.page.history.failed")));
    } finally {
      setBusy(false);
    }
  };

  const name = path ? titleOfPath(path, files) : "";

  return (
    <Dialog
      open={path !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t("docs.page.history.title", { name })}</DialogTitle>
          <DialogDescription>{t("docs.page.history.body")}</DialogDescription>
        </DialogHeader>

        <div className="grid min-h-[45vh] grid-cols-[minmax(0,15rem)_minmax(0,1fr)] gap-3">
          <div className="flex min-h-0 flex-col gap-1 overflow-y-auto scroll-thin">
            {history === null ? (
              <p className="flex items-center gap-1 p-2 text-[11px] text-muted-foreground">
                <Loader2 className="size-3 animate-spin" />
                {t("docs.page.history.loading")}
              </p>
            ) : history.versions.length === 0 ? (
              <p className="p-2 text-[11px] text-muted-foreground">
                {t("docs.page.history.empty")}
              </p>
            ) : (
              history.versions.map((version) => (
                <button
                  key={version.id}
                  type="button"
                  onClick={() => void pick(version)}
                  className={cn(
                    "flex flex-col gap-0.5 rounded-md border px-2 py-1 text-left",
                    picked?.id === version.id
                      ? "border-primary/40 bg-accent"
                      : "border-transparent hover:bg-accent/40",
                  )}
                >
                  <span className="flex items-center gap-1 text-[11px] font-medium">
                    <History className="size-3 shrink-0 text-muted-foreground" />
                    {when(version.at) || t("docs.page.history.unknownTime")}
                    {version.current ? (
                      <Badge tone="neutral">{t("docs.page.history.current")}</Badge>
                    ) : null}
                  </span>
                  <span className="text-[10px] text-muted-foreground">
                    {version.by === "person"
                      ? t("docs.page.history.byPerson")
                      : t("docs.page.history.byApp")}
                  </span>
                </button>
              ))
            )}
          </div>
          <pre className="max-h-[50vh] min-h-0 overflow-auto rounded-md border bg-muted/30 p-2 font-mono text-[11px] leading-relaxed">
            {content}
          </pre>
        </div>

        {error ? <p className="text-xs text-destructive">{error}</p> : null}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            {t("common.close")}
          </Button>
          <Button
            variant="outline"
            onClick={() => void restore()}
            disabled={busy || !picked || picked.current}
          >
            {busy ? <Loader2 className="animate-spin" /> : <RotateCcw />}
            {t("docs.page.history.restore")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
