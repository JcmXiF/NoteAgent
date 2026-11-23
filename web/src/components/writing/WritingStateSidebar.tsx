import { useTranslation } from "react-i18next";
import { PenLine, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useWritingState } from "@/components/writing/use-writing-state";

const AREAS = ["searches", "selectedPages", "frames", "draft"] as const;

/** The writing state's four areas, with what each holds. */
export function WritingStateSidebar() {
  const { t } = useTranslation();
  const { project, state, load } = useWritingState();

  const counts: Record<(typeof AREAS)[number], number> = {
    searches: state?.searches.length ?? 0,
    selectedPages: state?.selectedPages.length ?? 0,
    frames: state?.frames.length ?? 0,
    draft: state?.draft.pages?.length ?? 0,
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b px-3 py-2">
        <PenLine className="size-4 text-muted-foreground" />
        <span className="text-xs font-medium">
          {t("module.writing.sidebar")}
        </span>
        <Button
          variant="ghost"
          size="icon-xs"
          className="ml-auto"
          onClick={() => {
            if (project) void load(project, true);
          }}
          aria-label={t("writing.retry")}
        >
          <RefreshCw />
        </Button>
      </div>
      <ul className="min-h-0 flex-1 overflow-auto p-2">
        {AREAS.map((area) => (
          <li
            key={area}
            className="flex items-center justify-between rounded px-2 py-1.5 text-xs hover:bg-accent"
          >
            <span>{t(`writing.areas.${area}`)}</span>
            <span className="text-muted-foreground">{counts[area]}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
