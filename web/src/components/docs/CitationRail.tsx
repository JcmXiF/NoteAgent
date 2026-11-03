import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { BLOCK_ATTR, documentBox } from "@/lib/block-frame";
import type { SourceMark } from "@/lib/wiki-citations";
import { cn } from "@/lib/utils";

/**
 * The citations of a stored source, down its right-hand side.
 *
 * One numbered mark per note that used this source, placed against the passage it
 * took, so a reader can see at a glance what the source was used for and by whom.
 * Hovering marks the passages that note drew on; clicking opens the note beside,
 * at the section it came from.
 *
 * The marks belong to the page shown, and are placed in the page's own
 * coordinates: a frame is a document of its own, so the position has to be read
 * out of it and translated. They are kept inside the visible area of the frame,
 * which is the difference between "this passage is cited" and "something down
 * there is cited" when the passage has been scrolled past.
 */
export function CitationRail({
  marks,
  container,
  frame,
  scrollTop,
  onOpenNote,
}: {
  marks: SourceMark[];
  /** The element the frame fills, which the marks are positioned against. */
  container: React.RefObject<HTMLElement | null>;
  frame: React.RefObject<HTMLIFrameElement | null>;
  /** The frame's scroll offset, so positions follow as it scrolls. */
  scrollTop: number;
  /** Opens the citing note beside this source, at the section it came from. */
  onOpenNote: (mark: SourceMark) => void;
}) {
  const { t } = useTranslation();
  const [active, setActive] = useState<string | null>(null);
  const [placed, setPlaced] = useState<{ mark: SourceMark; top: number; inView: boolean }[]>(
    [],
  );

  /** Where each mark's first cited passage is, in the container's coordinates. */
  const measure = useCallback(() => {
    const host = container.current;
    const doc = frame.current?.contentDocument;
    const win = frame.current?.contentWindow;
    if (!host || !doc?.body || !win) return;
    const hostRect = host.getBoundingClientRect();
    const limit = Math.max(0, hostRect.height - MARK_SIZE);
    const found: typeof placed = [];
    for (const mark of marks) {
      let top: number | null = null;
      for (const id of mark.blockIds) {
        const el = doc.getElementById(`na-${CSS.escape(id)}`);
        if (!el) continue;
        // The frame's scroll offset is the caller's, so the passage's place in
        // the frame's viewport follows from it.
        top = documentBox(el, win).top - scrollTop;
        break;
      }
      // A citation that names no passage is about the file, so it sits at the top
      // rather than nowhere.
      const at = top ?? 0;
      found.push({
        mark,
        top: Math.min(Math.max(at, 0), limit),
        inView: top !== null && at >= 0 && at <= limit,
      });
    }

    // Marks for the same passage — which is common, since several notes often
    // start from the same opening lines — would otherwise hide one another, so
    // they are stepped down out of each other's way.
    found.sort((a, b) => a.top - b.top || a.mark.id.localeCompare(b.mark.id));
    const step: number[] = [];
    for (const item of found) {
      let slot = 0;
      while (
        slot < step.length &&
        item.top < (step[slot] ?? 0) + MARK_SIZE
      ) {
        slot += 1;
      }
      step[slot] = Math.max(item.top, step[slot] ?? item.top);
      item.top = Math.min(item.top + slot * MARK_SIZE, limit);
    }
    setPlaced(found);
  }, [container, frame, marks, scrollTop]);

  // The measured positions belong to the document shown, so they are taken again
  // whenever the document, its scroll offset or the citations change — and once the
  // frame has loaded, since a frame renders its document after the first pass.
  useEffect(() => {
    const el = frame.current;
    // A srcDoc frame is often already complete before React attaches, in which
    // case no load event is coming and the next frame is the earliest the layout
    // can be read.
    const raf = window.requestAnimationFrame(measure);
    if (!el) return () => window.cancelAnimationFrame(raf);
    el.addEventListener("load", measure);
    return () => {
      el.removeEventListener("load", measure);
      window.cancelAnimationFrame(raf);
    };
  }, [frame, measure]);

  // Hovering marks the passages, which means touching the frame's document. The
  // class is removed again when the pointer leaves, so nothing lingers.
  useEffect(() => {
    const doc = frame.current?.contentDocument;
    if (!doc) return undefined;
    const wanted = new Set(
      placed.find((item) => item.mark.id === active)?.mark.blockIds ?? [],
    );
    for (const el of doc.querySelectorAll(`[${BLOCK_ATTR}]`)) {
      const id = el.getAttribute(BLOCK_ATTR);
      el.classList.toggle("na-cited", Boolean(id && wanted.has(id)));
    }
    return () => undefined;
  }, [active, placed, frame]);

  const marksByTop = useMemo(
    () => [...placed].sort((a, b) => a.top - b.top || a.mark.id.localeCompare(b.mark.id)),
    [placed],
  );

  if (marks.length === 0) return null;

  return (
    <div
      className="pointer-events-none absolute inset-y-0 right-1 w-7"
      aria-label={t("docs.citations.rail")}
    >
      <div className="relative h-full">
        {marksByTop.map((item, index) => (
          <Popover key={item.mark.id} open={active === item.mark.id}>
            <PopoverTrigger
              aria-label={t("docs.citations.markLabel", {
                index: index + 1,
                title: item.mark.noteTitle,
              })}
              title={[index + 1, item.mark.noteTitle, item.mark.heading]
                .filter(Boolean)
                .join(" · ")}
              onMouseEnter={() => setActive(item.mark.id)}
              onMouseLeave={() => setActive(null)}
              onClick={(event) => event.stopPropagation()}
              className={cn(
                "pointer-events-auto absolute right-0 grid place-items-center rounded-full border text-[10px] tabular-nums transition-colors",
                item.inView ? "" : "opacity-60",
                "border-cite/40 bg-cite/15 text-cite hover:bg-cite/30",
              )}
              style={{ top: item.top, width: MARK_SIZE, height: MARK_SIZE }}
            >
              {index + 1}
            </PopoverTrigger>
            <PopoverContent side="left" className="w-56">
              <p className="px-1.5 py-1 text-[11px] font-medium">{item.mark.noteTitle}</p>
              {item.mark.heading ? (
                <p className="px-1.5 pb-1 text-[11px] text-muted-foreground">
                  {item.mark.heading}
                </p>
              ) : null}
              <p className="px-1.5 pb-1 text-[11px] text-muted-foreground">
                {t("docs.citations.tookPassages", { count: item.mark.blockIds.length })}
              </p>
              <Button
                variant="ghost"
                size="sm"
                className="w-full justify-start"
                onClick={() => {
                  setActive(null);
                  onOpenNote(item.mark);
                }}
              >
                <FileText /> {t("docs.citations.openNote")}
              </Button>
            </PopoverContent>
          </Popover>
        ))}
      </div>
    </div>
  );
}

const MARK_SIZE = 18;
