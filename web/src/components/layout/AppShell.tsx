import { useEffect, useRef } from "react";
import { PanelLeftOpen, PanelRightOpen } from "lucide-react";
import { useTranslation } from "react-i18next";
import { IconSidebar } from "@/components/layout/IconSidebar";
import { SettingsView } from "@/components/settings/SettingsView";
import { LeftColumn } from "@/components/layout/LeftColumn";
import { ContentArea } from "@/components/layout/ContentArea";
import { GuestHost } from "@/components/browse/GuestHost";
import { JobsOverlay } from "@/components/layout/JobsOverlay";
import { ProjectDialogs } from "@/components/layout/ProjectDialogs";
import { RightPanel } from "@/components/layout/RightPanel";
import { CommandPalette } from "@/components/search/CommandPalette";
import { Button } from "@/components/ui/button";
import { IconTip, TooltipProvider } from "@/components/ui/tooltip";
import { beginPanelDrag, dragHandleClass } from "@/lib/drag";
import { desktopBridge } from "@/lib/desktop";
import { shortcut } from "@/lib/platform";
import { RIGHT_MAX_RATIO, leftWidthFor, useLayoutStore } from "@/stores/layout-store";
import { MODULE_IDS } from "@/modules";

const RAIL_WIDTH = 48;

/** Modifier+1..5 selects a module; add Shift to open it beside the current one. */
function useLayoutShortcuts() {
  const openModule = useLayoutStore((state) => state.openModule);
  const setLeftOpen = useLayoutStore((state) => state.setLeftOpen);
  const setRightOpen = useLayoutStore((state) => state.setRightOpen);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;

      const digit = Number(event.key);
      if (Number.isInteger(digit) && digit >= 1 && digit <= MODULE_IDS.length) {
        event.preventDefault();
        openModule(MODULE_IDS[digit - 1], { side: event.shiftKey });
        return;
      }

      const key = event.key.toLowerCase();
      if (key === "b") {
        event.preventDefault();
        setLeftOpen((prev) => !prev);
      } else if (key === "e") {
        event.preventDefault();
        setRightOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [openModule, setLeftOpen, setRightOpen]);
}

/**
 * Opens the browser module when the shell asks for it.
 *
 * The shell has a menu item for its own browser — a reader can reach it from the
 * menu bar without knowing which module holds it — so the request arrives from
 * outside the app and is answered here, beside whatever is already open.
 */
function useShowBrowseRequest() {
  const openModule = useLayoutStore((state) => state.openModule);
  useEffect(() => {
    const bridge = desktopBridge();
    // Whether this is the desktop shell decides where the jobs list lives, so it is
    // written down once, where the bridge is looked at anyway.
    useLayoutStore.setState({ hasShell: Boolean(bridge) });
    if (!bridge?.onShowBrowse) return undefined;
    return bridge.onShowBrowse(() => openModule("browse", { side: true }));
  }, [openModule]);
}

/**
 * Application shell: icon rail, sidebar, workspace and details panel.
 * All three columns are resizable and their sizes are persisted.
 */
export function AppShell() {
  const { t } = useTranslation();
  const leftOpen = useLayoutStore((state) => state.leftOpen);
  const rightOpen = useLayoutStore((state) => state.rightOpen);
  // The sidebar's width belongs to the module being looked at: a list of names and
  // a file tree want different room.
  const view = useLayoutStore((state) => state.view);
  const side = useLayoutStore((state) => state.side);
  const focused = useLayoutStore((state) => state.focused);
  const leftWidth = useLayoutStore((state) =>
    leftWidthFor(state, focused === "side" && side ? side : view),
  );
  const rightWidth = useLayoutStore((state) => state.rightWidth);
  const setLeftWidth = useLayoutStore((state) => state.setLeftWidth);
  const setRightWidth = useLayoutStore((state) => state.setRightWidth);
  const setLeftOpen = useLayoutStore((state) => state.setLeftOpen);
  const setRightOpen = useLayoutStore((state) => state.setRightOpen);
  const settingsOpen = useLayoutStore((state) => state.settingsOpen);

  const shellRef = useRef<HTMLDivElement>(null);

  useLayoutShortcuts();
  useShowBrowseRequest();

  const onLeftSplitterDown = (event: React.MouseEvent) => {
    const shellRect = shellRef.current?.getBoundingClientRect();
    if (!shellRect) return;
    beginPanelDrag(event, {
      onMove: (moveEvent) => {
        setLeftWidth(moveEvent.clientX - shellRect.left - RAIL_WIDTH);
      },
    });
  };

  const onRightSplitterDown = (event: React.MouseEvent) => {
    const shellRect = shellRef.current?.getBoundingClientRect();
    if (!shellRect) return;
    const max = shellRect.width * RIGHT_MAX_RATIO;
    beginPanelDrag(event, {
      onMove: (moveEvent) => {
        setRightWidth(shellRect.right - moveEvent.clientX, max);
      },
    });
  };

  return (
    <TooltipProvider>
      <div ref={shellRef} className="flex h-full min-h-0">
        {/* Tells the shell where its browser goes. Mounted here so it outlives
            every module: leaving the module is then a change of claim rather than
            the page being left where it stood. */}
        <GuestHost />
        {/* Everything the app is doing, over the page, wherever the reader is. */}
        <JobsOverlay />
        {/* Making a project and opening a folder, wherever they were asked for:
            the empty workspace offers both before the sidebar has anything. */}
        <ProjectDialogs />
        {/* One search over pages, remarks and sources, from anywhere. */}
        <CommandPalette />
        <IconSidebar />

        {settingsOpen ? (
          <main className="flex min-h-0 min-w-0 flex-1 flex-col">
            <SettingsView />
          </main>
        ) : (
          <>
            {leftOpen ? (
              <>
                <div
                  style={{ width: leftWidth }}
                  className="flex h-full min-h-0 shrink-0 flex-col"
                >
                  <LeftColumn />
                </div>
                <div
                  className={dragHandleClass()}
                  onMouseDown={onLeftSplitterDown}
                  role="separator"
                  aria-orientation="vertical"
                  aria-label={t("nav.resizeLeft")}
                />
              </>
            ) : null}

            <main className="relative flex min-h-0 min-w-0 flex-1 flex-col">
              {!leftOpen || !rightOpen ? (
                <div className="flex h-6 shrink-0 items-center gap-1 border-b px-1">
                  {!leftOpen ? (
                    <IconTip label={`${t("nav.toggleLeft")} (${shortcut("B")})`}>
                      <Button variant="ghost" size="icon-xs" onClick={() => setLeftOpen(true)}>
                        <PanelLeftOpen />
                      </Button>
                    </IconTip>
                  ) : null}
                  <div className="min-w-0 flex-1" />
                  {!rightOpen ? (
                    <IconTip label={`${t("nav.toggleRight")} (${shortcut("E")})`}>
                      <Button variant="ghost" size="icon-xs" onClick={() => setRightOpen(true)}>
                        <PanelRightOpen />
                      </Button>
                    </IconTip>
                  ) : null}
                </div>
              ) : null}
              <ContentArea />
            </main>

            {rightOpen ? (
              <>
                <div
                  className={dragHandleClass()}
                  onMouseDown={onRightSplitterDown}
                  role="separator"
                  aria-orientation="vertical"
                  aria-label={t("nav.resizeRight")}
                />
                <div
                  style={{ width: rightWidth }}
                  className="flex h-full min-h-0 shrink-0 flex-col"
                >
                  <RightPanel />
                </div>
              </>
            ) : null}
          </>
        )}
      </div>
    </TooltipProvider>
  );
}
