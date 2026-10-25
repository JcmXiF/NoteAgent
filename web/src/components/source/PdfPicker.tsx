import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BlockSelection } from "@/components/source/BlockSelection";
import { pageFromBlockId } from "@/lib/pdf";
import { usePdfDocument, usePdfPage } from "@/lib/use-pdf";
import type { DerivedBlock } from "@/lib/api/types";

/**
 * Picks whole pages out of a PDF.
 *
 * A page is the unit because the backend derives one block per page (`p1`, `p2`,
 * …), so a selection made here points at pages rather than at coordinates.
 * Selecting part of a page is a separate problem and not solved yet.
 */
export function PdfPicker({
  blocks,
  pdfBase64,
  project,
  path,
  identity,
  sourceLabel,
  onCommit,
}: {
  blocks: DerivedBlock[];
  /** Present while the PDF is still a preview rather than a stored file. */
  pdfBase64?: string;
  project?: string;
  /** Set once the original has been stored, so the bytes come from the project. */
  path?: string;
  /**
   * Stable identity of these bytes. Page turns must not reload the document,
   * but pointing at a different PDF must; the byte length is not enough to tell
   * two files apart, so the caller passes the same key it uses for selections.
   */
  identity: string;
  sourceLabel: string;
  onCommit: (ids: string[]) => void;
}) {
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const [page, setPage] = useState(1);
  const [renderError, setRenderError] = useState<string | null>(null);

  const { pdf, error, loading, failed } = usePdfDocument({
    identity,
    base64: pdfBase64,
    project,
    path,
  });
  const pageCount = pdf?.numPages ?? Math.max(blocks.length, 1);

  usePdfPage(pdf, page, canvasRef, stageRef, setRenderError);

  // A new document starts at its first page.
  useEffect(() => {
    setPage(1);
  }, [identity]);

  // Arriving from a selection should show the page it came from.
  const focusId = blocks[0]?.id ?? null;
  useEffect(() => {
    if (!focusId) return;
    const wanted = pageFromBlockId(focusId);
    if (wanted) setPage(wanted);
  }, [focusId]);

  const message = renderError ?? (failed ? error || t("sources.picker.pdfFailed") : null);

  return (
    <BlockSelection blocks={blocks} onCommit={onCommit}>
      <div className="flex min-h-0 flex-col">
        <div className="flex shrink-0 items-center gap-1.5 px-2 py-1.5">
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
          {loading ? (
            <Loader2 className="size-3 animate-spin text-muted-foreground" />
          ) : null}
          <span className="ml-auto truncate text-[11px] text-muted-foreground">
            {sourceLabel}
          </span>
        </div>

        {message ? (
          <p className="px-2 py-1 text-[11px] text-destructive" role="alert">
            {message}
          </p>
        ) : null}

        <div ref={stageRef} className="overflow-auto px-2 pb-2 scroll-thin">
          <canvas ref={canvasRef} className="mx-auto block bg-white shadow-sm" />
        </div>
      </div>
    </BlockSelection>
  );
}
