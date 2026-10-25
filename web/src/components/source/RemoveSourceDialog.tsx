import { useTranslation } from "react-i18next";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { previewSourceKey } from "@/lib/clips";
import { captureMatches } from "@/lib/capture";
import { useAcquireStore } from "@/stores/acquire-store";
import { useClipsStore } from "@/stores/clips-store";

/**
 * Asks before taking a source out of the workbench.
 *
 * The part a reader cannot see from the list is the passages already chosen from
 * it: those go with it, and planning would lose them. The originals stay on disk,
 * so the sentence says that too rather than letting "remove" sound like "delete".
 */
export function RemoveSourceDialog({
  urls,
  onClose,
  onRemoved,
}: {
  /** The sources being removed, or null when nothing is being asked about. */
  urls: string[] | null;
  onClose: () => void;
  /** Called once the removal has happened, for state that only followed from it. */
  onRemoved?: () => void;
}) {
  const { t } = useTranslation();
  const entries = useAcquireStore((state) => state.entries);
  const removeCaptures = useAcquireStore((state) => state.removeCaptures);
  const collections = useClipsStore((state) => state.collections);

  const selected = urls ?? [];
  const asking = urls !== null;

  // Which selections go with them, counted the same way the removal itself finds
  // them: by the preview key the workbench entry holds.
  const keys = new Set<string>();
  for (const url of selected) {
    keys.add(previewSourceKey(url));
    const entry = entries.find((item) => item.url === url);
    const capture = entry?.capture;
    if (capture && captureMatches(capture, url)) {
      keys.add(previewSourceKey(entry.url));
      if (capture.finalUrl) keys.add(previewSourceKey(capture.finalUrl));
    }
  }
  const passages = collections
    .filter((item) => keys.has(item.sourceKey))
    .reduce((total, item) => total + item.blockIds.length, 0);

  const submit = () => {
    removeCaptures(selected);
    onRemoved?.();
    onClose();
  };

  return (
    <Dialog
      open={asking}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("sources.remove.title", { count: selected.length })}</DialogTitle>
          <DialogDescription>{t("sources.remove.body")}</DialogDescription>
        </DialogHeader>
        {passages > 0 ? (
          <p className="text-[11px] text-warning">
            {t("sources.remove.passages", { count: passages })}
          </p>
        ) : null}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button variant="destructive" onClick={submit}>
            <Trash2 /> {t("sources.remove.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
