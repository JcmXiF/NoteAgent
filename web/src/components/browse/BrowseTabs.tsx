import { useTranslation } from "react-i18next";
import { Globe, Loader2, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { IconTip } from "@/components/ui/tooltip";
import { tabLabel } from "@/lib/browse";
import { cn } from "@/lib/utils";
import { useBrowseStore } from "@/stores/browse-store";

/**
 * The pages open in the built-in browser, as one line above the page.
 *
 * A strip rather than a list down the side, because the side is where searching
 * happens: results need the width, and a tab only needs to say what it is.
 */
export function BrowseTabs() {
  const { t } = useTranslation();
  const tabs = useBrowseStore((state) => state.guest.tabs);
  const activeId = useBrowseStore((state) => state.guest.activeId);
  const available = useBrowseStore((state) => state.available);
  const switchTab = useBrowseStore((state) => state.switchTab);
  const closeTab = useBrowseStore((state) => state.closeTab);
  const newTab = useBrowseStore((state) => state.newTab);

  const nothingYet = !available || tabs.length === 0;

  return (
    <div className="scroll-thin flex shrink-0 items-center gap-1 overflow-x-auto border-b px-2 py-1">
      {nothingYet ? (
        <span className="text-[11px] text-muted-foreground">{t("browse.tabs.empty")}</span>
      ) : (
        tabs.map((tab) => {
          const active = tab.id === activeId;
          return (
            <span
              key={tab.id}
              className={cn(
                "group flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5",
                active ? "bg-accent" : "hover:bg-accent/40",
              )}
            >
              <button
                type="button"
                className="flex max-w-40 items-center gap-1"
                title={tab.url || t("browse.newTabTitle")}
                onClick={() => void switchTab(tab.id)}
              >
                {tab.loading ? (
                  <Loader2 className="size-3 shrink-0 animate-spin" />
                ) : (
                  <Globe className="size-3 shrink-0 text-muted-foreground" />
                )}
                <span
                  className={cn(
                    "truncate text-[11px]",
                    active ? "font-medium" : "text-muted-foreground",
                  )}
                >
                  {tabLabel(tab, t("browse.newTabTitle"))}
                </span>
              </button>
              <IconTip label={t("browse.closeTab")}>
                <button
                  type="button"
                  className="shrink-0 rounded text-muted-foreground opacity-0 hover:text-foreground group-hover:opacity-100"
                  aria-label={t("browse.closeTab")}
                  onClick={() => void closeTab(tab.id)}
                >
                  <X className="size-3" />
                </button>
              </IconTip>
            </span>
          );
        })
      )}

      <IconTip label={t("browse.newTab")}>
        <Button
          variant="ghost"
          size="icon-xs"
          className="ml-auto shrink-0"
          disabled={!available}
          onClick={() => void newTab()}
        >
          <Plus />
        </Button>
      </IconTip>
    </div>
  );
}
