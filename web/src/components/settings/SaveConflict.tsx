import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";

/**
 * A save the document no longer accepts: another writer got there first.
 *
 * The prompt refreshes the revision behind the form. It never touches the
 * draft, which is the point: the reader's work stays on screen to send again.
 */
export function SaveConflict({ onRefresh }: { onRefresh: () => Promise<void> }) {
  const { t } = useTranslation();
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-2 rounded-lg border border-warning/50 bg-warning/10 p-3"
    >
      <span className="min-w-0 flex-1 text-xs leading-relaxed">
        {t("error.code.settings.staleRevision")}
      </span>
      <Button type="button" variant="outline" size="sm" onClick={() => void onRefresh()}>
        {t("settings.refresh")}
      </Button>
    </div>
  );
}
