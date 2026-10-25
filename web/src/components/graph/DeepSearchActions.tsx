import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { IconTip } from "@/components/ui/tooltip";
import { useDeepSearchStore } from "@/stores/deep-search-store";
import { useLayoutStore } from "@/stores/layout-store";
import { useNavStore } from "@/stores/nav-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { DeepSearchActBody } from "@/lib/api/deep-search";
import type { DeepSearchSession } from "@/lib/api/types";

/**
 * What can be done to a round, at the step it is on.
 *
 * Kept apart from the panel that draws the steps and their content, because it is
 * the one part that changes with the step and needs no room: the panel is about
 * what the round has found, this is about what to do with it next.
 *
 * `selected` is how many selections the round has gathered — the one thing a round
 * cannot say about itself, since selections are made where material is gathered
 * rather than in the round.
 */
export function DeepSearchActions({
  round,
  selected,
  pickedCards,
  unwritten,
  running,
}: {
  round: DeepSearchSession;
  selected: number;
  /** Card addresses ticked across the round, which is what "fetch" acts on. */
  pickedCards: number;
  /** Selections from this round that were never written into a wiki page. */
  unwritten: number;
  running: boolean;
}) {
  const { t } = useTranslation();
  const project = useWorkspaceStore((state) => state.current);
  const act = useDeepSearchStore((state) => state.act);
  const edit = useDeepSearchStore((state) => state.edit);
  const fetchCards = useDeepSearchStore((state) => state.fetchCards);
  const setRightOpen = useLayoutStore((state) => state.setRightOpen);
  const go = useNavStore((state) => state.go);

  /** Runs a step of the round, as a job. */
  const run = (body: DeepSearchActBody) => {
    if (project) void act(project, body);
  };
  /** Makes a small edit to the round: ticking a box, taking all of them. */
  const touch = (body: DeepSearchActBody) => {
    if (project) void edit(project, body);
  };

  const chewBlockStep = round.mode === "chew" && round.step === 3;
  const pageStep = round.mode === "explore" && round.step === 2;
  const gatherStep = round.mode === "explore" && round.step === 3;
  const written = round.ingestedIds.length > 0;
  const allTaken =
    round.proposedBlocks.length > 0 && round.proposedBlocks.every((item) => item.accepted);

  return (
    <div className="flex flex-wrap items-center gap-1.5 border-t px-2 py-1.5">
      {chewBlockStep ? (
        <>
          <Button size="sm" disabled={running} onClick={() => run({ action: "proposeBlocks" })}>
            {t(round.proposedBlocks.length ? "deepSearch.blocks.again" : "deepSearch.blocks.find")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={running || round.proposedBlocks.length === 0}
            onClick={() => touch({ action: "selectAllBlocks", accepted: !allTaken })}
          >
            {t(allTaken ? "deepSearch.blocks.none" : "deepSearch.blocks.all")}
          </Button>
          <Button
            size="sm"
            disabled={running || !round.proposedBlocks.some((item) => item.accepted)}
            onClick={() => run({ action: "acceptBlocks" })}
          >
            {t("deepSearch.blocks.take")}
          </Button>
        </>
      ) : null}

      {pageStep ? (
        <>
          <Button size="sm" disabled={running} onClick={() => run({ action: "proposeWhitelist" })}>
            {t(round.whitelist.length ? "deepSearch.pages.again" : "deepSearch.pages.find")}
          </Button>
          <Button
            size="sm"
            disabled={running || !round.whitelist.some((item) => item.accepted)}
            onClick={() => run({ action: "acceptWhitelist" })}
          >
            {t("deepSearch.pages.take")}
          </Button>
        </>
      ) : null}

      {gatherStep ? (
        <>
          <Button
            size="sm"
            disabled={running || pickedCards === 0}
            onClick={() => project && void fetchCards(project)}
          >
            {t("deepSearch.cards.fetch")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={running}
            onClick={() => go({ kind: "sources" }, { placement: "beside" })}
          >
            {t("deepSearch.gather.go")}
          </Button>
          <Button
            size="sm"
            disabled={running || selected === 0}
            onClick={() => run({ action: "enterPlan" })}
          >
            {t("deepSearch.plan.start")}
          </Button>
        </>
      ) : null}

      {round.step >= 4 ? (
        <>
          <Button size="sm" variant="outline" disabled={running} onClick={() => setRightOpen(true)}>
            {t("deepSearch.plan.open")}
          </Button>
          {unwritten === 0 ? (
            <Button size="sm" disabled={running} onClick={() => run({ action: "end" })}>
              {t("deepSearch.end.now")}
            </Button>
          ) : (
            // Ending while material was never written is a choice the core insists
            // on: those passages are either kept for later or dropped.
            <>
              <Button
                size="sm"
                variant="outline"
                disabled={running}
                onClick={() => run({ action: "end", leftover: "discard" })}
              >
                {t("deepSearch.end.drop")}
              </Button>
              <Button
                size="sm"
                disabled={running}
                onClick={() => run({ action: "end", leftover: "merge" })}
              >
                {t("deepSearch.end.keep")}
              </Button>
            </>
          )}
        </>
      ) : null}

      {round.step >= 3 ? (
        // Offered while the round can still be taken back. Once pages have been
        // written it stays visible but cannot be used, because the pages are on
        // disk: hiding it would leave the reader wondering where it went.
        <IconTip label={t(written ? "deepSearch.startOver.written" : "deepSearch.startOver.hint")}>
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto"
            disabled={running || written}
            onClick={() => run({ action: "restart" })}
          >
            {t("deepSearch.startOver")}
          </Button>
        </IconTip>
      ) : null}
    </div>
  );
}
