import { useEffect, useState } from "react";
import {
  ArchiveRestore,
  Download,
  FolderOpen,
  FolderPlus,
  Loader2,
  RotateCcw,
  Trash2,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input, inputClass } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { IconTip } from "@/components/ui/tooltip";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { errorMessage } from "@/lib/api/client";
import { fetchProjectStats, type ProjectStats } from "@/lib/api/projects";
import { fetchTrash, restoreTrash, type TrashItem } from "@/lib/api/trash";
import { downloadProjectArchive } from "@/lib/project-export";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { cn } from "@/lib/utils";

/** When something was removed, as the reader's own locale writes dates. */
function whenLabel(raw: string): string {
  const at = new Date(raw);
  return Number.isNaN(at.getTime()) ? raw : at.toLocaleString();
}

/**
 * Project picker. Switching projects changes which wiki every other view reads
 * from, so the two dialogs that create and remove them live together here.
 *
 * Making one and opening a folder are asked for from the workspace as well, when
 * there is no project yet; the dialogs themselves are mounted by the shell for
 * that reason, and this bar only says which one it wants.
 */
export function ProjectBar() {
  const { t } = useTranslation();
  const projects = useWorkspaceStore((state) => state.projects);
  const current = useWorkspaceStore((state) => state.current);
  const loading = useWorkspaceStore((state) => state.loading);
  const busy = useWorkspaceStore((state) => state.busy);
  const error = useWorkspaceStore((state) => state.error);
  const refresh = useWorkspaceStore((state) => state.refresh);
  const select = useWorkspaceStore((state) => state.select);
  const remove = useWorkspaceStore((state) => state.remove);
  const clearError = useWorkspaceStore((state) => state.clearError);
  const setDialog = useWorkspaceStore((state) => state.setDialog);

  /** Opened where it stands, so removing it from the list leaves the files. */
  const linked = projects.find((project) => project.name === current)?.linked === true;

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteName, setDeleteName] = useState("");
  const [dialogError, setDialogError] = useState<string | null>(null);
  /** What the current project holds, for the delete confirmation to say. */
  const [stats, setStats] = useState<ProjectStats | null>(null);

  const [trashOpen, setTrashOpen] = useState(false);
  const [trashItems, setTrashItems] = useState<TrashItem[]>([]);
  const [trashLoading, setTrashLoading] = useState(false);
  const [trashError, setTrashError] = useState<string | null>(null);
  const [restoringId, setRestoringId] = useState<string | null>(null);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const submitDelete = async () => {
    if (!current) return;
    // A linked folder is only let go of: nothing is destroyed, so there is
    // nothing to type. The core still asks for the name, and it is the same one.
    if (!linked && deleteName.trim() !== current) {
      setDialogError(t("project.nameMismatch"));
      return;
    }
    setDialogError(null);
    await remove(current, linked ? current : deleteName.trim());
    const failure = useWorkspaceStore.getState().error;
    if (failure) {
      setDialogError(failure);
      return;
    }
    setDeleteName("");
    setStats(null);
    setDeleteOpen(false);
  };

  const openDelete = async () => {
    setDialogError(null);
    setDeleteName("");
    setStats(null);
    setDeleteOpen(true);
    if (!current) return;
    // The dialog stays open across a switch, so the count belongs to the project
    // named in it — not to whichever one is active when the reply arrives.
    const name = current;
    try {
      const counted = await fetchProjectStats(name);
      if (useWorkspaceStore.getState().current === name) setStats(counted);
    } catch {
      // The dialog can still be used; it just cannot say how much is at stake.
      if (useWorkspaceStore.getState().current === name) setStats(null);
    }
  };

  const openTrash = async () => {
    setTrashOpen(true);
    setTrashError(null);
    setTrashItems([]);
    setTrashLoading(true);
    try {
      setTrashItems(await fetchTrash());
    } catch (err) {
      setTrashError(errorMessage(err, t("trash.loadFailed")));
    } finally {
      setTrashLoading(false);
    }
  };

  const restore = async (id: string) => {
    setRestoringId(id);
    setTrashError(null);
    try {
      await restoreTrash(id);
      // A restored project is a project again: the list has to include it.
      await refresh();
      setTrashItems(await fetchTrash());
    } catch (err) {
      setTrashError(errorMessage(err, t("trash.restoreFailed")));
    } finally {
      setRestoringId(null);
    }
  };

  /** Fetches the archive the core builds, letting the browser save it. */
  const exportArchive = () => {
    if (!current) return;
    downloadProjectArchive(current);
  };

  return (
    <div className="flex flex-col gap-1.5 border-b px-2.5 py-2">
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
          {t("project.label")}
        </span>
        {busy || loading ? (
          <Loader2 className="size-3 animate-spin text-muted-foreground" />
        ) : null}
      </div>

      <div className="flex items-center gap-1">
        <select
          className={cn(inputClass, "h-7 flex-1 truncate text-xs")}
          value={current ?? ""}
          onChange={(event) => void select(event.target.value)}
        >
          {current === null ? <option value="">{t("project.none")}</option> : null}
          {projects.map((project) => (
            <option key={project.name} value={project.name}>
              {project.name}
            </option>
          ))}
        </select>
        <IconTip label={t("project.new")}>
          <Button variant="ghost" size="icon-sm" onClick={() => setDialog("create")}>
            <FolderPlus />
          </Button>
        </IconTip>
        <IconTip label={t("project.open.action")}>
          <Button variant="ghost" size="icon-sm" onClick={() => setDialog("open")}>
            <FolderOpen />
          </Button>
        </IconTip>
        <IconTip label={t("project.export")}>
          <Button variant="ghost" size="icon-sm" disabled={!current} onClick={exportArchive}>
            <Download />
          </Button>
        </IconTip>
        <IconTip label={t("trash.open")}>
          <Button variant="ghost" size="icon-sm" onClick={() => void openTrash()}>
            <ArchiveRestore />
          </Button>
        </IconTip>
        <IconTip label={linked ? t("project.unlink") : t("project.delete")}>
          <Button
            variant="ghost"
            size="icon-sm"
            disabled={!current}
            onClick={() => void openDelete()}
          >
            <Trash2 />
          </Button>
        </IconTip>
      </div>

      {error ? (
        // Not just a message: something to do about it. The first attempt happens
        // before the service may be up, and a reader who started the app first should
        // not have to know to reload the window.
        <div className="flex items-start gap-1.5">
          <p className="min-w-0 flex-1 text-[11px] text-destructive">{error}</p>
          <Button
            variant="ghost"
            size="sm"
            className="h-5 shrink-0 px-1.5"
            disabled={loading}
            onClick={() => void refresh()}
          >
            {t("common.retry")}
          </Button>
          <IconTip label={t("project.dismissError")}>
            <Button
              variant="ghost"
              size="icon-xs"
              className="shrink-0"
              onClick={clearError}
            >
              <X />
            </Button>
          </IconTip>
        </div>
      ) : null}

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {linked ? t("project.unlinkTitle") : t("project.deleteTitle")}
            </DialogTitle>
            <DialogDescription>
              {linked ? t("project.unlinkDescription") : t("project.deleteDescription")}
            </DialogDescription>
          </DialogHeader>
          {stats ? (
            <p className="text-[11px] text-muted-foreground">
              {t(linked ? "project.unlinkContents" : "project.deleteContents", {
                pages: stats.pages,
                sources: stats.sources,
                notes: stats.notes,
              })}
            </p>
          ) : null}
          {linked ? null : (
            <p className="text-[11px] text-muted-foreground">
              {t("project.deleteRecoverable")}
            </p>
          )}
          {linked ? null : (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="delete-project-name">
                {t("project.deleteConfirmLabel", { name: current ?? "" })}
              </Label>
              <Input
                id="delete-project-name"
                value={deleteName}
                onChange={(event) => setDeleteName(event.target.value)}
              />
            </div>
          )}
          {dialogError ? <p className="text-xs text-destructive">{dialogError}</p> : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              variant="destructive"
              disabled={busy || (!linked && deleteName.trim() !== current)}
              onClick={() => void submitDelete()}
            >
              {linked ? t("project.unlinkSubmit") : t("project.deleteSubmit")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={trashOpen} onOpenChange={setTrashOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("trash.title")}</DialogTitle>
            <DialogDescription>{t("trash.body")}</DialogDescription>
          </DialogHeader>
          {trashLoading ? (
            <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <Loader2 className="size-3 animate-spin" /> {t("trash.loading")}
            </p>
          ) : null}
          {trashError ? <p className="text-xs text-destructive">{trashError}</p> : null}
          {!trashLoading && trashItems.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">{t("trash.empty")}</p>
          ) : null}
          {trashItems.length > 0 ? (
            <ul className="scroll-thin flex max-h-72 flex-col divide-y overflow-y-auto">
              {trashItems.map((item) => (
                <li key={item.id} className="flex items-center gap-2 py-1.5">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs">
                      {item.kind === "project"
                        ? item.name
                        : item.kind === "wiki"
                          ? t("trash.wikiOf", { name: item.project || item.name })
                          : item.relativePath || item.name}
                    </p>
                    <p className="truncate text-[10px] text-muted-foreground">
                      {t(`trash.kind.${item.kind || "file"}`)}
                      {" · "}
                      {t("trash.fileCount", { count: item.fileCount })}
                      {" · "}
                      {whenLabel(item.removedAt)}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={restoringId !== null}
                    onClick={() => void restore(item.id)}
                  >
                    {restoringId === item.id ? (
                      <Loader2 className="animate-spin" />
                    ) : (
                      <RotateCcw />
                    )}
                    {t("trash.restore")}
                  </Button>
                </li>
              ))}
            </ul>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setTrashOpen(false)}>
              {t("common.close")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
