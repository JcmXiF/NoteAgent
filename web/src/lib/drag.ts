/**
 * Panel resizing, shared by every draggable separator.
 *
 * Tracked on `document` rather than the handle so the drag keeps working when
 * the pointer leaves the thin gutter.
 */
export function beginPanelDrag(
  event: { preventDefault: () => void },
  handlers: {
    /** Raw mousemove; callers derive the width or ratio they need. */
    onMove: (moveEvent: MouseEvent) => void;
    onEnd?: () => void;
  },
): void {
  event.preventDefault();

  const onUp = () => {
    document.removeEventListener("mousemove", handlers.onMove);
    document.removeEventListener("mouseup", onUp);
    delete document.body.dataset.panelResizing;
    handlers.onEnd?.();
  };

  document.body.dataset.panelResizing = "true";
  document.addEventListener("mousemove", handlers.onMove);
  document.addEventListener("mouseup", onUp);
}

/** Separator between two panels: thin, and highlighted while hovered. */
export function dragHandleClass(extra?: string): string {
  return [
    "relative w-1.5 shrink-0 cursor-col-resize transition-colors",
    "bg-border/40 hover:bg-primary/30 active:bg-primary/40",
    extra ?? "",
  ].join(" ");
}
