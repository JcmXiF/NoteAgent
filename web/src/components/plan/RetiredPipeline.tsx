import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { useLayoutStore } from "@/stores/layout-store";

/**
 * The old plan/compile workspace no longer starts a run.
 *
 * Its runs stay readable in the pipeline section; the write path is the
 * writing state, so the section points there instead of offering a button
 * whose request the core now refuses.
 */
export function RetiredPipeline() {
  const { t } = useTranslation();
  const openModule = useLayoutStore((state) => state.openModule);

  return (
    <div className="p-2">
      <p className="text-[11px] text-muted-foreground">
        {t("pipeline.retired")}
      </p>
      <Button
        variant="outline"
        size="sm"
        className="mt-2"
        onClick={() => openModule("writing")}
      >
        {t("pipeline.goWriting")}
      </Button>
    </div>
  );
}
