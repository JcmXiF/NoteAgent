import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";
import type { DeepSearchPage } from "@/lib/api/types";

/**
 * Why the page is wanted, in the reader's words.
 *
 * A reason the core worked out travels as a code and its figures, because the core
 * does not know what language the reader is using; the model's own sentence arrives
 * already in that language and is shown as it is. A code this version has never heard
 * of shows nothing: the figures are not a sentence, and a raw key is worse than quiet.
 */
function explainPage(
  page: DeepSearchPage,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  if (!page.reason) return page.plan ?? "";
  return t(`deepSearch.reason.${page.reason}`, {
    ...page.reasonArgs,
    defaultValue: "",
  });
}

/**
 * The pages a round wants to touch, with the reason it wants them.
 *
 * The same proposal is drawn on the graph above — a node to add or to revise — and
 * ticking here and clicking the node do the same thing. Both are offered because
 * they answer different questions: the graph says where the page sits among the
 * others, this says why the round wants it, and a reader wants one or the other.
 *
 * The title is separate from the tick for the same reason it is elsewhere: the tick
 * is a decision, the title is how the page is found on the graph.
 */
export function PagePickList({
  pages,
  selectedSlug,
  onToggle,
  onPick,
}: {
  pages: DeepSearchPage[];
  /** The page currently picked out on the graph, if any. */
  selectedSlug: string | null;
  onToggle: (slug: string, accepted: boolean) => void;
  onPick: (slug: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <ul className="flex flex-col pb-1">
      {pages.map((page) => {
        const reason = explainPage(page, t);
        return (
          <li key={page.slug}>
            <div
              className={cn(
                "flex items-start gap-1.5 px-2 py-1 hover:bg-accent/40",
                page.slug === selectedSlug && "bg-accent/60",
              )}
            >
              <Checkbox
                className="mt-0.5"
                checked={page.accepted}
                onCheckedChange={(checked) => onToggle(page.slug, checked)}
                aria-label={t("deepSearch.pages.include")}
              />
              <button
                type="button"
                className="min-w-0 flex-1 text-left"
                onClick={() => onPick(page.slug)}
              >
                <span className="flex items-center gap-1.5">
                  <Badge tone="outline" className="shrink-0">
                    {t(page.action === "revise" ? "deepSearch.pages.revise" : "deepSearch.pages.add")}
                  </Badge>
                  <span className="min-w-0 truncate text-[11px] font-medium">{page.title}</span>
                </span>
                {reason ? (
                  <span className="mt-0.5 block text-[10px] leading-snug text-muted-foreground">
                    {reason}
                  </span>
                ) : null}
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
