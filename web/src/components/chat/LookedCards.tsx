import { useTranslation } from "react-i18next";
import { Check, FileText, Globe, PenLine, Sparkles, Wrench } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ObservationPayload } from "@/lib/api/client";
import { lookedCards, type CardAction, type LookedCard } from "@/lib/chat-cards";
import { cn } from "@/lib/utils";

/**
 * What the assistant looked at, under the answer it wrote.
 *
 * The answer names pages and sources in prose; these are the same things as rows,
 * so a page it read can be opened instead of only being read about. Each row offers
 * only what exists for it — open the note, open the page, hand a search result to the
 * workbench — and a row with nothing to offer still says what it was.
 */
export function LookedCards({
  observations,
  activePath,
  onOpenPage,
  onOpenWeb,
  onSendToSources,
}: {
  observations: ObservationPayload[];
  /** The page in front of the reader, which one of these rows may already be. */
  activePath: string | null;
  onOpenPage: (path: string) => void;
  onOpenWeb: (url: string) => void;
  onSendToSources: (card: LookedCard) => void;
}) {
  const { t } = useTranslation();
  const cards = lookedCards(observations);
  if (cards.length === 0) return null;

  return (
    <section
      aria-label={t("chat.looked.title")}
      className="mt-1.5 mb-2 flex flex-col gap-px rounded-md border bg-muted/30 p-1.5"
    >
      {cards.map((card) => (
        <Card
          key={card.key}
          card={card}
          here={card.path !== null && card.path === activePath}
          onOpenPage={onOpenPage}
          onOpenWeb={onOpenWeb}
          onSendToSources={onSendToSources}
        />
      ))}
    </section>
  );
}

function Card({
  card,
  here,
  onOpenPage,
  onOpenWeb,
  onSendToSources,
}: {
  card: LookedCard;
  /** This row is the page the reader is in, so there is nowhere to go. */
  here: boolean;
  onOpenPage: (path: string) => void;
  onOpenWeb: (url: string) => void;
  onSendToSources: (card: LookedCard) => void;
}) {
  const { t } = useTranslation();
  const Icon = ICONS[card.kind];
  const primary = card.actions.length > 0 && !here ? card.actions[0] : null;
  const opens = primary !== null;
  const act = (action: CardAction) => {
    if (action === "doc" && card.path) onOpenPage(card.path);
    if (action === "browse" && card.url) onOpenWeb(card.url);
    if (action === "source") onSendToSources(card);
  };

  return (
    <div className="group flex items-center gap-1.5 rounded px-1 py-0.5 text-[11px] hover:bg-accent/40">
      <Icon
        className={cn("size-3 shrink-0", opens ? "text-cite" : "text-muted-foreground")}
      />
      <button
        type="button"
        // A card is a way in, so the name is what is clicked; a card with nowhere to
        // go is not a button at all.
        disabled={!opens}
        onClick={() => primary && act(primary)}
        className={cn(
          "min-w-0 flex-1 truncate text-left",
          opens ? "text-cite hover:underline" : "text-muted-foreground",
        )}
        title={card.path ?? card.url ?? card.label}
      >
        {card.label}
      </button>
      {/* The row the reader is already in says so, rather than being a click that
          does nothing — it is one of the things the answer was built from. */}
      {here ? (
        <span className="shrink-0 text-[10px] text-foreground/70">{t("chat.looked.reading")}</span>
      ) : null}
      {card.where ? (
        <span className="shrink-0 text-[10px] text-muted-foreground">{card.where}</span>
      ) : null}
      {card.note ? (
        <span className="shrink-0 text-[10px] text-muted-foreground">{t(card.note)}</span>
      ) : null}
      {card.actions.includes("source") ? (
        <Button variant="ghost" size="sm" className="h-5 shrink-0 px-1.5" onClick={() => act("source")}>
          {t("chat.looked.toSources")}
        </Button>
      ) : null}
    </div>
  );
}

/** One icon per kind of thing looked at. */
const ICONS = {
  page: FileText,
  web: Globe,
  check: Wrench,
  skill: Sparkles,
  write: PenLine,
  other: Check,
} as const;
