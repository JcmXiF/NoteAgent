import { useTranslation } from "react-i18next";
import { AlertCircle, Check, FileText, Hammer, Loader2, RotateCcw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { defaultActionForNode, topicMapNodes } from "@/lib/clips";
import { findingLines, findingSentence, severityWord } from "@/lib/doctor-findings";
import type { ConfirmedPage } from "@/lib/api/types";
import { useClipsStore } from "@/stores/clips-store";
import { useSectionCount } from "@/stores/right-sections";
import { useCompileStore } from "@/stores/compile-store";
import { useDeepSearchStore } from "@/stores/deep-search-store";
import { useNavStore } from "@/stores/nav-store";

/**
 * Writes the pages that were ticked.
 *
 * The decisions made while planning are the input: pages marked to be written
 * go in with the sections that were chosen, pages marked to be mentioned go in
 * as a mention, and pages left out are not sent at all. The work itself lives in
 * the compile store, because resuming an interrupted compile uses the same path.
 */
export function CompilePanel() {
  const { t } = useTranslation();
  const topicMap = useClipsStore((state) => state.topicMap);
  const decisions = useClipsStore((state) => state.decisions);
  const running = useCompileStore((state) => state.running);
  const round = useCompileStore((state) => state.round);
  const error = useCompileStore((state) => state.error);
  const compile = useCompileStore((state) => state.run);
  const retry = useCompileStore((state) => state.retry);
  const go = useNavStore((state) => state.go);

  const session = useDeepSearchStore((state) => state.session);
  const inRound = Boolean(session?.roundId) && (session?.step ?? 0) >= 4;
  // One list while a search runs: what it proposed, refined by its plan.
  const nodes = inRound
    ? (session?.whitelist ?? [])
    : topicMap
      ? topicMapNodes(topicMap)
      : [];

  // Worked out before the empty case returns, because the count below is a hook:
  // a hook that only runs once there is something to count runs sometimes.
  const planned = nodes.map((node) => ({
    node,
    decision: decisions[node.slug] ?? {
      slug: node.slug,
      action: defaultActionForNode(node),
      sectionIds: (node.outline ?? []).map((section) => section.id),
    },
  }));
  const writing = planned.filter((item) => item.decision.action === "write");
  const mentioning = planned.filter((item) => item.decision.action === "mention");

  useSectionCount("compile", writing.length);

  if (nodes.length === 0) return null;

  const start = () => {
    const confirmed: ConfirmedPage[] = planned.map(({ node, decision }) => ({
      // The confirmation body asks for `name` — that is its own contract, and the
      // page's own word for it is `title`.
      name: node.title,
      slug: node.slug,
      wikiPath: node.wikiPath ?? "",
      action: decision.action,
      sectionIds: decision.action === "write" ? decision.sectionIds : [],
    }));
    void compile({
      confirmed,
      intent: inRound ? (session?.intent ?? "") : (topicMap?.intent ?? ""),
    });
  };

  const written = round?.written ?? [];
  const lines = findingLines(round?.findings ?? []);
  const truncated = round?.truncated ?? false;
  const failures = round?.failures ?? [];

  return (
    <div className="flex shrink-0 flex-col gap-1.5 px-2 py-2">
      <p className="text-[11px] text-muted-foreground">
        {t("compile.counts", { write: writing.length, mention: mentioning.length })}
      </p>

      <Button size="sm" disabled={writing.length === 0 || running} onClick={start}>
        {running ? <Loader2 className="animate-spin" /> : <Hammer />}
        {writing.length === 0
          ? t("compile.none")
          : t("compile.start", { count: writing.length })}
      </Button>

      {error ? (
        // What did not happen, right where the button is that asked for it. A
        // failed round leaves no list of files, so nothing here can be mistaken
        // for work that was done.
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2">
          <p className="flex items-start gap-1 text-[11px] text-destructive">
            <AlertCircle className="mt-px size-3 shrink-0" />
            <span className="min-w-0 flex-1">{error}</span>
          </p>
          <Button
            variant="outline"
            size="sm"
            className="mt-1.5 h-6 px-2 text-[11px]"
            disabled={running}
            onClick={retry}
          >
            <RotateCcw /> {t("compile.retry")}
          </Button>
        </div>
      ) : null}

      {round ? (
        // Bounded, so a long list of files or findings cannot push the sections
        // above it out of the panel.
        <div className="flex max-h-64 flex-col gap-1 overflow-y-auto scroll-thin">
          {round.fromProject ? (
            // Read back from the project, so the list is not mistaken for the
            // round that has just been run.
            <p className="truncate text-[11px] text-muted-foreground">
              {round.intent
                ? t("compile.lastRoundIntent", { intent: round.intent })
                : t("compile.lastRound")}
            </p>
          ) : null}
          {written.length > 0 ? (
            <div className="flex flex-col gap-0.5">
              <span className="flex items-center gap-1 text-[11px] font-medium">
                <Check className="size-3 text-success" />
                {t("compile.files", { count: written.length })}
              </span>
              <ul className="flex flex-col gap-px">
                {written.map((file) => (
                  <li key={file.path}>
                    <button
                      type="button"
                      className="flex w-full items-center gap-1 truncate text-left text-[11px] text-cite hover:underline"
                      onClick={() => go({ kind: "docs", path: file.path }, { placement: "replace" })}
                    >
                      <FileText className="size-3 shrink-0" />
                      <span className="truncate">{file.path}</span>
                      <span className="shrink-0 text-muted-foreground">{file.mode}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {failures.length > 0 ? (
            <div className="flex flex-col gap-0.5">
              <span className="flex items-center gap-1 text-[11px] font-medium text-destructive">
                <AlertCircle className="size-3" />
                {t("compile.failed", { count: failures.length })}
              </span>
              <ul className="flex flex-col gap-px">
                {failures.map((item) => (
                  <li key={item.path} className="text-[11px] text-destructive">
                    {item.path}: {item.detail}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {lines.length > 0 || truncated ? (
            <details className="text-[11px]">
              <summary className="cursor-pointer text-muted-foreground">
                {t("compile.findings", { count: lines.length })}
              </summary>
              <ul className="mt-1 flex flex-col gap-px">
                {lines.map((line, index) => (
                  <li
                    key={`${line.code}-${line.path ?? ""}-${index}`}
                    className={
                      line.severity === "error"
                        ? "text-destructive"
                        : "text-muted-foreground"
                    }
                  >
                    <Badge
                      tone={line.severity === "error" ? "destructive" : "outline"}
                      className="mr-1"
                    >
                      {severityWord(line.severity, t)}
                    </Badge>
                    {findingSentence(line, t)}
                    {line.path ? (
                      <span className="text-muted-foreground"> · {line.path}</span>
                    ) : null}
                  </li>
                ))}
                {truncated ? (
                  <li className="text-muted-foreground">{t("compile.truncated")}</li>
                ) : null}
              </ul>
            </details>
          ) : (
            <p className="text-[11px] text-muted-foreground">{t("compile.clean")}</p>
          )}
        </div>
      ) : null}
    </div>
  );
}
