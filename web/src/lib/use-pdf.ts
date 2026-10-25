import { useEffect, useRef, useState, type RefObject } from "react";
import { bytesFromBase64, getDocument, type PDFDocumentProxy } from "@/lib/pdf";
import { fileBytesUrl } from "@/lib/api/files";

/**
 * Loading and drawing a PDF, shared by the picker and the reader.
 *
 * Both have to solve the same two problems — load a document once for a given
 * file, and draw one page without leaking the previous render — and their only
 * difference is what happens to the canvas afterwards.
 */

/** How wide a rendered page may be, in CSS pixels. */
const MAX_PAGE_WIDTH = 900;
const MIN_SCALE = 0.3;
const MAX_SCALE = 2;

type PdfSource = {
  /**
   * Stable identity of the bytes. Two files of the same length are not the same
   * file, so the caller passes the key it also uses for selections.
   */
  identity: string;
  /** Present while the PDF is still a preview rather than a stored file. */
  base64?: string;
  project?: string;
  /** Set once the original has been stored, so the bytes come from the project. */
  path?: string;
};

/**
 * The open document, or an error saying why there is none.
 *
 * The page is deliberately not an input: turning a page must not fetch the
 * document again, which is the whole reason the document lives in its own hook.
 */
export function usePdfDocument({ identity, base64, project, path }: PdfSource): {
  pdf: PDFDocumentProxy | null;
  /** The renderer's own message, when it gave one. */
  error: string | null;
  loading: boolean;
  /** There is a document to show but it could not be opened. */
  failed: boolean;
} {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!identity || (!base64 && !(project && path))) {
      setPdf(null);
      setError(null);
      setLoading(false);
      return undefined;
    }
    let cancelled = false;
    const task = getDocument(
      base64
        ? { data: bytesFromBase64(base64) }
        : { url: fileBytesUrl(project as string, path as string) },
    );
    setLoading(true);
    setError(null);
    task.promise
      .then((doc) => {
        // A document that arrives after unmount is released by the cleanup
        // below, which destroys the loading task along with it.
        if (cancelled) return;
        setPdf(doc);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      setPdf(null);
      void task.destroy();
    };
  }, [identity, base64, project, path]);

  return { pdf, error, loading, failed: Boolean(error) };
}

/**
 * Draws one page onto a canvas, sizing it to the container.
 *
 * A render in flight is cancelled when the page turns again; pdf.js reports that
 * as an error, and it is expected rather than a failure to show.
 */
export function usePdfPage(
  pdf: PDFDocumentProxy | null,
  page: number,
  canvasRef: RefObject<HTMLCanvasElement | null>,
  stageRef: RefObject<HTMLElement | null>,
  onError: (message: string) => void,
): void {
  // The callback is re-created by most callers on every render, so reading it
  // through a ref keeps the render effect from restarting.
  const latestError = useRef(onError);
  latestError.current = onError;

  useEffect(() => {
    if (!pdf) return undefined;
    let cancelled = false;
    let renderTask: { cancel: () => void } | null = null;

    (async () => {
      try {
        const target = Math.min(Math.max(page, 1), pdf.numPages);
        const pdfPage = await pdf.getPage(target);
        if (cancelled) return;

        const base = pdfPage.getViewport({ scale: 1 });
        const available = stageRef.current?.clientWidth ?? MAX_PAGE_WIDTH;
        const scale = Math.min(
          MAX_SCALE,
          Math.max(MIN_SCALE, Math.min(available, MAX_PAGE_WIDTH) / base.width),
        );
        const viewport = pdfPage.getViewport({ scale });

        const canvas = canvasRef.current;
        const context = canvas?.getContext("2d");
        if (!canvas || !context) return;
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);

        const task = pdfPage.render({ canvasContext: context, canvas, viewport });
        renderTask = task;
        await task.promise;
      } catch (err: unknown) {
        // A cancelled render is expected when turning pages quickly.
        const name = err instanceof Error ? err.name : "";
        if (cancelled || name === "RenderingCancelledException") return;
        latestError.current(err instanceof Error ? err.message : "");
      }
    })();

    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [pdf, page, canvasRef, stageRef]);
}
