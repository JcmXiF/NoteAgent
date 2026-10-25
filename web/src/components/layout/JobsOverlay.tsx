import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { IconTip } from "@/components/ui/tooltip";
import { ClearFinishedButton, JobsList } from "@/components/layout/JobsList";
import { useLayoutStore } from "@/stores/layout-store";

/**
 * Everything the app is doing, over whatever is on screen.
 *
 * This is an overlay rather than a part of the layout because the shell's browser
 * is drawn *over* the page: anything the page draws below that surface cannot be
 * read. An overlay says it is in front by holding that surface out of the way.
 *
 * In a plain browser there is no surface to hold, so opening it would cover the
 * page for nothing; the sidebar's row shows the same list there.
 */
export function JobsOverlay() {
  const { t } = useTranslation();
  const open = useLayoutStore((state) => state.jobsOpen);
  const setJobsOpen = useLayoutStore((state) => state.setJobsOpen);
  const holdGuest = useLayoutStore((state) => state.holdGuest);
  const releaseGuest = useLayoutStore((state) => state.releaseGuest);

  // The page the shell is showing steps aside while this is in front.
  useEffect(() => {
    if (!open) return undefined;
    holdGuest();
    return () => releaseGuest();
  }, [open, holdGuest, releaseGuest]);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setJobsOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, setJobsOpen]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      {/* Clicking away closes it, the way a panel over the page is expected to. */}
      <button
        type="button"
        aria-label={t("common.close")}
        className="flex-1 bg-black/20"
        onClick={() => setJobsOpen(false)}
      />
      <section
        role="dialog"
        aria-label={t("activity.title")}
        className="flex h-full w-[26rem] max-w-[85vw] flex-col border-l bg-sidebar shadow-xl"
      >
        <header className="flex h-9 shrink-0 items-center gap-1.5 border-b px-2">
          <span className="text-xs font-semibold">{t("activity.title")}</span>
          <span className="ml-auto flex items-center">
            <ClearFinishedButton />
            <IconTip label={t("common.close")}>
              <Button variant="ghost" size="icon-xs" onClick={() => setJobsOpen(false)}>
                <X />
              </Button>
            </IconTip>
          </span>
        </header>
        <JobsList />
      </section>
    </div>
  );
}
