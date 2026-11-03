import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { CitationRail } from "@/components/docs/CitationRail";
import { applyFrameStyle, hostColors, paintSelection, scrollToBlock } from "@/lib/block-frame";
import { fileBytesUrl } from "@/lib/api/files";
import type { SourceMark } from "@/lib/wiki-citations";
import type { DerivedBlock } from "@/lib/api/types";

/**
 * A stored original, read.
 *
 * Read-only on purpose: this is where a source is looked at, not where passages
 * are chosen. The marks it draws say which passages a selection already holds, so
 * a reader arriving from a note can see where the note came from.
 *
 * The PDF branch carries the largest dependency in the app, so it lives in its
 * own component and is loaded only when a PDF is opened.
 */
const PdfOriginal = lazy(() =>
  import("@/components/docs/PdfOriginal").then((mod) => ({ default: mod.PdfOriginal })),
);

type OriginalViewProps = {
  kind: string | null;
  /** Markdown and HTML originals arrive as text; binary ones do not. */
  content: string | null;
  blocks: DerivedBlock[];
  project: string;
  path: string;
  /** Passages already in a selection, marked in the original. */
  selectedIds: string[];
  /** The passage to bring into view. */
  focusId?: string | null;
  /** The notes that used this source, drawn down its side. */
  marks: SourceMark[];
  /** Opens a citing note beside this source, at the section it came from. */
  onOpenNote: (mark: SourceMark) => void;
};

export function OriginalView(props: OriginalViewProps) {
  const { t } = useTranslation();
  if (props.kind === "pdf") {
    return (
      <Suspense
        fallback={
          <p className="flex items-center gap-2 p-3 text-xs text-muted-foreground">
            <Loader2 className="size-3 animate-spin" />
            {t("sources.picker.openingPdf")}
          </p>
        }
      >
        <PdfOriginal
          blocks={props.blocks}
          project={props.project}
          path={props.path}
          selectedIds={props.selectedIds}
          focusId={props.focusId}
        />
      </Suspense>
    );
  }
  if (props.kind === "image") return <ImageOriginal {...props} />;
  // The backend renders an Office document to the same kind of sandboxed HTML a
  // captured page uses.
  return <HtmlOriginal {...props} />;
}

/**
 * A frame for a captured page or a converted document.
 *
 * The frame's document is a separate realm, so the marks are drawn from here by
 * reaching into it: React has no components in there to render. The citation rail
 * is the exception — it is a column of buttons, so it lives in this document,
 * where React can render it, and is positioned from what the frame reports.
 */
function HtmlOriginal({
  content,
  path,
  selectedIds,
  focusId,
  marks,
  onOpenNote,
}: OriginalViewProps) {
  const { t } = useTranslation();
  const hostRef = useRef<HTMLDivElement>(null);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [scrollTop, setScrollTop] = useState(0);

  useEffect(() => {
    const frame = frameRef.current;
    const host = hostRef.current;
    if (!frame || !host) return undefined;

    const paint = () => {
      const doc = frame.contentDocument;
      const win = frame.contentWindow;
      if (!doc?.body || !win) return;
      applyFrameStyle(doc, hostColors(host), false);
      paintSelection(doc, win, selectedIds, focusId);
    };

    // The rail is placed from the frame's own coordinates, so where the frame has
    // scrolled to is part of what the caller needs to draw it.
    const onScroll = () => setScrollTop(frame.contentWindow?.scrollY ?? 0);

    frame.addEventListener("load", paint);
    frame.contentWindow?.addEventListener("scroll", onScroll);
    // A srcDoc document is often already complete before React attaches.
    if (frame.contentDocument?.readyState === "complete") paint();
    return () => {
      frame.removeEventListener("load", paint);
      frame.contentWindow?.removeEventListener("scroll", onScroll);
    };
  }, [content, selectedIds, focusId]);

  useEffect(() => {
    const doc = frameRef.current?.contentDocument;
    if (doc) scrollToBlock(doc, focusId);
  }, [focusId]);

  if (!content) {
    return <p className="p-3 text-xs text-muted-foreground">{t("docs.original.unreadable")}</p>;
  }

  return (
    <div ref={hostRef} className="relative min-h-0 flex-1 bg-white">
      <iframe
        ref={frameRef}
        title={path}
        // Same origin so the page can be inspected and marked, but scripts are
        // never allowed: the page is shown, not run.
        sandbox="allow-same-origin"
        referrerPolicy="no-referrer"
        srcDoc={content}
        className="size-full border-0"
      />
      <CitationRail
        marks={marks}
        container={hostRef}
        frame={frameRef}
        scrollTop={scrollTop}
        onOpenNote={onOpenNote}
      />
    </div>
  );
}

/** A standalone image, whole. */
function ImageOriginal({ project, path, focusId }: OriginalViewProps) {
  const { t } = useTranslation();
  const imageRef = useRef<HTMLImageElement>(null);
  const src = fileBytesUrl(project, path);

  // Arriving from a selection that names this image brings it into view.
  useEffect(() => {
    if (focusId) imageRef.current?.scrollIntoView({ block: "center" });
  }, [focusId]);

  return (
    <div className="min-h-0 flex-1 overflow-auto p-2 scroll-thin">
      <img
        ref={imageRef}
        className="mx-auto block max-w-full bg-white shadow-sm"
        src={src}
        alt={t("docs.original.imageAlt")}
      />
    </div>
  );
}
