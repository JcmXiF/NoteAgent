import { useTranslation } from "react-i18next";
import { Checkbox } from "@/components/ui/checkbox";
import type { DeepSearchCard } from "@/lib/api/types";

/**
 * Material a round found, listed under the page it was found for.
 *
 * A card is not a source yet: it is a claim that something worth reading is at
 * that address. Ticking one says "bring this in"; bringing it in is a separate
 * step, because fetching is work the reader may want to do in one go after
 * choosing across all the pages.
 *
 * The address is shown as its site rather than in full: it is there to say where a
 * card comes from, and the title above it is what a reader recognises.
 */
export function CardPickList({
  groups,
  titleOf,
  onToggle,
}: {
  groups: { slug: string; cards: DeepSearchCard[] }[];
  /** The page a group belongs to, for its heading. */
  titleOf: (slug: string) => string;
  onToggle: (url: string, picked: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <ul className="flex flex-col pb-1">
      {groups.map((group) => (
        <li key={group.slug || "loose"}>
          <div className="flex items-baseline gap-1.5 px-2 pt-2 pb-0.5">
            <span className="min-w-0 truncate text-[11px] font-medium">
              {group.slug ? titleOf(group.slug) : t("deepSearch.cards.loose")}
            </span>
            <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
              {t("deepSearch.cards.taken", {
                picked: group.cards.filter((card) => card.picked).length,
                total: group.cards.length,
              })}
            </span>
          </div>
          <ul>
            {group.cards.map((card) => (
              <li key={card.url}>
                <div className="flex items-start gap-1.5 px-2 py-1 hover:bg-accent/40">
                  <Checkbox
                    className="mt-0.5"
                    checked={card.picked}
                    onCheckedChange={(checked) => onToggle(card.url, checked)}
                    aria-label={t("deepSearch.cards.include")}
                  />
                  <span className="min-w-0 flex-1 text-[11px] leading-snug">
                    <span className="block truncate">{card.title || card.url}</span>
                    <span className="block truncate text-[10px] text-muted-foreground">
                      {card.site || card.snippet}
                    </span>
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}
