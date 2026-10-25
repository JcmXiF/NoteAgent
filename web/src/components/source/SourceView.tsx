import { Suspense, lazy, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Globe, Loader2, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { BlockPicker } from "@/components/source/BlockPicker";
import { ImagePicker } from "@/components/source/ImagePicker";
import {
  failedCaptureHint,
  fidelityLabel,
  fidelityTone,
  intakeLabel,
  isHttpUrl,
  isImageCapture,
  isLocalCaptureUrl,
  isPdfCapture,
  reasonText,
} from "@/lib/capture";
import { isPreviewSource, previewSourceKey, previewUrlFromKey, sourceMarkup } from "@/lib/clips";
import { hostOf, tabFor } from "@/lib/browse";
import { fallbackFor, type SourceFallback } from "@/lib/source-fallback";
import { originUrlOf, sourceOriginFromUrl, type SourceOrigin } from "@/lib/source-origin";
import { titleOfPath } from "@/lib/project-tree";
import { roundIdForSelection } from "@/lib/deep-search";
import { message } from "@/lib/message";
import { useAcquireStore, findEntry } from "@/stores/acquire-store";
import { openOriginal, useBrowseStore } from "@/stores/browse-store";
import { useClipsStore } from "@/stores/clips-store";
import { hasDesktop } from "@/lib/desktop";
import { useDeepSearchStore } from "@/stores/deep-search-store";
import { useDocsReads, type FileRead } from "@/stores/docs-reads";
import { useDocsStore } from "@/stores/docs-store";
import { useNavStore } from "@/stores/nav-store";
import type { CapturePreview } from "@/lib/api/types";
import type { PaneSlot } from "@/stores/layout-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * The stored original behind a source, once it has been read.
 *
 * Asked for only while there is no capture: as long as the text is in memory that is
 * what the reader is working with, and reading the file as well would put the copy on
 * disk beside the one that was fetched.
 */
function useStoredOriginal(
  project: string | null,
  path: string | null,
): FileRead | undefined {
  const read = useDocsReads((state) => state.read);
  const file = useDocsReads((state) => (path ? state.reads[path] : undefined));
  useEffect(() => {
    if (project && path) void read(project, path);
  }, [project, path, read]);
  return path ? file : undefined;
}

/**
 * The PDF renderer carries a large dependency, so it is loaded only once a PDF
 * is actually opened rather than as part of the app.
 */
const PdfPicker = lazy(() =>
  import("@/components/source/PdfPicker").then((mod) => ({ default: mod.PdfPicker })),
);

/**
 * What a source gave us.
 *
 * The text of a source lives in one of two places, and which one it is read from is
 * what this decides: the capture this session made, or the original that was stored
 * under `raw/`. Reading the stored file back is what lets a source be shown
 * — and picked from — after a reload, and what keeps a selection's original reachable
 * when the page it came from can no longer be fetched.
 *
 * A source whose text is in neither place is not a dead end here: the way back to it
 * is offered in place of an empty page.
 */
export function SourceView({ slot }: { slot: PaneSlot }) {
  const { t } = useTranslation();
  const entry = useAcquireStore((state) => {
    if (!state.focusedUrl) return undefined;
    return findEntry(state, state.focusedUrl);
  });
  const fetchUrl = useAcquireStore((state) => state.fetchUrl);
  const captureIfOpen = useBrowseStore((state) => state.captureIfOpen);
  const guestTabs = useBrowseStore((state) => state.guest.tabs);
  const busy = useAcquireStore((state) => state.busy);
  const entries = useAcquireStore((state) => state.entries);
  const setFocusedUrl = useAcquireStore((state) => state.setFocusedUrl);
  const setNotice = useAcquireStore((state) => state.setNotice);
  const clearNotice = useAcquireStore((state) => state.clearNotice);
  const notice = useAcquireStore((state) => state.notice);
  const noticeOrigin = useAcquireStore((state) => state.noticeOrigin);
  const collections = useClipsStore((state) => state.collections);
  const focusedCollectionId = useClipsStore((state) => state.focusedId);
  const addCollection = useClipsStore((state) => state.addCollection);
  const project = useWorkspaceStore((state) => state.current);
  const files = useDocsStore((state) => state.files);

  /**
   * A source another view asked to see, when this session's list does not have it.
   *
   * The list is a session's; the project outlives it. A selection made before its
   * source was stored names the file rather than a page, and a selection made from a
   * page that was fetched back then names a page the list has since forgotten. Either
   * way the request is what is left to answer, so it is kept until it has been.
   */
  const [asked, setAsked] = useState<string | null>(null);

  // Another view asking to see one of its selections: find what is behind it, in this
  // session's list or in the project.
  const navSeq = useNavStore((state) => state.seq);
  useEffect(() => {
    const seqNow = navSeq;
    const intent = useNavStore.getState().intent;
    if (!intent || intent.kind !== "sources" || !intent.sourceKey) return;
    const key = intent.sourceKey;
    // A request names the page's address when it carries a preview key, and the
    // stored path otherwise.
    const url = previewUrlFromKey(key) ?? key;
    const match = entries.find(
      (item) =>
        item.url === url ||
        item.path === key ||
        item.path === url ||
        item.capture?.finalUrl === url,
    );
    if (match) {
      setAsked(null);
      setFocusedUrl(match.url);
      clearNotice();
      return;
    }
    // Not in this session's list. What the request is for is seeing the passages in
    // what they came from, and for a source that was stored that is the file — so it
    // is read rather than refused for a list's sake. Saying the list does not have it
    // belongs to the case where the file cannot answer.
    setAsked(key);
    if (isPreviewSource(key) || !project) {
      setNotice(message("sources.notInList"), sourceOriginFromUrl(url));
      return;
    }
    void useDocsReads.getState().read(project, key).then((file) => {
      // Another request may have arrived while this was being read.
      const now = useNavStore.getState();
      if (now.seq !== seqNow) return;
      if (now.intent?.kind !== "sources" || now.intent.sourceKey !== key) return;
      if (file?.status === "ready") {
        clearNotice();
        return;
      }
      setNotice(message("sources.notInList"));
    });
    // Keyed on the request, not on `entries`: the intent is read when it arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navSeq]);

  const capture = entry?.capture;
  /** Where this source's original is, when it has one stored. */
  const storedPath = entry
    ? entry.path ?? null
    : asked && !isPreviewSource(asked) && !isLocalCaptureUrl(asked)
      ? asked
      : null;
  const stored = useStoredOriginal(project, capture ? null : storedPath);
  const storedReady = capture || stored?.status !== "ready" ? null : stored;
  /** The address the stored original records in itself, when it records one. */
  const storedOrigin = storedReady
    ? originUrlOf({ sourceUrl: storedReady.sourceUrl, content: storedReady.content })
    : null;
  /** Whether the stored file is drawn as itself, rather than as its extracted text. */
  const drawnAsFile = storedReady?.kind === "html" || storedReady?.kind === "office";
  /**
   * A markdown original's own document.
   *
   * The core cuts a markdown source into the same numbered blocks as every other
   * original, so the block picker serves it too. Without this, a source written in
   * the product's own format was the one kind that could be read but not selected.
   */
  const markdownBlocks = storedReady?.kind === "markdown" ? storedReady.html : null;

  /**
   * The source in front, in the shape of a capture.
   *
   * A stored original stands for the same thing a capture does: the same text, the
   * same blocks, and a selection made from it points at the file. Reading it back
   * this way is why one set of views, marks and pickers serves both — and why the
   * page does not have to be fetched again to be read.
   */
  const shown: CapturePreview | null = capture
    ? capture
    : storedReady && storedPath
      ? {
          // The address the file records is what "open the original page" needs; the
          // path stands in when it recorded none.
          url: storedOrigin ?? storedPath,
          finalUrl: storedOrigin ?? storedPath,
          title: entry?.title || titleOfPath(storedPath, files),
          // The kinds that have a picker of their own draw the file itself; the plain
          // text view is what the rest is shown as — and a markdown original draws
          // its blocks, so a passage of it can be chosen like any other source.
          markdown: drawnAsFile ? "" : storedReady.content,
          html: drawnAsFile ? storedReady.content : (markdownBlocks ?? undefined),
          blocks: storedReady.blocks,
          captureMethod: "",
          // What the fetch thought of the page belongs to the fetch; the original in
          // the project is readable by virtue of having been read, and the record's
          // own verdict is carried over when it has one.
          fidelity: entry?.fidelity ?? "",
          reasons: [],
          httpStatus: null,
          charCount: 0,
          kind: entry?.kind || storedReady.kind || "",
        }
      : null;

  // The key a selection made from this source records. Until the original is stored
  // the page's address carries its identity; afterwards the file does, which is also
  // the name a request for a stored source arrives under.
  const sourceKey = entry?.path
    ? entry.path
    : entry?.url
      ? previewSourceKey(entry.url)
      : storedPath ?? "";

  // Which blocks of this page already belong to a selection. A selection made before
  // the page was stored is keyed by its preview URL; afterwards by the stored path, so
  // every name this page answers to is offered.
  const markupForSource = useMemo(
    () =>
      sourceMarkup(
        collections,
        [entry?.url ?? "", entry?.capture?.finalUrl ?? "", storedPath ?? ""],
        focusedCollectionId,
      ),
    [collections, entry, storedPath, focusedCollectionId],
  );

  /**
   * Brings a page back, from wherever it can still come from.
   *
   * The browser is asked first when it is on the page: a request cannot get past a
   * sign-in, and the page the browser holds is the one the reader saw. Otherwise
   * this is an ordinary fetch.
   */
  const refetch = async (url: string) => {
    if (!url) return;
    if (await captureIfOpen(url)) return;
    await fetchUrl(url);
  };

  /** Every picker ends the same way: record the chosen blocks. */
  const commit = (ids: string[]) => {
    if (!shown || ids.length === 0) return;
    addCollection({
      sourceKey,
      // A source read back from the project is named by the record's own title, which
      // reads better than the path it is stored at; a page fetched in this session
      // keeps the name it was brought in under.
      sourceLabel: capture ? capture.title || entry?.url || "" : shown.title,
      blockIds: ids,
      blocks: shown.blocks ?? [],
      // While a search is gathering material, what is picked here is that search's.
      roundId: roundIdForSelection(useDeepSearchStore.getState().session),
    });
  };

  if (!shown) {
    // Nothing can be drawn: either the stored original is still being read, or this
    // source's text is in neither place and the way back to it is all that is left.
    if (storedPath && (!stored || stored.status === "loading")) return <OpeningOriginal />;
    // Which source this is about: the one in front, or the one that was asked for.
    // A request names a page's address when it carries a preview key, and the
    // stored path otherwise — and a path is not something to go back to.
    const askedAddress = asked ? previewUrlFromKey(asked) ?? asked : "";
    const address = entry?.url ?? askedAddress;
    const fallback = fallbackFor({
      address,
      inBrowser: Boolean(tabFor(guestTabs, address)),
    });
    return (
      <SourceOffer
        inList={Boolean(entry)}
        readFailed={stored?.status === "error" ? stored.error : null}
        fallback={fallback}
        noticeBody={notice ? t(notice.key, notice.params) : null}
        busy={busy}
        onRefetch={() => void refetch(fallback.refetchUrl ?? "")}
        onOpenInBrowser={() => openOriginal(fallback.browserUrl ?? "", slot)}
      />
    );
  }

  const failed = shown.fidelity === "failed";
  // Which advice fits depends on whether this window has a browser that can sign
  // in: in a plain one, telling the reader to open the page in Browse would be a
  // suggestion the app cannot carry out.
  const hint = failed ? failedCaptureHint(shown, hasDesktop()) : null;
  const pageUrl = isHttpUrl(shown.finalUrl || shown.url) ? shown.finalUrl || shown.url : null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Something to say about the list rather than about this page — e.g. that
          the page another view asked for is not in it. Dismissible, because it is
          not about what is on screen. */}
      {notice ? (
        <p
          aria-live="polite"
          className="flex shrink-0 items-start gap-2 border-b bg-muted/50 px-3 py-1.5 text-[11px] text-muted-foreground"
        >
          <span className="min-w-0 flex-1">{t(notice.key, notice.params)}</span>
          {noticeOrigin ? (
            <MissingSourceActions
              origin={noticeOrigin}
              busy={busy}
              onRefetch={() => void refetch(noticeOrigin.url)}
            />
          ) : null}
          <button
            type="button"
            className="shrink-0 underline underline-offset-2"
            onClick={clearNotice}
          >
            {t("sources.dismissNotice")}
          </button>
        </p>
      ) : null}
      <header className="flex shrink-0 flex-col gap-1.5 border-b px-3 py-2">
        <h2 className="text-sm font-medium">{shown.title || shown.url}</h2>
        <div className="flex flex-wrap items-center gap-1">
          {shown.fidelity ? (
            <Badge tone={fidelityTone(shown.fidelity)}>
              {t(fidelityLabel(shown.fidelity).key)}
            </Badge>
          ) : null}
          {shown.kind ? <Badge tone="outline">{shown.kind}</Badge> : null}
          {shown.intake ? (
            <Badge tone="neutral">{t(intakeLabel(shown.intake).key)}</Badge>
          ) : null}
          {storedPath ? <Badge tone="primary">{t("sources.saved")}</Badge> : null}
        </div>
        {hint ? (
          <p className="text-[11px] text-destructive">
            {t(hint.key, hint.params)} —{" "}
            {shown.reasons.map((code) => reasonText(code, t)).join(" · ")}
          </p>
        ) : null}
        {pageUrl ? (
          <button
            type="button"
            className="w-fit text-[11px] text-primary underline underline-offset-2"
            onClick={() => openOriginal(pageUrl, slot)}
          >
            {t("sources.openInBrowse")}
          </button>
        ) : null}
      </header>

      <div className="min-h-0 flex-1 overflow-auto scroll-thin">
        {isPdfCapture(shown) ? (
          <Suspense
            fallback={
              <p className="flex items-center gap-2 p-3 text-xs text-muted-foreground">
                <Loader2 className="size-3 animate-spin" />
                {t("sources.picker.openingPdf")}
              </p>
            }
          >
            <PdfPicker
              blocks={shown.blocks ?? []}
              pdfBase64={shown.pdfBase64}
              project={project ?? undefined}
              path={storedPath ?? undefined}
              identity={sourceKey}
              sourceLabel={shown.title || shown.url}
              onCommit={commit}
            />
          </Suspense>
        ) : isImageCapture(shown) ? (
          <ImagePicker
            blocks={shown.blocks ?? []}
            imageBase64={shown.imageBase64}
            imageMediaType={shown.imageMediaType}
            project={project ?? undefined}
            path={storedPath ?? undefined}
            sourceLabel={shown.title || shown.url}
            onCommit={commit}
          />
        ) : shown.html ? (
          // A page that was rendered can be shown as it was.
          <BlockPicker
            html={shown.html}
            title={shown.title || shown.url}
            blocks={shown.blocks ?? []}
            sourceKey={sourceKey}
            sourceLabel={capture ? entry?.url || "" : shown.title}
            selectedIds={markupForSource.selectedIds}
            focusId={markupForSource.focusId}
          />
        ) : shown.markdown.trim() ? (
          <pre className="px-3 py-2 text-[11px] whitespace-pre-wrap">
            {shown.markdown}
          </pre>
        ) : (
          <p className="p-3 text-xs text-muted-foreground">{t("sources.preview.noText")}</p>
        )}
      </div>
    </div>
  );
}

/** A stored original being read, before there is anything to show. */
function OpeningOriginal() {
  const { t } = useTranslation();
  return (
    <div className="flex h-full items-center justify-center gap-2 text-xs text-muted-foreground">
      <Loader2 className="size-3.5 animate-spin" />
      {t("sources.restored.opening")}
    </div>
  );
}

/**
 * What is offered when a source's text cannot be shown.
 *
 * The text is in one of two places — the capture this session made, or the original
 * stored in the project — and when neither can be read the reader is not left with a
 * statement about it: where the source came from decides what brings it back. A page
 * can be asked for again, or opened in a browser that can sign in to it. A file the
 * reader brought in has no address to ask for, and saying that adding it again is the
 * way is the whole answer, because the same file is the same source.
 */
function SourceOffer({
  inList,
  readFailed,
  fallback,
  noticeBody,
  busy,
  onRefetch,
  onOpenInBrowser,
}: {
  /** Whether the source is in this session's list at all. */
  inList: boolean;
  /** Why the stored original could not be read, when one was found and read. */
  readFailed: string | null;
  fallback: SourceFallback;
  /** What the list has to say about this source, already in the reader's language. */
  noticeBody: string | null;
  busy: boolean;
  onRefetch: () => void;
  onOpenInBrowser: () => void;
}) {
  const { t } = useTranslation();
  const inBrowser = Boolean(fallback.refetchUrl) && !fallback.browserUrl;

  const said = fallback.readdFile
    ? { title: "sources.fallback.readd.title", body: "sources.fallback.readd.body" }
    : inList
      ? {
          title: "sources.preview.needFetch.title",
          body: inBrowser
            ? "sources.preview.needFetch.inBrowser"
            : "sources.preview.needFetch.body",
        }
      : { title: "sources.empty.title", body: "" };

  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      <p className="text-sm font-medium">{t(said.title)}</p>
      <p className="max-w-sm text-xs text-muted-foreground">
        {/* The list's own sentence when the list is what is missing, so that case is
            described in one place. */}
        {said.body
          ? t(said.body)
          : noticeBody ?? t("sources.empty.body")}
      </p>
      {/* A file that was found and could not be read is a different thing from one
          whose page is gone; the reader is told which it is. */}
      {readFailed ? <p className="max-w-sm text-[11px] text-destructive">{readFailed}</p> : null}
      {fallback.refetchUrl || fallback.browserUrl ? (
        <div className="flex flex-wrap items-center justify-center gap-1.5">
          {fallback.refetchUrl ? (
            <span className="text-[11px] text-muted-foreground">
              {hostOf(fallback.refetchUrl)}
            </span>
          ) : null}
          {fallback.refetchUrl ? (
            <Button variant="outline" size="sm" className="h-6" disabled={busy} onClick={onRefetch}>
              <RefreshCw /> {t(inBrowser ? "sources.takeFromBrowser" : "sources.refetch")}
            </Button>
          ) : null}
          {fallback.browserUrl ? (
            <Button variant="ghost" size="sm" className="h-6" onClick={onOpenInBrowser}>
              <Globe /> {t("sources.openInBrowse")}
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The way back to a source the list does not have.
 *
 * Only the site is shown, not the whole address: the reader is being offered "bring
 * that page back", and which site it was is what makes that a decision they can make.
 */
function MissingSourceActions({
  origin,
  busy,
  onRefetch,
}: {
  origin: SourceOrigin;
  busy: boolean;
  onRefetch: () => void;
}) {
  const { t } = useTranslation();
  return (
    <span className="flex shrink-0 items-center gap-1.5">
      <span className="text-[11px] text-muted-foreground">{origin.domain}</span>
      <Button variant="outline" size="sm" className="h-6" disabled={busy} onClick={onRefetch}>
        <RefreshCw /> {t("sources.refetch")}
      </Button>
    </span>
  );
}
