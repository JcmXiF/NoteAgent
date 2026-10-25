import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, Loader2, RefreshCw, RotateCcw, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { errorMessage } from "@/lib/api/client";
import { fetchAttention, type AttentionPayload } from "@/lib/api/attention";
import { postIngestRecoveryDiscard, postRevisionInboxAck, postRevisionInboxIgnore } from "@/lib/api/inbox";
import type { IngestRecoveryJob, RevisionInboxItem } from "@/lib/api/types";
import { findingLines, findingSentence, severityWord } from "@/lib/doctor-findings";
import { usePoll } from "@/lib/use-poll";
import { useClipsStore } from "@/stores/clips-store";
import { useCompileStore } from "@/stores/compile-store";
import { useLayoutStore } from "@/stores/layout-store";
import { useNavStore } from "@/stores/nav-store";
import { usePlanStore } from "@/stores/plan-store";
import { useSectionCount } from "@/stores/right-sections";
import { useWorkspaceStore } from "@/stores/workspace-store";

/** Base names look better in a list than full paths do. */
function baseName(identity: string): string {
  const parts = identity.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || identity;
}

/**
 * Everything waiting on the reader, in one list.
 *
 * Three queues used to announce themselves separately: sources that changed after
 * they were compiled, a compile that was confirmed and interrupted, and the wiki
 * check's own findings. To the person reading them they are one question, so they
 * share one heading and one number — and the number is on the heading, where it is
 * legible while the whole section is folded away.
 *
 * Nothing here acts on its own. A changed source is only reported until asked to
 * be worked out again, and an interrupted compile waits for the reader to resume
 * it: both cost model calls, and neither is a decision a poller should make.
 */
export function AttentionInbox() {
  const { t } = useTranslation();
  const project = useWorkspaceStore((state) => state.current);
  const rightOpen = useLayoutStore((state) => state.rightOpen);
  const collections = useClipsStore((state) => state.collections);
  const focusCollection = useClipsStore((state) => state.focusCollection);
  const go = useNavStore((state) => state.go);
  const plan = usePlanStore((state) => state.run);
  const compiling = useCompileStore((state) => state.running);
  const compile = useCompileStore((state) => state.run);

  const [payload, setPayload] = useState<AttentionPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const load = useCallback(
    async (check: boolean) => {
      if (!project) {
        setPayload(null);
        return;
      }
      try {
        const found = await fetchAttention(project, check);
        // A repeat read skips the check, so its answer carries no findings. What
        // the check last said stays on screen rather than vanishing between ticks.
        setPayload((previous) =>
          check || !previous ? found : { ...found, doctor: previous.doctor },
        );
        setError(null);
      } catch (err) {
        setError(errorMessage(err, t("attention.readFailed")));
      }
    },
    [project, t],
  );

  // The first read is the one that runs the check; after that only the queues are
  // reread, because a check reads every page in the project.
  useEffect(() => {
    if (!project || !rightOpen) return;
    void load(true);
  }, [project, rightOpen, load]);

  usePoll(() => load(false), Boolean(project) && rightOpen && payload !== null);

  const total = payload?.total ?? null;
  useSectionCount("attention", total);

  const dismissRevision = async (item: RevisionInboxItem) => {
    if (!project) return;
    const key = `${item.identity}::${item.kind}`;
    setBusyKey(key);
    try {
      if (item.kind === "raw_deleted") {
        await postRevisionInboxAck(project, item.identity);
      } else {
        await postRevisionInboxIgnore(project, item.identity, item.kind);
      }
      await load(false);
    } catch (err) {
      setError(errorMessage(err, t("attention.updateFailed")));
    } finally {
      setBusyKey(null);
    }
  };

  const discardRecovery = async (job: IngestRecoveryJob) => {
    if (!project) return;
    setBusyKey(`recovery:${job.id}`);
    try {
      await postIngestRecoveryDiscard(project, job.id);
      await load(false);
    } catch (err) {
      setError(errorMessage(err, t("attention.updateFailed")));
    } finally {
      setBusyKey(null);
    }
  };

  if (!project) return null;

  const revision = payload?.revision.items ?? [];
  const recovery = payload?.recovery.items ?? [];
  const doctorLines = payload?.doctor ? findingLines(payload.doctor.findings) : [];
  const findings = doctorLines.slice(0, 12);

  if (!payload && !error) return null;
  if (payload && payload.total === 0 && !error) return null;

  return (
    <div className="flex shrink-0 flex-col gap-1 pb-1">
      {error ? (
        <p className="px-2 text-[11px] text-destructive">{error}</p>
      ) : null}

      {revision.length > 0 ? (
        <section>
          <p className="flex items-center gap-1 px-2 py-0.5 text-[10px] font-semibold text-muted-foreground">
            {t("attention.revisions")}
            <Badge tone="warning">{payload?.counts.revision ?? 0}</Badge>
          </p>
          <ul className="flex flex-col gap-px">
            {revision.map((item) => {
              const key = `${item.identity}::${item.kind}`;
              const deleted = item.kind === "raw_deleted";
              // Only selections the regular planning pass would use. A round's
              // selections are planned while that round runs, so offering to plan
              // them here would be a button that does nothing.
              const related = collections.filter(
                (row) => row.sourceKey === item.identity && !row.roundId,
              );
              const pages = item.filesWritten.filter((path) =>
                path.replace(/\\/g, "/").startsWith("wiki/"),
              );
              return (
                <li key={key} className="px-2 py-1 text-[11px]">
                  <p className="truncate font-medium" title={item.identity}>
                    {baseName(item.identity)}
                    <span className="ml-1 font-normal text-muted-foreground">
                      {t(`attention.kind.${item.kind}`, { defaultValue: item.kind })}
                    </span>
                  </p>
                  {pages.length > 0 ? (
                    <p
                      className="truncate text-[10px] text-muted-foreground"
                      title={pages.join(" · ")}
                    >
                      {pages.join(" · ")}
                    </p>
                  ) : null}
                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    {deleted ? (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={busyKey === key}
                        onClick={() => void dismissRevision(item)}
                      >
                        {busyKey === key ? <Loader2 className="animate-spin" /> : null}
                        {t("attention.acknowledge")}
                      </Button>
                    ) : (
                      <>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={busyKey === key}
                          onClick={() => {
                            if (related.length === 0) {
                              go(
                                { kind: "sources", sourceKey: item.identity },
                                { placement: "beside" },
                              );
                              return;
                            }
                            focusCollection(related[0].id);
                            go(
                              { kind: "sources", sourceKey: item.identity },
                              { placement: "beside" },
                            );
                            void plan({ sourceIdentity: item.identity });
                          }}
                        >
                          {related.length > 0 ? t("attention.planAgain") : t("attention.pickAgain")}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={busyKey === key}
                          onClick={() => void dismissRevision(item)}
                        >
                          {busyKey === key ? <Loader2 className="animate-spin" /> : null}
                          {t("attention.ignore")}
                        </Button>
                      </>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {recovery.length > 0 ? (
        <section>
          <p className="flex items-center gap-1 px-2 py-0.5 text-[10px] font-semibold text-muted-foreground">
            {t("attention.recovery")}
            <Badge tone="warning">{payload?.counts.recovery ?? 0}</Badge>
          </p>
          <ul className="flex flex-col gap-px">
            {recovery.map((job) => {
              const key = `recovery:${job.id}`;
              const pages = job.confirmed.filter((item) => item.action === "write");
              const names =
                job.writePaths.length > 0
                  ? job.writePaths
                  : pages.map((item) => item.wikiPath || item.slug);
              return (
                <li key={key} className="px-2 py-1 text-[11px]">
                  <p className="truncate font-medium">
                    {job.intent || t("compile.title")}
                    <span className="ml-1 font-normal text-muted-foreground">
                      {t("attention.pages", { count: names.length })}
                    </span>
                  </p>
                  <p
                    className="truncate text-[10px] text-muted-foreground"
                    title={names.join(" · ")}
                  >
                    {names.join(" · ")}
                    {job.error ? ` · ${job.error}` : ""}
                  </p>
                  <div className="mt-1 flex items-center gap-1">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={compiling || busyKey === key}
                      onClick={() =>
                        void compile({
                          confirmed: job.confirmed,
                          intent: job.intent,
                          retryable: false,
                        })
                      }
                    >
                      {compiling ? <Loader2 className="animate-spin" /> : <RotateCcw />}
                      {t("attention.resume")}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={compiling || busyKey === key}
                      onClick={() => void discardRecovery(job)}
                    >
                      <Trash2 /> {t("attention.discard")}
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {payload?.doctor && payload.counts.doctor > 0 ? (
        <section>
          <p className="flex items-center gap-1 px-2 py-0.5 text-[10px] font-semibold text-muted-foreground">
            {t("attention.check")}
            <Badge tone="warning">{payload.counts.doctor}</Badge>
            <Button
              variant="ghost"
              size="icon-xs"
              className="ml-auto"
              aria-label={t("attention.recheck")}
              title={t("attention.recheck")}
              onClick={() => void load(true)}
            >
              <RefreshCw />
            </Button>
          </p>
          <ul className="flex flex-col gap-px">
            {findings.map((line, index) => (
              <li
                key={`${line.code}:${line.path ?? ""}:${index}`}
                className="flex items-start gap-1 px-2 py-0.5 text-[11px]"
              >
                <AlertCircle
                  className={
                    line.severity === "error"
                      ? "mt-0.5 size-3 shrink-0 text-destructive"
                      : "mt-0.5 size-3 shrink-0 text-warning"
                  }
                />
                <span className="min-w-0 flex-1">
                  <span className="block">{findingSentence(line, t)}</span>
                  {line.path ? (
                    <span className="block truncate text-[10px] text-muted-foreground">
                      {baseName(line.path)} · {severityWord(line.severity, t)}
                    </span>
                  ) : (
                    <span className="block text-[10px] text-muted-foreground">
                      {severityWord(line.severity, t)}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
          {doctorLines.length > findings.length ? (
            <p className="px-2 py-0.5 text-[10px] text-muted-foreground">
              {t("attention.more", { count: doctorLines.length - findings.length })}
            </p>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
