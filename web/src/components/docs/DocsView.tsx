import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertCircle,
  FileText,
  History,
  Loader2,
  Pencil,
  RotateCcw,
  StickyNote,
  TextCursorInput,
  X,
} from "lucide-react";
import { CitationList } from "@/components/docs/CitationList";
import { MarkdownPage } from "@/components/docs/MarkdownPage";
import { OriginalView } from "@/components/docs/OriginalView";
import { PageEditDialog } from "@/components/docs/PageEditDialog";
import { PageHistoryDialog } from "@/components/docs/PageHistoryDialog";
import { RenamePageDialog } from "@/components/docs/RenamePageDialog";
import { FirstRun, GettingStarted } from "@/components/docs/Welcome";
import { EmptyState } from "@/components/layout/EmptyState";
import { collectionHeadline, sourceMarkup } from "@/lib/clips";
import { titleOfPath } from "@/lib/project-tree";
import { isGeneratedWikiPage } from "@/lib/source-kind";
import { originUrlOf } from "@/lib/source-origin";
import { citationEntries, evidenceFrom, marksForSource, type CitationEntry, type SourceMark } from "@/lib/wiki-citations";
import { bindWikiSections, type EvidenceCollection, type SectionBind } from "@/lib/wiki-evidence";
import { parseWikiSourceLocators, splitWikiFrontmatter } from "@/lib/wiki-locators";
import { openOriginal } from "@/stores/browse-store";
import { Button } from "@/components/ui/button";
import { IconTip } from "@/components/ui/tooltip";
import { useClipsStore } from "@/stores/clips-store";
import { useDocsReads, type FileRead } from "@/stores/docs-reads";
import { useDocsStore, type SectionTarget } from "@/stores/docs-store";
import type { PaneSlot } from "@/stores/layout-store";
import { useNavStore } from "@/stores/nav-store";
import { useNotesStore } from "@/stores/notes-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * Reading a document.
 *
 * A note is rendered as prose; a stored original is shown as it is, and the
 * passages a note already uses are marked in it.
 *
 * The pane is an input because a pane keeps its own set of open documents: the
 * two panes can show different pages, which is what makes reading a note beside
 * the source it cites possible.
 */
export function DocsView({ slot }: { slot: PaneSlot }) {
  const { t } = useTranslation();
  const project = useWorkspaceStore((state) => state.current);
  const [editPath, setEditPath] = useState<string | null>(null);
  const [renamePath, setRenamePath] = useState<string | null>(null);
  const [historyPath, setHistoryPath] = useState<string | null>(null);
  const status = useDocsStore((state) => state.status);
  const files = useDocsStore((state) => state.files);
  const activePath = useDocsStore((state) => state.paths[slot]);
  const focusBlockId = useDocsStore((state) => state.focusBlockIds[slot]);
  const sectionTarget = useDocsStore((state) => state.sectionTargets[slot]);
  const linkError = useDocsStore((state) => state.linkErrors[slot]);
  const open = useDocsStore((state) => state.open);
  const close = useDocsStore((state) => state.close);
  const openLink = useDocsStore((state) => state.openLink);
  const followNote = useDocsStore((state) => state.followNote);
  const hasPage = useDocsStore((state) => state.hasPage);
  const collections = useClipsStore((state) => state.collections);
  const focusedCollectionId = useClipsStore((state) => state.focusedId);
  const followDocument = useDocsStore((state) => state.followDocument);
  const citationIndex = useDocsStore((state) => state.citations);
  const refresh = useDocsStore((state) => state.refresh);
  const projects = useWorkspaceStore((state) => state.projects);
  const projectsLoading = useWorkspaceStore((state) => state.loading);
  const go = useNavStore((state) => state.go);

  // The selections the app knows about, in the shape the evidence helpers take.
  // What the project recorded is included as well as this session's: a page's
  // markers name the selection each section was written from, and after a round
  // ends the project's record is the only place those names are still known.
  const evidence = useMemo<EvidenceCollection[]>(
    () =>
      evidenceFrom(
        citationIndex?.collections,
        collections.map((item) => ({
          id: item.id,
          sourceKey: item.sourceKey,
          blockIds: item.blockIds,
          title: collectionHeadline(item),
        })),
      ),
    [citationIndex, collections],
  );

  // Blocks are needed to match a section by its prose, which only this session's
  // selections can do; the project records ids.
  const evidenceWithBlocks = useMemo<EvidenceCollection[]>(
    () =>
      evidence.map((item) => {
        const session = collections.find((entry) => entry.id === item.id);
        return session
          ? { ...item, blocks: session.blocks.map((block) => ({ id: block.id, text: block.text })) }
          : item;
      }),
    [evidence, collections],
  );

  const read = useDocsReads((state) => (activePath ? state.reads[activePath] : undefined));
  // The passages of the open document, so a citation's passages are checked against
  // what the source actually has before a mark is placed for them.
  const blockOrder = useMemo(
    () => (read?.blocks ?? []).map((block) => block.id),
    [read?.blocks],
  );

  // What this page was written from. The locators are the page's own declaration;
  // the selections say which passages they were, when the app still has them.
  const citations = useMemo(
    () =>
      read?.status === "ready" && read.kind === "markdown"
        ? citationEntries(parseWikiSourceLocators(read.content), evidenceWithBlocks, files)
        : [],
    [read?.status, read?.kind, read?.content, evidenceWithBlocks, files],
  );

  // The sections a page's passages were written into, so a reader can go from a
  // section to the material behind it.
  const binds = useMemo(
    () =>
      read?.status === "ready" && read.kind === "markdown"
        ? bindWikiSections(splitWikiFrontmatter(read.content).body, evidenceWithBlocks)
        : [],
    [read?.status, read?.kind, read?.content, evidenceWithBlocks],
  );

  // A stored original and a selection name the same file, so the passages a
  // selection holds can be marked where the reader is looking. Memoised because
  // the viewer re-marks when this changes, and a fresh array on every render
  // would have it walk the whole document for nothing.
  const markup = useMemo(
    () =>
      sourceMarkup(collections, activePath ? [activePath] : [], focusedCollectionId),
    [collections, activePath, focusedCollectionId],
  );

  // What the project already holds, for the empty workspace to say what is next
  // rather than only that there is nothing here. Generated pages are the program's
  // own and do not count as a page the reader has made.
  const hasPages = useMemo(
    () =>
      files.some(
        (file) => file.path.startsWith("wiki/") && !isGeneratedWikiPage(file.path),
      ),
    [files],
  );
  const sourceCount = useMemo(
    () => files.filter((file) => file.path.startsWith("raw/")).length,
    [files],
  );

  const retry = (
    <Button variant="outline" size="sm" onClick={() => void refresh()}>
      <RotateCcw />
      {t("common.retry")}
    </Button>
  );

  if (status === "error") {
    return (
      <EmptyState
        icon={<AlertCircle className="size-8 text-destructive/40" />}
        title={t("docs.view.loadFailed")}
        body={t("docs.view.loadFailedHint")}
        action={retry}
      />
    );
  }

  if (!activePath) {
    if (!project) {
      // Distinguishing "no project yet" from "the list has not answered" is the
      // difference between an invitation and a flash of one.
      if (projects.length > 0) {
        return (
          <EmptyState
            icon={<FileText className="size-8 text-muted-foreground/25" />}
            title={t("welcome.pickProject")}
            body={t("welcome.pickProjectHint")}
          />
        );
      }
      if (projectsLoading) {
        return (
          <EmptyState
            icon={<Loader2 className="size-6 animate-spin text-muted-foreground/40" />}
            title={t("docs.view.opening")}
          />
        );
      }
      return <FirstRun />;
    }
    // A project with no pages of its own is a project that has not started: the
    // three steps are more use than "open a page", because there is none.
    if (status === "ready" && !hasPages) {
      return <GettingStarted sources={sourceCount} selections={collections.length} />;
    }
    return (
      <EmptyState
        icon={<FileText className="size-8 text-muted-foreground/25" />}
        title={t("docs.view.empty")}
        body={t("docs.view.emptyHint")}
      />
    );
  }

  // A captured page records where it came from inside itself; the other kinds
  // carry it in the file's own record.
  const originUrl = read ? originUrlOf(read) : null;

  // Which kind of document is in front, decided before anything is drawn of it:
  // both what is drawn and whether remarks can be written about it follow from
  // this. A page of prose is the only thing a remark is about.
  const markdown =
    (read?.kind ?? (activePath.endsWith(".md") ? "markdown" : null)) === "markdown";

  // The prose of a wiki page is the reader's to fix; the pages the app writes for
  // itself are not, because the next write would replace them.
  const editable =
    Boolean(activePath) &&
    markdown &&
    activePath.startsWith("wiki/") &&
    !isGeneratedWikiPage(activePath);

  /** Opens the remarks about this page, with one of them in front when named. */
  const openRemarks = (noteId?: string) => {
    if (!activePath) return;
    go({ kind: "notes", path: activePath, noteId }, { placement: "beside", from: slot });
  };

  return (
    <article className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center gap-2 border-b px-3 py-1.5">
        {/* The document is named the way its tab names it; where it sits in the
            project is what the tooltip is for. */}
        <span
          className="truncate text-[11px] text-muted-foreground"
          title={activePath ?? undefined}
        >
          {activePath ? titleOfPath(activePath, files) : ""}
        </span>
        {markup.selectedIds.length > 0 ? (
          <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">
            {t("docs.original.markedCount", { count: markup.selectedIds.length })}
          </span>
        ) : null}
        {originUrl ? (
          <button
            type="button"
            className="shrink-0 text-[11px] text-cite underline underline-offset-2"
            onClick={() => openOriginal(originUrl, slot)}
          >
            {t("docs.original.openSource")}
          </button>
        ) : null}
        {editable ? (
          <>
            <IconTip label={t("docs.page.edit.open")}>
              <Button
                variant="ghost"
                size="icon-xs"
                className="shrink-0"
                aria-label={t("docs.page.edit.open")}
                onClick={() => setEditPath(activePath)}
              >
                <Pencil />
              </Button>
            </IconTip>
            <IconTip label={t("docs.page.rename.open")}>
              <Button
                variant="ghost"
                size="icon-xs"
                className="shrink-0"
                aria-label={t("docs.page.rename.open")}
                onClick={() => setRenamePath(activePath)}
              >
                <TextCursorInput />
              </Button>
            </IconTip>
            <IconTip label={t("docs.page.history.open")}>
              <Button
                variant="ghost"
                size="icon-xs"
                className="shrink-0"
                aria-label={t("docs.page.history.open")}
                onClick={() => setHistoryPath(activePath)}
              >
                <History />
              </Button>
            </IconTip>
          </>
        ) : null}
        {markdown ? (
          <IconTip label={t("remark.openPage")}>
            <Button
              variant="ghost"
              size="icon-xs"
              className="shrink-0"
              aria-label={t("remark.openPage")}
              onClick={() => openRemarks()}
            >
              <StickyNote />
            </Button>
          </IconTip>
        ) : null}
        {slot === "side" && activePath ? (
          <IconTip label={t("docs.nav.closeBeside")}>
            <Button
              variant="ghost"
              size="icon-xs"
              className="shrink-0"
              onClick={() => close("side")}
            >
              <X />
            </Button>
          </IconTip>
        ) : null}
      </header>

      <Body
        read={read}
        activePath={activePath}
        focusBlockId={focusBlockId}
        sectionTarget={sectionTarget}
        linkError={linkError}
        markup={markup}
        project={project}
        openLink={(target) => openLink(target, slot)}
        hasPage={hasPage}
        citations={citations}
        binds={binds}
        marks={activePath ? marksForSource(citationIndex?.sources[activePath], blockOrder) : []}
        onOpenSource={(path, blockId) => open(slot, path, blockId)}
        onOpenSourceBeside={(path, blockId) => followDocument(path, blockId, slot)}
        onOpenNote={(mark) =>
          followNote(
            mark.notePath,
            {
              heading: mark.heading,
              collectionId: mark.collectionId,
            },
            slot,
          )
        }
        onOpenRemarks={openRemarks}
        onRetry={() => void refresh()}
      />

      <PageEditDialog
        project={project}
        path={editPath}
        onClose={() => setEditPath(null)}
      />
      <RenamePageDialog
        project={project}
        path={renamePath}
        slot={slot}
        onClose={() => setRenamePath(null)}
      />
      <PageHistoryDialog
        project={project}
        path={historyPath}
        onClose={() => setHistoryPath(null)}
      />
    </article>
  );
}

/** The document itself, in whichever way suits what it is. */
function Body({
  read,
  activePath,
  focusBlockId,
  sectionTarget,
  linkError,
  markup,
  project,
  openLink,
  hasPage,
  citations,
  binds,
  marks,
  onOpenSource,
  onOpenSourceBeside,
  onOpenNote,
  onOpenRemarks,
  onRetry,
}: {
  read: FileRead | undefined;
  activePath: string | null;
  focusBlockId: string | null;
  sectionTarget: SectionTarget | null;
  linkError: string | null;
  markup: { selectedIds: string[]; focusId: string | null };
  project: string | null;
  openLink: (target: string) => void;
  hasPage: (target: string) => boolean;
  citations: CitationEntry[];
  binds: SectionBind[];
  marks: SourceMark[];
  onOpenSource: (path: string, blockId: string | null) => void;
  onOpenSourceBeside: (path: string, blockId: string | null) => void;
  onOpenNote: (mark: SourceMark) => void;
  /** Opens this page's remarks, with one of them in front when it names one. */
  onOpenRemarks: (noteId?: string) => void;
  /** Reads the page again, after a failure that may not happen twice. */
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  // How this document is shown, once it has been read. Read before anything is
  // drawn of it, because it decides both what is drawn and whether this is the
  // sort of document that has remarks about it at all.
  const kind =
    read?.status === "ready" && activePath
      ? (read.kind ?? (activePath.endsWith(".md") ? "markdown" : null))
      : null;

  // A page's remarks are read with the page and kept beside it, so changing away
  // and back does not read them again.
  const remarks = useNotesStore((state) =>
    activePath ? state.pages[activePath] : undefined,
  );
  const readNotes = useNotesStore((state) => state.read);
  useEffect(() => {
    if (project && activePath && kind === "markdown") {
      void readNotes(project, activePath);
    }
  }, [project, activePath, kind, readNotes]);

  if (!activePath) return null;
  if (!read || read.status === "loading") {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        {t("docs.view.opening")}
      </div>
    );
  }
  if (read.status === "error") {
    return (
      <EmptyState
        icon={<AlertCircle className="size-8 text-destructive/40" />}
        title={t("docs.view.fileFailed")}
        body={read.error ?? ""}
        action={
          <Button variant="outline" size="sm" onClick={onRetry}>
            <RotateCcw />
            {t("common.retry")}
          </Button>
        }
      />
    );
  }

  if (kind === "markdown") {
    return (
      <>
        {/* A link that leads nowhere, or a page's remarks that could not be read,
            is reported where the reader is looking rather than as a page-level
            error: the page itself is fine either way. */}
        <div aria-live="polite" className="shrink-0">
          {linkError ? (
            <span className="block border-b bg-muted/50 px-3 py-1.5 text-[11px] text-muted-foreground">
              {t("docs.view.missingPageNamed", { page: linkError })}
            </span>
          ) : null}
          {remarks?.status === "error" ? (
            <span className="block border-b bg-muted/50 px-3 py-1.5 text-[11px] text-muted-foreground">
              {remarks.error ?? t("remark.loadFailed")}
            </span>
          ) : null}
        </div>
        <CitationList
          entries={citations}
          onOpen={onOpenSource}
          onOpenBeside={onOpenSourceBeside}
        />
        <div className="min-h-0 flex-1 overflow-auto scroll-thin">
          <MarkdownPage
            raw={read.content}
            onOpenLink={(target) => openLink(target)}
            isKnownLink={hasPage}
            binds={binds}
            onOpenSource={onOpenSourceBeside}
            focusSection={sectionTarget}
            notes={remarks?.notes}
            onOpenNote={(note) => onOpenRemarks(note.id)}
            onOpenRemarks={() => onOpenRemarks()}
          />
        </div>
      </>
    );
  }

  if (!kind || !project) {
    return (
      <EmptyState
        icon={<FileText className="size-8 text-muted-foreground/25" />}
        title={t("docs.original.unreadable")}
      />
    );
  }

  return (
    <OriginalView
      kind={kind}
      content={read.content}
      blocks={read.blocks}
      project={project}
      path={activePath}
      selectedIds={markup.selectedIds}
      focusId={focusBlockId ?? markup.focusId}
      marks={marks}
      onOpenNote={onOpenNote}
    />
  );
}
