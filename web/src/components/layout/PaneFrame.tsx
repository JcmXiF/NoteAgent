import { ArrowLeft, ArrowRight, Columns2, X, ArrowLeftRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { IconTip } from "@/components/ui/tooltip";
import { moduleViews } from "@/components/module-views";
import { canGoBack, canGoForward } from "@/lib/reading-history";
import { MODULE_META, type ModuleId } from "@/modules";
import { cn } from "@/lib/utils";
import { useLayoutStore, type PaneSlot } from "@/stores/layout-store";
import { useReadingStore } from "@/stores/reading-store";

/**
 * One workspace pane: a title bar plus the module it hosts.
 *
 * Panes render their module directly. There is no shared stage that hosts every
 * view and repositions it onto the active pane, so a module mounts and unmounts
 * with its pane.
 */
export function PaneFrame({
  slot,
  module,
  focused,
  style,
  className,
}: {
  slot: PaneSlot;
  module: ModuleId;
  focused: boolean;
  /** Layout injected by the workspace, e.g. `flex: 0 0 50%` when split. */
  style?: React.CSSProperties;
  className?: string;
}) {
  const { t } = useTranslation();
  const meta = MODULE_META[module];
  const openModule = useLayoutStore((state) => state.openModule);
  const lastSide = useLayoutStore((state) => state.lastSide);
  const closeSide = useLayoutStore((state) => state.closeSide);
  const focusPane = useLayoutStore((state) => state.focusPane);
  const swapPanes = useLayoutStore((state) => state.swapPanes);
  // Where the reader has been is the window's, not a module's: a step holds both
  // panes and the module each of them is showing, so the way back belongs where both
  // panes are on screen rather than inside whichever view is in front.
  const history = useReadingStore((state) => state.history);
  const stepBack = useReadingStore((state) => state.back);
  const stepForward = useReadingStore((state) => state.forward);
  const hasSteps = canGoBack(history) || canGoForward(history);

  return (
    <section
      style={style}
      className={cn(
        "flex min-h-0 min-w-0 flex-col bg-background",
        focused ? "ring-1 ring-inset ring-ring/20" : "",
        className,
      )}
      // Focusing on the click rather than on the press: the sidebar's width
      // follows the focused module, so focusing mid-press moves the panes out
      // from under the pointer and the browser reports the click on a shared
      // ancestor instead — the control that was pressed never hears it. The
      // click is also what the pane's own views act on, and the store has
      // already moved by the time their handlers run.
      onClickCapture={() => focusPane(slot)}
    >
      <header className="flex h-9 shrink-0 items-center gap-1.5 border-b px-2">
        {/* Somewhere to have come from, and somewhere to have come back to. Shown
            only where there is a step to take: a pair of controls that can do
            nothing says less than no controls at all. */}
        {slot === "main" && hasSteps ? (
          <div className="flex shrink-0 items-center gap-0.5">
            <IconTip label={t("nav.back")}>
              <Button
                variant="ghost"
                size="icon-xs"
                disabled={!canGoBack(history)}
                onClick={stepBack}
              >
                <ArrowLeft />
              </Button>
            </IconTip>
            <IconTip label={t("nav.forward")}>
              <Button
                variant="ghost"
                size="icon-xs"
                disabled={!canGoForward(history)}
                onClick={stepForward}
              >
                <ArrowRight />
              </Button>
            </IconTip>
          </div>
        ) : null}
        <meta.icon className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="text-xs font-semibold">{t(`${meta.i18nKey}.label`)}</span>
        <span className="truncate text-[11px] text-muted-foreground">
          {t(`${meta.i18nKey}.sidebar`)}
        </span>

        <div className="ml-auto flex items-center gap-0.5">
          {slot === "main" ? (
            <IconTip label={`${t("nav.split")} — ${t("nav.splitHint")}`}>
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => openModule(lastSide, { side: true })}
              >
                <Columns2 />
              </Button>
            </IconTip>
          ) : null}
          {slot === "side" ? (
            <>
              <IconTip label={t("nav.swapPanes")}>
                <Button variant="ghost" size="icon-xs" onClick={swapPanes}>
                  <ArrowLeftRight />
                </Button>
              </IconTip>
              <IconTip label={t("nav.closeSplit")}>
                <Button variant="ghost" size="icon-xs" onClick={closeSide}>
                  <X />
                </Button>
              </IconTip>
            </>
          ) : null}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-hidden">
        <PaneBody module={module} slot={slot} />
      </div>
    </section>
  );
}

/**
 * Dispatches to the module's view, telling it which pane it is in.
 *
 * Only the active pane's module is rendered, which is what makes the placeholder
 * in `module-views` a fallback rather than a second implementation. A view that
 * keeps its own state — the reader does, so two panes can read different pages —
 * needs to know which of the two it is.
 */
function PaneBody({ module, slot }: { module: ModuleId; slot: PaneSlot }) {
  const { Content } = moduleViews(module);
  return <Content slot={slot} />;
}
