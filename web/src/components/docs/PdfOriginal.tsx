import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { pageFromBlockId } from "@/lib/pdf";
import { usePdfDocument, usePdfPage } from "@/lib/use-pdf";
import type { DerivedBlock } from "@/lib/api/types";

/**
 * A PDF original, a page at a time.
 *
 * Its own component, and its own chunk: the PDF renderer is the largest
 * dependency in the app and only a PDF needs it.
 */
export function PdfOriginal({
  blocks,
  project,
  path,
  selectedIds,
  focusId,
}: {
  blocks: DerivedBlock[];
  project: string;
  path: string;
  /** Pages that belong to a selection. */
  selectedIds: string[];
  focusId?: string | null;
}) {
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const [page, setPage] = useState(1);
  const [renderError, setRenderError] = useState<string | null>(null);

  const { pdf, error, loading, failed } = usePdfDocument({ identity: path, project, path });
  const pageCount = pdf?.numPages ?? Math.max(blocks.length, 1);

  usePdfPage(pdf, page, canvasRef, stageRef, setRenderError);

  // A different document starts at its first page.
  useEffect(() => {
    setPage(1);
  }, [path]);

  // A passage names the page it is on, so arriving from one turns to it.
  useEffect(() => {
    if (!focusId) return;
    const wanted = pageFromBlockId(focusId);
    if (wanted) setPage(wanted);
  }, [focusId]);

  const marked = new Set(selectedIds);
  const message = renderError ?? (failed ? error || t("sources.picker.pdfFailed") : null);
  // A page is drawn to a canvas, so it cannot carry a mark of its own; the fact
  // is said in words instead.
  const onMarkedPage = marked.has(`p${page}`);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-1.5 border-b px-2 py-1.5">
        <Button
          variant="outline"
          size="icon-sm"
          disabled={page <= 1}
          aria-label={t("sources.picker.previousPage")}
          onClick={() => setPage((current) => Math.max(1, current - 1))}
        >
          <ChevronLeft />
        </Button>
        <span className="text-[11px] text-muted-foreground tabular-nums">
          {t("sources.picker.pageOf", { page, count: pageCount })}
        </span>
        <Button
          variant="outline"
          size="icon-sm"
          disabled={page >= pageCount}
          aria-label={t("sources.picker.nextPage")}
          onClick={() => setPage((current) => Math.min(pageCount, current + 1))}
        >
          <ChevronRight />
        </Button>
        {loading ? <Loader2 className="size-3 animate-spin text-muted-foreground" /> : null}
        {onMarkedPage ? (
          <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">
            {t("docs.original.markedPage")}
          </span>
        ) : null}
      </div>

      {message ? (
        <p className="px-2 py-1 text-[11px] text-destructive" role="alert">
          {message}
        </p>
      ) : null}

      <div ref={stageRef} className="min-h-0 flex-1 overflow-auto p-2 scroll-thin">
        <canvas
          ref={canvasRef}
          className={
            onMarkedPage
              ? "mx-auto block bg-white shadow-sm outline-2 outline-offset-4 outline-clip"
              : "mx-auto block bg-white shadow-sm"
          }
        />
      </div>
    </div>
  );
}
