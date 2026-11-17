import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Play, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { errorMessage } from "@/lib/api/client";
import {
  cancelPipelineRun,
  fetchPipelineRuns,
  startPipelineRun,
  type PipelineRun,
  type PipelineRunStatus,
} from "@/lib/api/pipeline";
import { usePoll } from "@/lib/use-poll";
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

export function PipelineRunPanel() {
  const { t } = useTranslation();
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const latest = runs[0] ?? null;
  const active = latest !== null && ACTIVE.has(latest.status);

  const fetchRuns = useCallback(async () => {
    try {
      const data = await fetchPipelineRuns();
      setRuns(data.runs);
      return true;
    } catch (err) {
      setError(errorMessage(err, t("pipeline.loadFailed")));
      return false;
    } finally {
      setLoaded(true);
    }
  }, [t]);

  const load = useCallback(async () => {
    if (await fetchRuns()) setError(null);
  }, [fetchRuns]);

  useEffect(() => {
    void load();
  }, [load]);

  // A moving run is worth watching; a settled one is not.
  usePoll(load, active, 1000);
  useSectionCount("pipeline", active ? 1 : 0);

  const start = async () => {
    const intent = draft.trim();
    if (!intent || busy) return;
    setBusy(true);
    setError(null);
    try {
      const run = await startPipelineRun({ intent });
      setRuns((current) => [run, ...current]);
      setDraft("");
    } catch (err) {
      setError(errorMessage(err, t("pipeline.startFailed")));
      // A refusal is usually another run holding the project; showing that run
      // is the answer, and it starts the poll.
      void fetchRuns();
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
          disabled={!draft.trim() || busy}
        >
          {busy ? <Loader2 className="animate-spin" /> : <Play />}
          {t("pipeline.start")}
        </Button>
      </form>

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
