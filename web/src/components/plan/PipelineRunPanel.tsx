import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Play, RotateCcw, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { errorMessage } from "@/lib/api/client";
import {
  cancelPipelineRun,
  fetchPipelineConfirmation,
  fetchPipelineMaterials,
  fetchPipelineRestorePreview,
  fetchPipelineRuns,
  restorePipelineRun,
  startPipelineRun,
  type PipelineConfirmation,
  type PipelineMaterial,
  type PipelineMaterialMode,
  type PipelineQueryMode,
  type PipelineRestoreChoice,
  type PipelineRestorePreview,
  type PipelineRun,
  type PipelineRunStatus,
} from "@/lib/api/pipeline";
import { usePoll } from "@/lib/use-poll";
import { useNavStore } from "@/stores/nav-store";
import { useSectionCount } from "@/stores/right-sections";

/**
 * The pipeline's one run: start it with an intent, watch it, stop it.
 *
 * The panel is a client of the core's run object, not its owner: it starts a run
 * and reads the state back. A run that is still moving is polled; one that has
 * settled is left alone.
 */
const ACTIVE: ReadonlySet<PipelineRunStatus> = new Set([
  "running",
  "awaiting_confirmation",
  "compiling",
]);

type RestoreChoice = { action: "restore" | "skip"; force: boolean };

export function PipelineRunPanel() {
  const { t } = useTranslation();
  const go = useNavStore((state) => state.go);
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [materials, setMaterials] = useState<PipelineMaterial[]>([]);
  const [draft, setDraft] = useState("");
  const [queryMode, setQueryMode] = useState<PipelineQueryMode>("precise");
  const [materialMode, setMaterialMode] = useState<PipelineMaterialMode>("auto");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] =
    useState<PipelineConfirmation | null>(null);
  const [preview, setPreview] = useState<PipelineRestorePreview | null>(null);
  const [choices, setChoices] = useState<Record<string, RestoreChoice>>({});
  const [restoreBusy, setRestoreBusy] = useState(false);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [restoreNotice, setRestoreNotice] = useState("");

  const latest = runs[0] ?? null;
  const active = latest !== null && ACTIVE.has(latest.status);

  useEffect(() => {
    setPreview(null);
    setChoices({});
    setRestoreError(null);
    setRestoreNotice("");
  }, [latest?.id, latest?.status]);

  const fetchAll = useCallback(async () => {
    try {
      const data = await fetchPipelineRuns();
      setRuns(data.runs);
      const newest = data.runs[0];
      const listed = newest
        ? await fetchPipelineMaterials(newest.id)
        : { materials: [] };
      setMaterials(listed.materials);
      if (newest?.status === "awaiting_confirmation") {
        try {
          setConfirmation(await fetchPipelineConfirmation(newest.id));
        } catch {
          setConfirmation(null);
        }
      } else {
        setConfirmation(null);
      }
      return true;
    } catch (err) {
      setError(errorMessage(err, t("pipeline.loadFailed")));
      return false;
    } finally {
      setLoaded(true);
    }
  }, [t]);

  const load = useCallback(async () => {
    if (await fetchAll()) setError(null);
  }, [fetchAll]);

  useEffect(() => {
    void load();
  }, [load]);

  // A moving run is worth watching; a settled one is not.
  usePoll(load, active, 1000);
  useSectionCount("pipeline", active ? 1 : 0);

  const start = async () => {
    const intent = draft.trim();
    if (!intent || busy || materialMode !== "auto") return;
    setBusy(true);
    setError(null);
    try {
      const run = await startPipelineRun({
        intent,
        queryMode,
        materialMode,
      });
      setRuns((current) => [run, ...current]);
      setDraft("");
      void load();
    } catch (err) {
      setError(errorMessage(err, t("pipeline.startFailed")));
      // A refusal is usually another run holding the project; showing that run
      // is the answer, and it starts the poll. The reason the start failed stays
      // on screen, so this refresh does not clear the error.
      void fetchAll();
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    if (!latest || !active || busy) return;
    setBusy(true);
    setError(null);
    try {
      const run = await cancelPipelineRun(latest.id);
      setRuns((current) =>
        current.map((item) => (item.id === run.id ? run : item)),
      );
    } catch (err) {
      setError(errorMessage(err, t("pipeline.cancelFailed")));
    } finally {
      setBusy(false);
    }
  };

  const openRestore = async () => {
    if (!latest || restoreBusy) return;
    setRestoreBusy(true);
    setRestoreError(null);
    setRestoreNotice("");
    try {
      const data = await fetchPipelineRestorePreview(latest.id);
      setPreview(data);
      setChoices(
        Object.fromEntries(
          data.pages.map((page) => [
            page.path,
            { action: page.defaultAction, force: false },
          ]),
        ),
      );
    } catch (err) {
      setRestoreError(errorMessage(err, t("pipeline.restore.loadFailed")));
    } finally {
      setRestoreBusy(false);
    }
  };

  const choose = (path: string, patch: Partial<RestoreChoice>) => {
    setChoices((current) => ({
      ...current,
      [path]: {
        ...(current[path] ?? { action: "skip", force: false }),
        ...patch,
      },
    }));
  };

  const applyRestore = async () => {
    if (!latest || !preview || restoreBusy) return;
    setRestoreBusy(true);
    setRestoreError(null);
    try {
      const pages: PipelineRestoreChoice[] = preview.pages.map((page) => {
        const choice = choices[page.path] ?? {
          action: page.defaultAction,
          force: false,
        };
        return {
          path: page.path,
          action: choice.action,
          force: choice.force,
        };
      });
      const result = await restorePipelineRun(latest.id, pages);
      const parts = [
        t("pipeline.restore.done", { count: result.restored.length }),
      ];
      if (result.conflicted.length > 0) {
        parts.push(
          t("pipeline.restore.conflicted", {
            count: result.conflicted.length,
          }),
        );
      }
      if (result.failed.length > 0) {
        parts.push(
          t("pipeline.restore.failedCount", {
            count: result.failed.length,
          }),
        );
      }
      setRestoreNotice(parts.join(" "));
      setPreview(null);
      setChoices({});
      await load();
    } catch (err) {
      setRestoreError(errorMessage(err, t("pipeline.restore.failed")));
    } finally {
      setRestoreBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-1.5 p-2">
      <form
        className="flex items-center gap-1"
        onSubmit={(event) => {
          event.preventDefault();
          void start();
        }}
      >
        <Input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={t("pipeline.intentPlaceholder")}
          aria-label={t("pipeline.intentLabel")}
          className="h-7 flex-1 text-xs"
          spellCheck={false}
        />
        <Button
          type="submit"
          size="sm"
          disabled={!draft.trim() || busy || materialMode === "manual"}
        >
          {busy ? <Loader2 className="animate-spin" /> : <Play />}
          {t("pipeline.start")}
        </Button>
      </form>

      <div className="flex items-center gap-1 text-[10px]">
        <span className="shrink-0 text-muted-foreground">
          {t("pipeline.query.label")}
        </span>
        <ModeButton
          active={queryMode === "precise"}
          onClick={() => setQueryMode("precise")}
          label={t("pipeline.query.precise")}
        />
        <ModeButton
          active={queryMode === "model"}
          onClick={() => setQueryMode("model")}
          label={t("pipeline.query.model")}
        />
      </div>
      <div className="flex flex-wrap items-center gap-1 text-[10px]">
        <span className="shrink-0 text-muted-foreground">
          {t("pipeline.materialMode.label")}
        </span>
        <ModeButton
          active={materialMode === "auto"}
          onClick={() => setMaterialMode("auto")}
          label={t("pipeline.materialMode.auto")}
        />
        <ModeButton
          active={materialMode === "manual"}
          onClick={() => setMaterialMode("manual")}
          label={t("pipeline.materialMode.manual")}
        />
      </div>
      {materialMode === "manual" ? (
        <p className="text-[10px] text-muted-foreground">
          {t("pipeline.materialMode.manualHint")}
        </p>
      ) : null}

      {error ? (
        <p className="text-[11px] text-destructive">{error}</p>
      ) : null}

      {!loaded ? null : latest ? (
        <div className="flex flex-col gap-0.5 rounded-md border bg-muted/30 p-1.5">
          <p className="truncate text-[11px] font-medium" title={latest.intent}>
            {latest.intent}
          </p>
          <p className="text-[10px] text-muted-foreground">
            {t(`pipeline.status.${latest.status}`)}
            {" · "}
            {t(`pipeline.stage.${latest.stage}`, {
              defaultValue: latest.stage,
            })}
          </p>
          {latest.error ? (
            <p className="text-[10px] text-destructive">{latest.error}</p>
          ) : null}
          {confirmation?.pages.some((page) => page.action === "write") ? (
            <div className="mt-1 border-t pt-1">
              <p className="text-[10px] font-medium text-muted-foreground">
                {t("pipeline.toWrite", {
                  count: confirmation.pages.filter(
                    (page) => page.action === "write",
                  ).length,
                })}
              </p>
              <ul className="mt-0.5 flex flex-col gap-0.5">
                {confirmation.pages
                  .filter((page) => page.action === "write")
                  .slice(0, 6)
                  .map((page) => (
                    <li
                      key={page.slug}
                      className="truncate text-[10px]"
                      title={page.wikiPath}
                    >
                      {page.wikiPath}
                    </li>
                  ))}
              </ul>
            </div>
          ) : null}
          {latest.result?.written?.length ? (
            <div className="mt-1 border-t pt-1">
              <p className="text-[10px] font-medium text-muted-foreground">
                {t("pipeline.written", {
                  count: latest.result.written.length,
                })}
              </p>
              <ul className="mt-0.5 flex flex-col gap-0.5">
                {latest.result.written.slice(0, 6).map((path) => (
                  <li
                    key={path}
                    className="truncate text-[10px]"
                    title={path}
                  >
                    {path}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {materials.length > 0 ? (
            <div className="mt-1 border-t pt-1">
              <p className="text-[10px] font-medium text-muted-foreground">
                {t("pipeline.materials", { count: materials.length })}
              </p>
              <ul className="mt-0.5 flex flex-col gap-0.5">
                {materials.slice(0, 6).map((item) => (
                  <li key={item.material}>
                    <button
                      type="button"
                      className="flex w-full items-center gap-1 text-left text-[10px] hover:text-foreground"
                      title={item.material}
                      onClick={() =>
                        go(
                          {
                            kind: "material",
                            runId: latest.id,
                            material: item.material,
                            title: item.title,
                          },
                          { placement: "beside" },
                        )
                      }
                    >
                      <span className="min-w-0 flex-1 truncate">
                        {item.title}
                      </span>
                      <span className="shrink-0 text-muted-foreground">
                        {t(`pipeline.material.${item.state}`, {
                          defaultValue: item.state,
                        })}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {(latest.result?.effectIds?.length ?? 0) > 0 ? (
            <div className="mt-1 border-t pt-1">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 self-start px-1.5 text-[10px]"
                disabled={restoreBusy}
                onClick={() => void openRestore()}
              >
                {restoreBusy ? <Loader2 className="animate-spin" /> : <RotateCcw />}
                {t("pipeline.restore.open")}
              </Button>
              {preview ? (
                <div className="mt-1 flex flex-col gap-1">
                  <p className="text-[10px] text-muted-foreground">
                    {t("pipeline.restore.hint")}
                  </p>
                  {preview.pages.map((page) => {
                    const choice = choices[page.path] ?? {
                      action: page.defaultAction,
                      force: false,
                    };
                    const canRestore =
                      page.classification !== "missing" &&
                      page.classification !== "renamed";
                    const needsForce =
                      canRestore && page.classification !== "unchanged";
                    return (
                      <div
                        key={page.path}
                        className="rounded border bg-background/60 p-1 text-[10px]"
                      >
                        <p className="truncate" title={page.path}>
                          {page.path}
                        </p>
                        <p className="text-muted-foreground">
                          {t(
                            `pipeline.restore.classification.${page.classification}`,
                            { defaultValue: page.classification },
                          )}
                        </p>
                        <p className="text-muted-foreground">
                          {t("pipeline.restore.kind", { kind: page.kind })}
                        </p>
                        <p className="text-muted-foreground">
                          {t("pipeline.restore.run", {
                            runId: page.runId,
                          })}
                        </p>
                        <p className="text-muted-foreground">
                          {t("pipeline.restore.at", { at: page.at })}
                        </p>
                        <p className="text-muted-foreground">
                          {t("pipeline.restore.defaultAction", {
                            action: t(
                              `pipeline.restore.default.${page.defaultAction}`,
                            ),
                          })}
                        </p>
                        {page.currentPath ? (
                          <p className="text-muted-foreground">
                            {t("pipeline.restore.movedTo", {
                              path: page.currentPath,
                            })}
                          </p>
                        ) : null}
                        {canRestore ? (
                          <>
                            <label className="mt-0.5 flex items-center gap-1">
                              <input
                                type="checkbox"
                                checked={choice.action === "restore"}
                                onChange={(event) =>
                                  choose(page.path, {
                                    action: event.target.checked
                                      ? "restore"
                                      : "skip",
                                  })
                                }
                              />
                              {t("pipeline.restore.restore")}
                            </label>
                            {needsForce ? (
                              <label className="flex items-center gap-1">
                                <input
                                  type="checkbox"
                                  checked={choice.force}
                                  onChange={(event) =>
                                    choose(page.path, {
                                      force: event.target.checked,
                                    })
                                  }
                                />
                                {t("pipeline.restore.force")}
                              </label>
                            ) : null}
                          </>
                        ) : (
                          <p className="text-muted-foreground">
                            {t("pipeline.restore.cannotRestore")}
                          </p>
                        )}
                      </div>
                    );
                  })}
                  <Button
                    type="button"
                    size="sm"
                    className="h-7 self-start px-1.5 text-[10px]"
                    disabled={restoreBusy}
                    onClick={() => void applyRestore()}
                  >
                    {restoreBusy ? <Loader2 className="animate-spin" /> : null}
                    {t("pipeline.restore.apply")}
                  </Button>
                </div>
              ) : null}
              {restoreNotice ? (
                <p className="mt-1 text-[10px] text-muted-foreground">
                  {restoreNotice}
                </p>
              ) : null}
              {restoreError ? (
                <p className="mt-1 text-[10px] text-destructive">
                  {restoreError}
                </p>
              ) : null}
            </div>
          ) : null}
          {active ? (
            <Button
              variant="ghost"
              size="sm"
              className="mt-1 self-start"
              disabled={busy}
              onClick={() => void stop()}
            >
              <Square />
              {t("pipeline.cancel")}
            </Button>
          ) : null}
        </div>
      ) : (
        <p className="text-[11px] text-muted-foreground">
          {t("pipeline.empty")}
        </p>
      )}
    </div>
  );
}

function ModeButton({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant={active ? "secondary" : "ghost"}
      aria-pressed={active}
      className="h-6 px-1.5 text-[10px]"
      onClick={onClick}
    >
      {label}
    </Button>
  );
}
