import { useTranslation } from "react-i18next";
import { Loader2, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { collectionsByScope, defaultActionForNode, topicMapNodes, type TopicDecision } from "@/lib/clips";
import type { TopicAction, TopicNode } from "@/lib/api/types";
import { useClipsStore } from "@/stores/clips-store";
import { useSectionCount } from "@/stores/right-sections";
import { useDeepSearchStore } from "@/stores/deep-search-store";
import { usePlanStore } from "@/stores/plan-store";

const ACTIONS: TopicAction[] = ["write", "mention", "skip"];

/**
 * Works out which pages to write, from the passages that were selected.
 *
 * The candidates come back in one streaming call. What the model suggests is
 * shown alongside each page, but the decision belongs to the user: the
 * suggestions start filled in because that is a reasonable default, and nothing
 * is written until the compile step writes what was chosen.
 */
export function PlanBoard() {
  const { t } = useTranslation();
  const collections = useClipsStore((state) => state.collections);
  const intent = useClipsStore((state) => state.intent);
  const setIntent = useClipsStore((state) => state.setIntent);
  const topicMap = useClipsStore((state) => state.topicMap);
  const decisions = useClipsStore((state) => state.decisions);
  const setDecision = useClipsStore((state) => state.setDecision);
  const running = usePlanStore((state) => state.running);
  const plan = usePlanStore((state) => state.run);

  const round = useDeepSearchStore((state) => state.session);
  const inRound = Boolean(round?.roundId) && (round?.step ?? 0) >= 4;
  /**
   * Which pages to show. A search carries its own list — what it proposed, refined
   * by its plan — so there is one list to read while it runs, and the project's own
   * plan the rest of the time.
   */
  const nodes = inRound
    ? (round?.whitelist ?? [])
    : topicMap
      ? topicMapNodes(topicMap)
      : [];
  const counts = nodes.reduce(
    (tally, node) => {
      const action = decisions[node.slug]?.action ?? defaultActionForNode(node);
      tally[action] += 1;
      return tally;
    },
    { write: 0, mention: 0, skip: 0 } as Record<TopicAction, number>,
  );

  /**
   * Which selections this pass plans: the round's own while a round is planning,
   * and the everyday ones otherwise. The same split the core makes, so the button
   * is available exactly when a planning pass would find something to plan.
   */
  const scope = collectionsByScope(collections, round?.roundId ?? "");
  const planned = inRound ? scope.mine : scope.everyday;
  const hasSelection = planned.length > 0;
  const roundOnly = !hasSelection && collections.length > 0;
  useSectionCount("plan", nodes.length);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {nodes.length > 0 ? (
          <div className="flex shrink-0 items-center justify-end gap-1 px-2 py-1">
            {counts.write > 0 ? (
              <Badge tone="success">{t("plan.summary.write", { count: counts.write })}</Badge>
            ) : null}
            {counts.mention > 0 ? (
              <Badge tone="warning">
                {t("plan.summary.mention", { count: counts.mention })}
              </Badge>
            ) : null}
            {counts.skip > 0 ? (
              <Badge>{t("plan.summary.skip", { count: counts.skip })}</Badge>
            ) : null}
          </div>
        ) : null}

      <div className="flex shrink-0 flex-col gap-1.5 px-2 pb-1.5">
        <div className="flex items-center gap-1">
          {inRound ? (
            // A round's own intent is what its planning uses, so there is nothing to
            // type; showing it keeps the panel honest about what it plans from.
            <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground">
              {round?.intent?.trim()
                ? t("plan.roundIntent", { intent: round.intent.trim() })
                : t("plan.roundNoIntent")}
            </span>
          ) : (
            <>
              <Label htmlFor="plan-intent" className="sr-only">
                {t("plan.intentLabel")}
              </Label>
              <Input
                id="plan-intent"
                className="h-7 flex-1 text-xs"
                value={intent}
                placeholder={t("plan.intentPlaceholder")}
                onChange={(event) => setIntent(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && hasSelection && !running) void plan();
                }}
              />
            </>
          )}
          <Button size="sm" disabled={!hasSelection || running} onClick={() => void plan()}>
            {running ? <Loader2 className="animate-spin" /> : <Sparkles />}
            {nodes.length > 0 ? t("plan.again") : t("plan.start")}
          </Button>
        </div>
        <p className="text-[11px] text-muted-foreground">
          {hasSelection
            ? t(inRound ? "plan.roundHint" : "plan.hint")
            : roundOnly
              ? t("plan.roundOnly")
              : t("plan.needSelection")}
        </p>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto border-t scroll-thin">
        {nodes.length === 0 ? (
          <div className="flex flex-col gap-1 p-2">
            <p className="text-[11px] font-medium">{t("plan.empty.title")}</p>
            <p className="text-[11px] text-muted-foreground">{t("plan.empty.body")}</p>
          </div>
        ) : (
          <ul className="flex flex-col gap-px py-1">
            {nodes.map((node) => (
              <li key={node.slug}>
                <PlanCard
                  node={node}
                  decision={
                    decisions[node.slug] ?? {
                      slug: node.slug,
                      action: defaultActionForNode(node),
                      sectionIds: (node.outline ?? []).map((section) => section.id),
                    }
                  }
                  onChange={(patch) => setDecision(node.slug, patch)}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function PlanCard({
  node,
  decision,
  onChange,
}: {
  node: TopicNode;
  decision: TopicDecision;
  onChange: (patch: Partial<TopicDecision>) => void;
}) {
  const { t } = useTranslation();
  const suggestion = node.coverage;
  const relation = node.relationToPrimary;
  const writing = decision.action === "write";
  const outline = node.outline ?? [];
  const collectionIds = node.collectionIds ?? [];

  return (
    <div className={cn("flex flex-col gap-1 px-2 py-2", writing ? "bg-success/5" : "")}>
      <div className="flex items-start gap-1.5">
        <span className="min-w-0 flex-1 text-[11px] font-medium">{node.title}</span>
        {node.unlisted ? (
          // Reading is how material turns up, so a page the list did not have can
          // still be the right page to write. It is marked rather than dropped, so
          // the reader can see it was not part of the plan they approved earlier.
          <Badge tone="cite">{t("plan.unlisted")}</Badge>
        ) : null}
        {suggestion ? (
          <Badge
            title={t("plan.suggestion.label")}
            tone={
              suggestion === "write"
                ? "success"
                : suggestion === "mention"
                  ? "warning"
                  : "neutral"
            }
          >
            {t(`plan.suggestion.${suggestion}`)}
          </Badge>
        ) : null}
      </div>

      {relation && relation !== "primary" ? (
        <p className="text-[10px] text-muted-foreground">
          {t(`plan.relation.${relation}`, { defaultValue: relation })}
        </p>
      ) : null}

      {node.wikiPath ? (
        <p className="truncate font-mono text-[10px] text-muted-foreground">
          {node.wikiPath}
        </p>
      ) : null}

      {node.evidence ? (
        <p className="text-[10px] text-muted-foreground">{node.evidence}</p>
      ) : null}

      {collectionIds.length > 0 ? (
        <p className="text-[10px] text-muted-foreground">
          {t("plan.fromPages", { count: collectionIds.length })}
        </p>
      ) : null}

      <fieldset
        className="flex flex-wrap items-center gap-x-2 gap-y-0.5"
        aria-label={t("plan.action.legend")}
      >
        {ACTIONS.map((action) => (
          <label key={action} className="flex items-center gap-1 text-[11px]">
            <input
              type="radio"
              className="size-3"
              name={`plan-action-${node.slug}`}
              checked={decision.action === action}
              onChange={() => onChange({ action })}
            />
            {t(`plan.action.${action}`)}
          </label>
        ))}
      </fieldset>

      {writing && outline.length > 0 ? (
        <div className="flex flex-col gap-0.5 rounded-md bg-muted/40 p-1.5">
          <span className="text-[10px] font-medium text-muted-foreground">
            {t("plan.sections")}
          </span>
          {outline.map((section) => {
            const checked = decision.sectionIds.includes(section.id);
            return (
              <label key={section.id} className="flex items-start gap-1 text-[11px]">
                <input
                  type="checkbox"
                  className="mt-0.5 size-3 shrink-0"
                  checked={checked}
                  onChange={() =>
                    onChange({
                      sectionIds: checked
                        ? decision.sectionIds.filter((id) => id !== section.id)
                        : [...decision.sectionIds, section.id],
                    })
                  }
                />
                <span className="min-w-0 truncate">{section.title}</span>
              </label>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
