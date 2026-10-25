import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Loader2, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MAX_FETCH_CARDS } from "@/lib/api/acquire";
import { atLimit, selectedCards, toggleSelected } from "@/lib/search-cards";
import { cn } from "@/lib/utils";
import { useAcquireStore } from "@/stores/acquire-store";
import { useNavStore } from "@/stores/nav-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * Searching the web, and choosing which results to keep.
 *
 * The results are candidates, not sources: nothing is read until the reader ticks
 * what they want and asks for it. What comes back lands in Sources, where the
 * passages to write from are chosen, so the panel says so rather than leaving the
 * next step to be guessed.
 */
export function BrowseSearch() {
  const { t } = useTranslation();
  const project = useWorkspaceStore((state) => state.current);
  const results = useAcquireStore((state) => state.results);
  const query = useAcquireStore((state) => state.query);
  const busy = useAcquireStore((state) => state.busy);
  const error = useAcquireStore((state) => state.error);
  const lastFetch = useAcquireStore((state) => state.lastFetch);
  const search = useAcquireStore((state) => state.search);
  const fetchCards = useAcquireStore((state) => state.fetchCards);
  const go = useNavStore((state) => state.go);

  const [draft, setDraft] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [full, setFull] = useState(false);

  const tick = (url: string) => {
    const next = toggleSelected(selected, url);
    setSelected(next.selected);
    // Said while it is happening, not only when the tick is refused.
    setFull(next.refused || atLimit(next.selected));
  };

  const run = async () => {
    const text = draft.trim();
    if (!text) return;
    setSelected([]);
    setFull(false);
    await search(text);
  };

  const chosen = selectedCards(results, selected);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <form
        className="shrink-0 px-2 pt-1.5 pb-1"
        onSubmit={(event) => {
          event.preventDefault();
          void run();
        }}
      >
        <div className="flex items-center gap-1">
          <Input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={t("browse.search.placeholder")}
            aria-label={t("browse.search.label")}
            className="h-7 text-[11px]"
            spellCheck={false}
            disabled={!project}
          />
          <Button
            type="submit"
            size="icon-xs"
            variant="outline"
            disabled={busy || !project || !draft.trim()}
            aria-label={t("browse.search.run")}
            title={t("browse.search.run")}
          >
            {busy ? <Loader2 className="animate-spin" /> : <Search />}
          </Button>
        </div>
      </form>

      {!project ? (
        <p className="shrink-0 px-2 pb-1 text-[11px] leading-relaxed text-muted-foreground">
          {t("project.needed")}
        </p>
      ) : null}

      {project && error ? (
        <p className="shrink-0 px-2 pb-1 text-[11px] text-destructive">{error}</p>
      ) : null}

      {lastFetch && lastFetch.count > 0 ? (
        <div className="shrink-0 border-y bg-muted/40 px-2 py-1.5">
          <p className="text-[11px]">{t("browse.fetch.landed", { count: lastFetch.count })}</p>
          <Button
            variant="outline"
            size="sm"
            className="mt-1 w-full"
            onClick={() =>
              go(
                { kind: "sources", sourceKey: lastFetch.firstUrl ?? undefined },
                { placement: "beside" },
              )
            }
          >
            {t("browse.fetch.goSelect")}
          </Button>
        </div>
      ) : null}

      {results.length === 0 ? (
        <p className="p-3 text-[11px] leading-relaxed text-muted-foreground">
          {query ? t("browse.search.none") : t("browse.search.empty")}
        </p>
      ) : (
        <>
          <ul className="scroll-thin min-h-0 flex-1 overflow-y-auto">
            {results.map((card) => {
              const ticked = selected.includes(card.url);
              return (
                <li key={card.url}>
                  <button
                    type="button"
                    className={cn(
                      "flex w-full items-start gap-1.5 px-2 py-1.5 text-left",
                      ticked ? "bg-accent" : "hover:bg-accent/40",
                    )}
                    aria-pressed={ticked}
                    onClick={() => tick(card.url)}
                  >
                    <span
                      className={cn(
                        "mt-0.5 flex size-3.5 shrink-0 items-center justify-center rounded border",
                        ticked ? "border-primary bg-primary text-primary-foreground" : "border-input",
                      )}
                    >
                      {ticked ? <Check className="size-2.5" /> : null}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[11px] font-medium">
                        {card.title || card.url}
                      </span>
                      <span className="block truncate text-[10px] text-muted-foreground">
                        {card.site || card.url}
                      </span>
                      {card.snippet ? (
                        <span className="mt-0.5 line-clamp-2 block text-[10px] leading-snug text-muted-foreground">
                          {card.snippet}
                        </span>
                      ) : null}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>

          <div className="shrink-0 border-t px-2 py-1.5">
            <Button
              size="sm"
              className="w-full"
              disabled={busy || !project || chosen.length === 0}
              onClick={() => void fetchCards(chosen.map((card) => card.url))}
            >
              {t("browse.fetch.run", { count: chosen.length })}
            </Button>
            <p className="mt-1 text-[10px] text-muted-foreground">
              {full
                ? t("browse.search.atLimit", { max: MAX_FETCH_CARDS })
                : t("browse.search.hint", { max: MAX_FETCH_CARDS })}
            </p>
          </div>
        </>
      )}
    </div>
  );
}
