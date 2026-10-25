import { getJson, postSse, projectPath, type SseHandlers } from "@/lib/api/client";
import { currentLanguage } from "@/i18n";
import type { DeepSearchSession } from "@/lib/api/types";

/**
 * One deep search round. The state machine lives in Python
 * (`.noteagent/deep-search.json`); callers only pick an action.
 *
 * An action can take as long as a model call. `postDeepSearchAct` waits for it and
 * answers with the session; `streamDeepSearchAct` says what the round is doing
 * while it does it, and can be abandoned. Both apply the same action — the stream
 * is the one to use when a reader is waiting.
 */
export function fetchDeepSearch(project: string): Promise<DeepSearchSession> {
  return getJson<DeepSearchSession>(projectPath(project, "deep-search"));
}

type DeepSearchAction =
  | "start"
  | "proposeWhitelist"
  | "acceptWhitelist"
  | "proposeBlocks"
  | "selectAllBlocks"
  | "acceptBlocks"
  | "enterPlan"
  | "togglePage"
  | "toggleBlock"
  | "toggleCard"
  | "redo"
  | "restart"
  | "end"
  | "clear"
  | "markIngested";

/**
 * What one action of a round is asked with.
 *
 * `seed` is what the round starts from, either a stored file or a page on the
 * graph. `useLlm: false` keeps the model out of it, which is what tests and probes
 * do so they can check the round without one.
 */
export type DeepSearchActBody = {
  action: DeepSearchAction;
  mode?: string;
  seed?: { kind?: string; path?: string; slug?: string; title?: string };
  intent?: string;
  step?: number;
  slug?: string;
  locator?: string;
  accepted?: boolean;
  url?: string;
  picked?: boolean;
  collectionIds?: string[];
  /** What to do with material that was not written, when ending the round. */
  leftover?: "merge" | "discard" | "";
  useLlm?: boolean;
};

/**
 * Takes one action while it happens.
 *
 * The round reports which step it is on, and the caller can leave: aborting stops
 * the reader, and the core notices before its next model round. Nothing is written
 * until the action finishes, so leaving part-way leaves the round as it was.
 *
 * A blocking form of the same route exists for scripts, `deep-search/act`; the app
 * uses this one, because a round is something a reader waits on.
 */
export function streamDeepSearchAct(
  project: string,
  body: DeepSearchActBody,
  handlers: SseHandlers = {},
): Promise<DeepSearchSession> {
  return postSse<DeepSearchSession>(
    projectPath(project, "deep-search/act-stream"),
    {
      ...body,
      // A round's page list carries a reason per page. The names follow the material;
      // the reasons are read by whoever is looking at the screen, so the core is told
      // which language that is.
      language: currentLanguage(),
    },
    handlers,
  );
}
