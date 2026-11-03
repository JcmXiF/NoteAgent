import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { FolderCog, Loader2, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input, inputClass } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorMessage } from "@/lib/api/client";
import { initProject, resetProject } from "@/lib/api/projects";
import { useDocsStore } from "@/stores/docs-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * The two things that are done to a project rather than to a document in it.
 *
 * They sit at the foot of the sidebar because they are about everything the list
 * shows, and they stay there when the list is empty — which is exactly when a
 * project needs its structure filled in. Both say what they will do before they
 * do it; the second is destructive and asks for the project's name.
 */
export function DocsMaintenance() {
  const { t } = useTranslation();
  const project = useWorkspaceStore((state) => state.current);
  const refresh = useDocsStore((state) => state.refresh);

  const [dialog, setDialog] = useState<"fill" | "reset" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [preview, setPreview] = useState<
    { state: "counting" } | { state: "ready"; count: number } | { state: "failed"; message: string }
  >({ state: "counting" });

  // What a reset would remove is asked for before it is offered, so the dialog can
  // say how much is at stake rather than describing it in the abstract.
  useEffect(() => {
    if (dialog !== "reset" || !project) return undefined;
    let cancelled = false;
    setPreview({ state: "counting" });
    resetProject(project, { dryRun: true })
      .then((result) => {
        if (!cancelled) setPreview({ state: "ready", count: result.deletedFiles.length });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setPreview({ state: "failed", message: errorMessage(err, t("docs.maintenance.failed")) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [dialog, project, t]);

  const close = () => {
    setDialog(null);
    setTyped("");
    setError(null);
  };

  const submitFill = async () => {
    if (!project) return;
    setBusy(true);
    setError(null);
    try {
      await initProject(project);
      await refresh();
      close();
    } catch (err) {
      setError(errorMessage(err, t("docs.maintenance.failed")));
    } finally {
      setBusy(false);
    }
  };

  const submitReset = async () => {
    if (!project || typed.trim() !== project) return;
    setBusy(true);
    setError(null);
    try {
      await resetProject(project, { confirmName: typed.trim() });
      await refresh();
      close();
    } catch (err) {
      setError(errorMessage(err, t("docs.maintenance.failed")));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex shrink-0 items-center gap-1 border-t px-2 py-1.5">
      <Button
        variant="ghost"
        size="sm"
        disabled={!project}
        onClick={() => {
          setError(null);
          setDialog("fill");
        }}
        className="min-w-0 flex-1 justify-start text-[11px]"
      >
        <FolderCog /> <span className="truncate">{t("docs.maintenance.fill")}</span>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        disabled={!project}
        onClick={() => {
          setError(null);
          setTyped("");
          setDialog("reset");
        }}
        className="min-w-0 flex-1 justify-start text-[11px] text-destructive hover:bg-destructive/10"
      >
        <RotateCcw /> <span className="truncate">{t("docs.maintenance.reset")}</span>
      </Button>

      <Dialog
        open={dialog === "fill"}
        onOpenChange={(open) => {
          if (!open) close();
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("docs.maintenance.fillTitle")}</DialogTitle>
            <DialogDescription>{t("docs.maintenance.fillBody")}</DialogDescription>
          </DialogHeader>
          {error ? <p className="text-xs text-destructive">{error}</p> : null}
          <DialogFooter>
            <Button variant="outline" onClick={close}>
              {t("common.cancel")}
            </Button>
            <Button disabled={!project || busy} onClick={() => void submitFill()}>
              {busy ? <Loader2 className="animate-spin" /> : null}
              {t("docs.maintenance.fillSubmit")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={dialog === "reset"}
        onOpenChange={(open) => {
          if (!open) close();
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("docs.maintenance.resetTitle")}</DialogTitle>
            <DialogDescription>{t("docs.maintenance.resetBody")}</DialogDescription>
          </DialogHeader>
          <p className="text-[11px] text-muted-foreground">
            {preview.state === "counting"
              ? t("docs.maintenance.resetCounting")
              : preview.state === "ready"
                ? t("docs.maintenance.resetCount", { count: preview.count })
                : preview.message}
          </p>
          <p className="text-[11px] text-muted-foreground">
            {t("docs.maintenance.resetKept")}
          </p>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="reset-project-name">
              {t("docs.maintenance.resetConfirmLabel", { name: project ?? "" })}
            </Label>
            <Input
              id="reset-project-name"
              value={typed}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => setTyped(event.target.value)}
              className={inputClass}
            />
          </div>
          {error ? <p className="text-xs text-destructive">{error}</p> : null}
          <DialogFooter>
            <Button variant="outline" onClick={close}>
              {t("common.cancel")}
            </Button>
            <Button
              variant="destructive"
              disabled={!project || typed.trim() !== project || busy}
              onClick={() => void submitReset()}
            >
              {busy ? <Loader2 className="animate-spin" /> : null}
              {t("docs.maintenance.resetSubmit")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
