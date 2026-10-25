import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronUp } from "lucide-react";
import { BlockPickList } from "@/components/graph/BlockPickList";
import { CardPickList } from "@/components/graph/CardPickList";
import { DeepSearchActions } from "@/components/graph/DeepSearchActions";
import { PagePickList } from "@/components/graph/PagePickList";
import { StepBar } from "@/components/graph/StepBar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { IconTip } from "@/components/ui/tooltip";
import {
  hasWritten,
  pendingActionOf,
  rewindAction,
  roundSteps,
  roundSummary,
  stepLabels,
  type PendingAction,
} from "@/lib/deep-search";
import { parseSourceLocator } from "@/lib/wiki-locators";
import { buildOverlay, cardsToShow, pickedCardCount } from "@/lib/graph-overlay";
import { useClipsStore } from "@/stores/clips-store";
import { useDeepSearchRunning, useDeepSearchStore } from "@/stores/deep-search-store";
import { useDocsStore } from "@/stores/docs-store";
import { useGraphStore } from "@/stores/graph-store";
import type { PaneSlot } from "@/stores/layout-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { DeepSearchSession } from "@/lib/api/types";
import i18n from "@/i18n";

/**
 * A deep search round, under the graph it works on.
 *
 * A round is a sequence of steps, and this is where the reader drives it: the step
 * bar says where it has got to, the body is the step's own work, and the actions
 * move it on. It belongs under the graph rather than in the sidebar because its
 * material — the passages it finds — needs room to be read, and because the graph
 * stays visible above it: a round is a reading of that graph.
 *
 * The panel is always rendered, even with no round, so the canvas above it is
 * measured against a size that does not come and go.
 */
export function DeepSearchPanel({ slot }: { slot: PaneSlot }) {
  const { t } = useTranslation();
  const project = useWorkspaceStore((state) => state.current);
  const session = useDeepSearchStore((state) => state.session);
  const status = useDeepSearchStore((state) => state.status);
  const error = useDeepSearchStore((state) => state.error);
  const act = useDeepSearchStore((state) => state.act);
  const running = useDeepSearchRunning();

  const collections = useClipsStore((state) => state.collections);
  const files = useDocsStore((state) => state.files);
  const nodes = useGraphStore((state) => state.nodes);
  const selectedId = useGraphStore((state) => state.selectedId);

  const [intent, setIntent] = useState("");
  const [expanded, setExpanded] = useState(true);

  const round = session?.roundId ? session : null;
  const written = hasWritten(round);
  const mine = round ? collections.filter((item) => item.roundId === round.roundId) : [];
  const unwritten = round ? mine.filter((item) => !round.ingestedIds.includes(item.id)).length : 0;
  const next = pendingActionOf(round, mine.length);
  const steps = round
    ? roundSteps(round.mode, round.step, written).map((step) => ({
        id: step.id,
        label: t(step.labelKey),
        state: step.state,
      }))
    : [];
  const seed = selectedId ? nodes.find((node) => node.id === selectedId) : undefined;
  const summary = round ? roundSummary(round) : [];
  // What the round found, grouped by page — or only the page picked out, since a
  // round can carry more material than fits in one panel.
  const overlay = useMemo(() => buildOverlay(round), [round]);
  const cardGroups = useMemo(() => cardsToShow(overlay, selectedId), [overlay, selectedId]);

  const start = (mode: "chew" | "explore") => {
    if (!project) return;
    void act(
      project,
      {
        action: "start",
        mode,
        intent: intent.trim(),
        ...(seed ? { seed: { kind: "graph-node", slug: seed.id, title: seed.title } } : {}),
      },
      mode === "chew" ? i18n.t("deepSearch.title.chew") : i18n.t("deepSearch.title.explore"),
    );
  };

  /** Opens the passage's file beside the graph, with that passage picked out. */
  const openPassage = (locator: string) => {
    const parsed = parseSourceLocator(locator);
    if (parsed) useDocsStore.getState().followDocument(parsed.path, parsed.blockId, slot);
  };

  const rewind = (stepId: string) => {
    if (!round || !project) return;
    const shown = roundSteps(round.mode, round.step, written);
    const body = rewindAction(
      round.mode,
      Number(stepId),
      shown.findIndex((item) => item.state === "current") + 1,
      stepLabels(round.mode).length,
      written,
    );
    if (body) void act(project, body);
  };

  return (
    <section className="shrink-0 border-t bg-sidebar/60">
      <header className="flex h-7 items-center gap-2 px-2">
        {round ? (
          <StepBar
            steps={steps}
            onRewind={rewind}
            // Nothing is taken back while something is running, and nothing is
            // taken back once pages have been written: the pages are on disk. Said
            // through the same switch, so the steps do not look like controls that
            // quietly do nothing.
            disabled={running || written}
            label={t("deepSearch.steps")}
            rewindHint={(step) => t("deepSearch.rewindHint", { step: step.label })}
          />
        ) : (
          <span className="text-[11px] font-medium">{t("deepSearch.title.explore")}</span>
        )}
        <div className="ml-auto flex items-center gap-1">
          {summary.length > 0 ? (
            <span className="max-w-72 truncate text-[10px] text-muted-foreground">
              {summary.map((item) => t(item.key, item.params)).join(" · ")}
            </span>
          ) : null}
          <IconTip label={t(expanded ? "deepSearch.collapse" : "deepSearch.expand")}>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => setExpanded((open) => !open)}
              aria-expanded={expanded}
            >
              {expanded ? <ChevronDown /> : <ChevronUp />}
            </Button>
          </IconTip>
        </div>
      </header>

      {expanded ? (
        <div className="max-h-52 overflow-y-auto border-t scroll-thin">
          {error && round ? (
            // The round is still there, so this is something that happened to it
            // rather than the state of the whole panel.
            <p className="px-2 pt-2 text-[11px] text-destructive">{error}</p>
          ) : null}

          {!round ? (
            status === "error" ? (
              <p className="p-2 text-[11px] text-destructive">
                {error ?? t("deepSearch.loadFailed")}
              </p>
            ) : (
              <div className="flex flex-col gap-2 p-2">
                <div className="flex items-center gap-1.5">
                  <Input
                    value={intent}
                    onChange={(event) => setIntent(event.target.value)}
                    maxLength={200}
                    placeholder={t("deepSearch.intent.placeholder")}
                    aria-label={t("deepSearch.intent.label")}
                    className="h-7 text-[11px]"
                  />
                  <Button size="sm" disabled={running || !project} onClick={() => start("chew")}>
                    {t("deepSearch.start.chew")}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={running || !project}
                    onClick={() => start("explore")}
                  >
                    {t("deepSearch.start.explore")}
                  </Button>
                </div>
                <Potential session={session} />
                <p className="text-[10px] text-muted-foreground">
                  {seed
                    ? t("deepSearch.seed.node", { title: seed.title })
                    : t("deepSearch.seed.none")}
                </p>
              </div>
            )
          ) : round.mode === "chew" && round.step === 3 ? (
            round.proposedBlocks.length === 0 ? (
              <p className="p-2 text-[11px] text-muted-foreground">{t("deepSearch.blocks.empty")}</p>
            ) : (
              <BlockPickList
                blocks={round.proposedBlocks}
                files={files}
                onToggle={(locator, accepted) => {
                  if (project) {
                    void useDeepSearchStore
                      .getState()
                      .edit(project, { action: "toggleBlock", locator, accepted });
                  }
                }}
                onOpen={openPassage}
              />
            )
          ) : round.mode === "explore" && round.step === 3 ? (
            cardGroups.length === 0 ? (
              <p className="p-2 text-[11px] text-muted-foreground">{t("deepSearch.cards.empty")}</p>
            ) : (
              <CardPickList
                groups={cardGroups}
                titleOf={(slug) => overlay.pages.get(slug)?.title ?? slug}
                onToggle={(url, picked) => {
                  if (project) {
                    void useDeepSearchStore
                      .getState()
                      .edit(project, { action: "toggleCard", url, picked });
                  }
                }}
              />
            )
          ) : round.mode === "explore" && round.step === 2 && round.whitelist.length > 0 ? (
            <PagePickList
              pages={round.whitelist}
              selectedSlug={selectedId}
              onToggle={(slug, accepted) => {
                if (project) {
                  void useDeepSearchStore
                    .getState()
                    .edit(project, { action: "togglePage", slug, accepted });
                }
              }}
              onPick={(slug) => useGraphStore.getState().select(slug)}
            />
          ) : (
            <p className="p-2 text-[11px] text-muted-foreground">{t(hintKey(round, next))}</p>
          )}

          {round ? (
            <DeepSearchActions
              round={round}
              selected={mine.length}
              pickedCards={pickedCardCount(overlay)}
              unwritten={unwritten}
              running={running}
            />
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/** What a step is for, when its work is not a list to tick through. */
function hintKey(round: { mode: string; step: number }, next: PendingAction): string {
  if (round.mode === "explore" && round.step === 2) return "deepSearch.hint.pages";
  if (round.mode === "explore" && round.step === 3) {
    return next.kind === "gather" ? "deepSearch.hint.gather" : "deepSearch.hint.plan";
  }
  if (round.step >= 4) return "deepSearch.hint.write";
  return "deepSearch.hint.none";
}

/** How much material is left unread, so a reader can judge whether to start. */
function Potential({ session }: { session: DeepSearchSession | null }) {
  const { t } = useTranslation();
  const potential = session?.potential;
  if (!potential) return null;
  if (potential.chewDisabled) {
    return <p className="text-[10px] text-muted-foreground">{t("deepSearch.potential.none")}</p>;
  }
  return (
    <p className="text-[10px] text-muted-foreground">
      {t("deepSearch.potential.some", {
        files: potential.filesWithRemainder,
        total: potential.sourceFiles,
        blocks: potential.remainderBlocks,
      })}
    </p>
  );
}
