import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { CheckCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { windowHeight, windowRange } from "@/lib/window-range";
import type { DerivedBlock } from "@/lib/api/types";

/**
 * Tick blocks and hand the chosen ones over.
 *
 * A PDF page and a whole image are both selected the same way — from a list of
 * what the source came out as — so the list and its actions live here once
 * rather than in each picker. The caller supplies whatever visual belongs above
 * it, and receives the ids in document order.
 *
 * The list can be as long as the document: a book gives one row per page. Only
 * the rows near the viewport are drawn, which is why every row is one height.
 */

/**
 * Height of one row: the two clamped lines of text it can hold, plus its padding.
 *
 * Every row has to be the same height for the windowing arithmetic, so the text is
 * clamped rather than allowed to wrap freely.
 */
const ROW_TEXT_HEIGHT = 34;
const ROW_PADDING = 8;
const ROW_HEIGHT = ROW_TEXT_HEIGHT + ROW_PADDING;
/** How tall the list is: whole rows, so nothing is half on screen at rest. */
const LIST_HEIGHT = ROW_HEIGHT * 6;

export function BlockSelection({
  blocks,
  onCommit,
  children,
}: {
  blocks: DerivedBlock[];
  /** Called with the chosen ids, ordered as the document reads. */
  onCommit: (ids: string[]) => void;
  /** The visual for the source, shown above the list. */
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const [picked, setPicked] = useState<string[]>([]);
  const listRef = useRef<HTMLDivElement>(null);
  const [windowTop, setWindowTop] = useState(0);

  const order = useMemo(
    () => new Map(blocks.map((block, index) => [block.id, index])),
    [blocks],
  );

  // The list is a fixed height, so it only needs measuring once; the offset is
  // what changes. Reading it from the element rather than keeping it in state
  // means a scroll cannot arrive out of step with what is drawn.
  useLayoutEffect(() => {
    const node = listRef.current;
    if (!node) return;
    setWindowTop(node.scrollTop);
  }, [blocks]);

  const toggle = (id: string) =>
    setPicked((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );

  const commit = () => {
    if (picked.length === 0) return;
    // Document order, not the order they were ticked.
    const ids = [...picked].sort((a, b) => (order.get(a) ?? 0) - (order.get(b) ?? 0));
    onCommit(ids);
    setPicked([]);
  };

  if (blocks.length === 0) {
    return (
      <div className="flex h-full flex-col">
        {children}
        <p className="p-3 text-xs text-muted-foreground">
          {t("sources.picker.noBlocks")}
        </p>
      </div>
    );
  }

  const range = windowRange({
    total: blocks.length,
    rowHeight: ROW_HEIGHT,
    viewport: LIST_HEIGHT,
    scrollTop: windowTop,
  });
  const drawn = blocks.slice(range.start, range.end);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-auto scroll-thin">{children}</div>

      <div className="flex shrink-0 items-center gap-1.5 border-t px-2 py-1.5">
        <Button
          variant="outline"
          size="sm"
          onClick={() => setPicked(blocks.map((block) => block.id))}
        >
          <CheckCheck /> {t("sources.picker.selectAll")}
        </Button>
        <span className="ml-auto text-[11px] text-muted-foreground">
          {t("sources.picker.selectedCount", { count: picked.length })}
        </span>
        <Button size="sm" disabled={picked.length === 0} onClick={commit}>
          {t("sources.picker.addSelected")}
        </Button>
      </div>

      <div
        ref={listRef}
        onScroll={(event) => setWindowTop(event.currentTarget.scrollTop)}
        style={{ height: LIST_HEIGHT }}
        className="shrink-0 overflow-auto border-t scroll-thin"
      >
        {/* The spacer keeps the scrollbar honest; the rows are drawn over it. */}
        <div style={{ height: windowHeight(blocks.length, ROW_HEIGHT) }}>
          <div style={{ transform: `translateY(${range.start * ROW_HEIGHT}px)` }}>
            {drawn.map((block) => {
              const on = picked.includes(block.id);
              return (
                <label
                  key={block.id}
                  style={{ height: ROW_HEIGHT }}
                  className={cn(
                    "flex cursor-pointer items-start gap-1.5 overflow-hidden px-2 py-1 text-[11px] leading-[16.5px]",
                    on ? "bg-accent" : "hover:bg-accent/40",
                  )}
                >
                  <input
                    type="checkbox"
                    className="mt-0.5 size-3 shrink-0"
                    checked={on}
                    onChange={() => toggle(block.id)}
                  />
                  <span className="line-clamp-2 min-w-0">
                    <span className="font-mono text-[10px] text-muted-foreground">
                      {block.id}
                    </span>{" "}
                    {block.preview || block.text}
                  </span>
                </label>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
