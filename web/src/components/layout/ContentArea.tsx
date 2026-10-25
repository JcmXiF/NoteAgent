import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { PaneFrame } from "@/components/layout/PaneFrame";
import { clampSplitRatio, useLayoutStore } from "@/stores/layout-store";
import { beginPanelDrag, dragHandleClass } from "@/lib/drag";

/**
 * Workspace. One pane by default; splitting shows a second module beside it.
 * The split ratio is stored in the layout store, so it survives reloads.
 */
export function ContentArea() {
  const { t } = useTranslation();
  const view = useLayoutStore((state) => state.view);
  const side = useLayoutStore((state) => state.side);
  const focused = useLayoutStore((state) => state.focused);
  const splitRatio = useLayoutStore((state) => state.splitRatio);
  const setSplitRatio = useLayoutStore((state) => state.setSplitRatio);

  const containerRef = useRef<HTMLDivElement>(null);

  const onSplitterDown = (event: React.MouseEvent) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return;
    beginPanelDrag(event, {
      onMove: (moveEvent) => {
        const ratio = (moveEvent.clientX - rect.left) / rect.width;
        setSplitRatio(clampSplitRatio(ratio, rect.width));
      },
    });
  };

  return (
    <div ref={containerRef} className="flex min-h-0 min-w-0 flex-1">
      <PaneFrame
        slot="main"
        module={view}
        focused={focused === "main" || side === null}
        // A single pane fills the space; a split gives each pane its share.
        style={{ flex: side === null ? "1 1 auto" : `0 0 ${splitRatio * 100}%` }}
      />

      {side !== null ? (
        <>
          <div
            className={dragHandleClass()}
            onMouseDown={onSplitterDown}
            role="separator"
            aria-orientation="vertical"
            aria-label={t("nav.resizeSplit")}
          />
          <PaneFrame
            slot="side"
            module={side}
            focused={focused === "side"}
            className="flex-1"
          />
        </>
      ) : null}
    </div>
  );
}
