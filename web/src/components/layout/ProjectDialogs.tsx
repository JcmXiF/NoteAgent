import { useEffect, useState } from "react";
import { Check, FolderOpen, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
  inspectFolder,
  type FolderInspection,
  type FolderOpenMode,
  type FolderOpenResult,
} from "@/lib/api/projects";
import { pickFolder, canPickFolder } from "@/lib/desktop";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { cn } from "@/lib/utils";

/**
 * The two ways a project begins.
 *
 * They are one component because they are one decision — "what am I working in" —
 * and because both are asked for from places that are not the sidebar: the empty
 * workspace offers them before the sidebar has anything to show.
 */
export function ProjectDialogs() {
  const dialog = useWorkspaceStore((state) => state.dialog);
  const setDialog = useWorkspaceStore((state) => state.setDialog);

  return (
    <>
      <NewProjectDialog open={dialog === "create"} onClose={() => setDialog(null)} />
      <OpenFolderDialog open={dialog === "open"} onClose={() => setDialog(null)} />
    </>
  );
}

function NewProjectDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const busy = useWorkspaceStore((state) => state.busy);
  const create = useWorkspaceStore((state) => state.create);

  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setName("");
      setError(null);
    }
  }, [open]);

  const submit = async () => {
    setError(null);
    await create(name);
    const failure = useWorkspaceStore.getState().error;
    if (failure) {
      setError(failure);
      return;
    }
    onClose();
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("project.createTitle")}</DialogTitle>
          <DialogDescription>{t("project.createDescription")}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="new-project-name">{t("project.createNameLabel")}</Label>
          <Input
            id="new-project-name"
            value={name}
            placeholder={t("project.createNamePlaceholder")}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void submit();
            }}
          />
          {error ? <p className="text-xs text-destructive">{error}</p> : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button disabled={!name.trim() || busy} onClick={() => void submit()}>
            {t("project.createSubmit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function OpenFolderDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const busy = useWorkspaceStore((state) => state.busy);
  const openExisting = useWorkspaceStore((state) => state.openExisting);

  const [path, setPath] = useState("");
  const [inspection, setInspection] = useState<FolderInspection | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const [mode, setMode] = useState<FolderOpenMode>("link");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  /** What came in, when there is something to report before closing. */
  const [opened, setOpened] = useState<FolderOpenResult | null>(null);

  useEffect(() => {
    if (open) return;
    setPath("");
    setInspection(null);
    setInspecting(false);
    setError(null);
    setOpened(null);
  }, [open]);

  const check = async (candidate: string) => {
    const wanted = candidate.trim();
    setError(null);
    if (!wanted) {
      setInspection(null);
      return;
    }
    setInspecting(true);
    try {
      const found = await inspectFolder(wanted);
      setInspection(found);
      setName(found.name);
      setMode(found.canLink ? "link" : "copy");
    } catch (err) {
      setInspection(null);
      setError(errorMessage(err, t("project.open.inspectFailed")));
    } finally {
      setInspecting(false);
    }
  };

  const browse = async () => {
    const chosen = await pickFolder();
    if (!chosen) return;
    setPath(chosen);
    await check(chosen);
  };

  const submit = async () => {
    if (!inspection) return;
    setError(null);
    const result = await openExisting(inspection.path, mode, name);
    if (!result) {
      setError(useWorkspaceStore.getState().error ?? t("project.open.failed"));
      return;
    }
    // What was left out is the one thing worth saying before the dialog goes: a
    // page that did not come in would otherwise look like a page that was lost.
    if (result.skipped.length > 0) {
      setOpened(result);
      return;
    }
    onClose();
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("project.open.title")}</DialogTitle>
          <DialogDescription>{t("project.open.description")}</DialogDescription>
        </DialogHeader>

        {opened ? (
          <OpenedSummary result={opened} />
        ) : (
          <>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="open-folder-path">{t("project.open.pathLabel")}</Label>
              <div className="flex items-center gap-1.5">
                <Input
                  id="open-folder-path"
                  value={path}
                  className="flex-1"
                  placeholder={t("project.open.pathPlaceholder")}
                  title={path}
                  onChange={(event) => {
                    setPath(event.target.value);
                    // The summary belongs to the path that was checked, not to
                    // whatever has been typed since.
                    setInspection(null);
                    setError(null);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      void check(path);
                    }
                  }}
                />
                {canPickFolder() ? (
                  <Button variant="outline" size="sm" onClick={() => void browse()}>
                    <FolderOpen />
                    {t("project.open.browse")}
                  </Button>
                ) : null}
              </div>
              <p className="text-[11px] text-muted-foreground">
                {t("project.open.browseHint")}
              </p>
            </div>

            {inspecting ? (
              <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <Loader2 className="size-3 animate-spin" />
                {t("project.open.inspecting")}
              </p>
            ) : null}

            {inspection ? (
              <>
                <p className="text-[11px] text-muted-foreground">
                  {t("project.open.summary", {
                    pages: inspection.markdownCount,
                    others: inspection.otherCount,
                    size: sizeLabel(inspection.bytes),
                  })}
                  {inspection.truncated ? ` ${t("project.open.truncated")}` : ""}
                </p>

                {inspection.alreadyManaged ? (
                  <p className="text-[11px] text-warning">
                    {t("project.open.alreadyManaged")}
                  </p>
                ) : (
                  <div className="flex flex-col gap-1.5">
                    {inspection.canLink ? (
                      <ModeChoice
                        checked={mode === "link"}
                        onSelect={() => setMode("link")}
                        label={t("project.open.linkOption")}
                        hint={t("project.open.linkHint")}
                      />
                    ) : null}
                    <ModeChoice
                      checked={mode === "copy"}
                      onSelect={() => setMode("copy")}
                      label={
                        inspection.canLink
                          ? t("project.open.copyOption")
                          : t("project.open.importOption")
                      }
                      hint={
                        inspection.canLink
                          ? t("project.open.copyHint")
                          : t("project.open.importHint")
                      }
                    />
                  </div>
                )}

                {mode === "copy" ? (
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="open-folder-name">
                      {t("project.open.nameLabel")}
                    </Label>
                    <Input
                      id="open-folder-name"
                      value={name}
                      placeholder={t("project.createNamePlaceholder")}
                      onChange={(event) => setName(event.target.value)}
                    />
                  </div>
                ) : null}

                {inspection.alreadyLinked ? (
                  <p className="text-[11px] text-muted-foreground">
                    {t("project.open.alreadyLinked")}
                  </p>
                ) : null}
              </>
            ) : null}

            {error ? <p className="text-xs text-destructive">{error}</p> : null}
          </>
        )}

        <DialogFooter>
          {opened ? (
            <Button onClick={onClose}>{t("common.close")}</Button>
          ) : (
            <>
              <Button variant="outline" onClick={onClose}>
                {t("common.cancel")}
              </Button>
              <Button
                disabled={
                  !inspection ||
                  busy ||
                  inspecting ||
                  inspection.alreadyManaged ||
                  (mode === "copy" && !name.trim())
                }
                onClick={() => void submit()}
              >
                {busy ? <Loader2 className="animate-spin" /> : null}
                {mode === "copy"
                  ? t("project.open.submitCopy")
                  : t("project.open.submitLink")}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** One of the ways a folder can be opened, chosen as a whole row. */
function ModeChoice({
  checked,
  onSelect,
  label,
  hint,
}: {
  checked: boolean;
  onSelect: () => void;
  label: string;
  hint: string;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      onClick={onSelect}
      className={cn(
        "flex w-full items-start gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors",
        checked ? "border-primary/60 bg-primary/5" : "hover:bg-accent/40",
      )}
    >
      <span
        className={cn(
          "mt-0.5 flex size-3.5 shrink-0 items-center justify-center rounded-full border",
          checked ? "border-primary text-primary" : "text-transparent",
        )}
      >
        {checked ? <Check className="size-3" /> : null}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-xs font-medium">{label}</span>
        <span className="block text-[11px] text-muted-foreground">{hint}</span>
      </span>
    </button>
  );
}

/** What came in, and what was left out. */
function OpenedSummary({ result }: { result: FolderOpenResult }) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-1.5 text-[11px] text-muted-foreground">
      <p className="text-xs font-medium text-foreground">
        {t("project.open.opened", { name: result.name })}
      </p>
      <p>
        {t("project.open.openedCounts", {
          pages: result.imported,
          others: result.attachments,
        })}
      </p>
      {result.skipped.length > 0 ? (
        <p>{t("project.open.openedSkipped", { files: result.skipped.join(", ") })}</p>
      ) : null}
    </div>
  );
}

/** A size as a person reads it, for the line that says what would be copied. */
export function sizeLabel(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}
