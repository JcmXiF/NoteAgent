import type { ObservationPayload } from "@/lib/api/client";
import type { TopicMap, TopicNode } from "@/lib/api/types";
import { defaultActionForNode, topicMapNodes } from "@/lib/clips";

/**
 * The gate: what the assistant wanted to write, and what the reader decides.
 *
 * Writing pages runs through one gate — the ticks the project records — so the chat
 * cannot open it and must not look as if it could. It can, however, let the reader do
 * the ticking where they are reading: the choice is the same record the plan board
 * shows, so deciding here and deciding there are the same act.
 */

/** One page waiting at the gate. */
export type GatePage = {
  slug: string;
  title: string;
  /** What the plan suggests for it, before the reader decides. */
  suggested: "write" | "mention" | "skip";
  /**
   * The sections the plan offers for it.
   *
   * Carried so a choice made here does not lose them: the plan board defaults a
   * page's sections to its outline, and a decision recorded without them would come
   * back as a page with nothing to write.
   */
  sectionIds: string[];
};

export type WriteGate = {
  /**
   * `proposed`: pages are waiting to be confirmed.
   * `refused`: the assistant tried to write and was stopped, because nothing is
   *   ticked — the gate did its job.
   * `wrote`: writing happened, through ticks that were already there.
   */
  state: "proposed" | "refused" | "wrote";
  /** What is waiting, read from the plan the project holds. */
  pages: GatePage[];
};

function hasWrite(observations: ObservationPayload[]): ObservationPayload[] {
  return observations.filter((item) => item.tool === "wiki_write");
}

/**
 * The gate's state for a turn, or null when writing never came up.
 *
 * The page list comes from the plan rather than from the observation: the plan is
 * what the gate actually reads, and the core does not send page lists with results.
 */
export function writeGate(
  observations: ObservationPayload[],
  topicMap: TopicMap | null,
): WriteGate | null {
  const writes = hasWrite(observations);
  if (writes.length === 0) return null;

  const wrote = writes.some((item) => item.wrote_wiki === true);
  const refused = !wrote && writes.some((item) => item.action === "ingest_confirmed");
  const state: WriteGate["state"] = wrote ? "wrote" : refused ? "refused" : "proposed";

  const pages: GatePage[] = topicMap
    ? topicMapNodes(topicMap).map((node: TopicNode) => ({
        slug: node.slug,
        title: node.title || node.slug,
        suggested: defaultActionForNode(node),
        sectionIds: (node.outline ?? []).map((section) => section.id),
      }))
    : [];

  return { state, pages };
}
