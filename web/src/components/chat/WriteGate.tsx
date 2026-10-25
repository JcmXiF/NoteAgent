import { useTranslation } from "react-i18next";
import { CheckCircle2, ShieldQuestion } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ObservationPayload } from "@/lib/api/client";
import type { TopicAction, TopicMap } from "@/lib/api/types";
import { writeGate, type GatePage } from "@/lib/chat-gate";
import { useClipsStore } from "@/stores/clips-store";
import { cn } from "@/lib/utils";

const ACTIONS: TopicAction[] = ["write", "mention", "skip"];

/**
 * Where writing is decided, said where the reader is reading.
 *
 * Pages are written only from the ticks the project records, so the assistant cannot
 * write on its own. The ticks themselves are the reader's, and they can be made here:
 * this writes the very same record the plan board shows, section by section included,
 * so choosing here and choosing there are one act rather than two places to keep in
 * step. The board is still where the details are read and where compiling happens.
 */
export function WriteGate({
  observations,
  topicMap,
  onGoCompile,
  onOpenSources,
}: {
  observations: ObservationPayload[];
  topicMap: TopicMap | null;
  /** Opens the right panel's compile section, which is the step after deciding. */
  onGoCompile: () => void;
  /** Opens the workbench, for when there is nothing selected to plan from yet. */
  onOpenSources: () => void;
}) {
  const { t } = useTranslation();
  const decisions = useClipsStore((state) => state.decisions);
  const setDecision = useClipsStore((state) => state.setDecision);
  const gate = writeGate(observations, topicMap);
  if (!gate) return null;

  const wrote = gate.state === "wrote";
  // Nothing waiting and nothing written means the assistant was stopped before it had
  // anything to propose — which needs a different sentence, and a different place to go.
  const empty = !wrote && gate.pages.length === 0;

  return (
    <section
      aria-label={t("chat.gate.title")}
      className={cn(
        "mt-1.5 mb-2 flex flex-col gap-1.5 rounded-md border p-2",
        wrote ? "border-success/40 bg-success/5" : "border-warning/40 bg-warning/5",
      )}
    >
      <div className="flex items-start gap-1.5">
        {wrote ? (
          <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-success" />
        ) : (
          <ShieldQuestion className="mt-0.5 size-3.5 shrink-0 text-warning" />
        )}
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-medium">
            {t(wrote ? "chat.gate.wroteTitle" : "chat.gate.waitingTitle")}
          </p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            {t(
              wrote
                ? "chat.gate.wroteBody"
                : empty
                  ? "chat.gate.nothingBody"
                  : "chat.gate.waitingBody",
            )}
          </p>
        </div>
      </div>

      {gate.pages.length > 0 && !wrote ? (
        <ul className="flex flex-col gap-1 pl-5">
          {gate.pages.map((page: GatePage) => (
            <li key={page.slug} className="flex flex-col gap-0.5">
              <span className="min-w-0 truncate text-[11px] font-medium">{page.title}</span>
              <fieldset
                // The same words and the same record as the plan board: one decision,
                // asked for in two places.
                aria-label={t("plan.action.legend")}
                className="flex flex-wrap items-center gap-x-2 gap-y-0.5"
              >
                {ACTIONS.map((action) => (
                  <label key={action} className="flex items-center gap-1 text-[11px]">
                    <input
                      type="radio"
                      className="size-3"
                      name={`gate-action-${page.slug}`}
                      checked={(decisions[page.slug]?.action ?? page.suggested) === action}
                      onChange={() =>
                        // Sections come along: a decision recorded without them would
                        // come back as a page with nothing to write.
                        setDecision(page.slug, { action, sectionIds: page.sectionIds })
                      }
                    />
                    {t(`plan.action.${action}`)}
                  </label>
                ))}
              </fieldset>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="flex items-center gap-1.5 pl-5">
        <Button size="sm" variant={wrote ? "outline" : "default"} onClick={onGoCompile}>
          {t(wrote ? "chat.gate.seeWritten" : "chat.gate.goCompile")}
        </Button>
        {empty ? (
          <Button size="sm" variant="ghost" onClick={onOpenSources}>
            {t("chat.gate.openSources")}
          </Button>
        ) : null}
      </div>
    </section>
  );
}
