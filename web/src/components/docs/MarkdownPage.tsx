import {
  Fragment,
  isValidElement,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import type { Components } from "react-markdown";
import { PanelRightOpen } from "lucide-react";
import {
  MarkdownBody,
  MarkdownFlow,
  useMarkdownComponents,
} from "@/components/docs/MarkdownBody";
import { NoteBlock } from "@/components/docs/NoteBlock";
import type { Note } from "@/lib/api/notes";
import { placeNotes } from "@/lib/note-placement";
import { pageBody } from "@/lib/page-body";
import { readSections } from "@/lib/sections";
import { cn } from "@/lib/utils";
import type { SectionBind } from "@/lib/wiki-evidence";
import type { SectionTarget } from "@/stores/docs-store";

type MarkdownPageProps = {
  /** The page's text, frontmatter and all. */
  raw: string;
  /** Opens the page a link names; the view decides what that means. */
  onOpenLink?: (target: string) => void;
  /**
   * Whether a link names something the project has. A link that does not is shown
   * as one that leads nowhere, so the page says so before it is clicked. Omitted
   * means every link is taken as good, which suits a reader with no project behind
   * it.
   */
  isKnownLink?: (target: string) => boolean;
  /**
   * Which passages each section was written from, as the page's own markers and
   * the selections describe them. A section that can be tied to material gets an
   * affordance to read that material beside it.
   */
  binds?: SectionBind[];
  /** Opens the material a section came from, in the pane next door. */
  onOpenSource?: (path: string, blockId: string) => void;
  /**
   * The section to land on, named by the heading it was written under or by the
   * selection it came from. Either may be missing, so whichever is known is used.
   * The page scrolls to it once it has rendered.
   */
  focusSection?: SectionTarget | null;
  /**
   * The remarks written about this page, in the order they were made. Each one is
   * shown beside the section its author put it with; one naming a place the page
   * no longer has is shown at the end, so it is not lost by being unplaceable.
   */
  notes?: Note[];
  /**
   * Opens one remark where it is written. The page is read-only, so pointing at a
   * remark is the only thing there is to do with it here.
   */
  onOpenNote?: (note: Note) => void;
  /** Opens the page's remarks themselves, for the ones with no place to be drawn at. */
  onOpenRemarks?: () => void;
};

/**
 * Shows a wiki page as it reads.
 *
 * Links, embeds and maths are prepared by the shared markdown module, so a page
 * reads here exactly as it does anywhere else it is shown.
 *
 * A page is drawn a section at a time, which is what lets a remark sit at the join
 * between two of them without the page's own text being touched. A section is a
 * heading and what follows it, so nothing a paragraph, list, table or code fence
 * can carry is cut in half by this; what no single section can carry is a
 * construct reaching past its own end — a link whose definition is written under
 * another heading, a formula opened in one and closed in the next — and such a
 * construct is read as the text it looks like rather than as what it stood for.
 */
export function MarkdownPage({
  raw,
  onOpenLink,
  isKnownLink,
  binds,
  onOpenSource,
  focusSection,
  notes,
  onOpenNote,
  onOpenRemarks,
}: MarkdownPageProps) {
  const { t } = useTranslation();
  const body = useMemo(() => pageBody(raw), [raw]);

  // The sections the page is drawn by, and where each remark goes among them.
  // Both come from this one reading, so the two cannot disagree about where a
  // section ends.
  const split = useMemo(() => readSections(body), [body]);
  const placement = useMemo(
    () => placeNotes(notes ?? [], body, split.sections),
    [notes, body, split],
  );

  /**
   * Where each heading node is, so a section can be brought into view.
   *
   * The page keeps this itself rather than leaving callers to search the document:
   * it is the only place that knows which node belongs to which heading, and a
   * search from outside would walk the DOM twice and match on text already known
   * here.
   */
  const headingRefs = useRef(new Map<string, HTMLElement>());
  const registerHeading = useCallback(
    (children: ReactNode) => (node: HTMLElement | null) => {
      const text = headingText(children).trim();
      if (!text) return;
      if (node) headingRefs.current.set(text, node);
      else headingRefs.current.delete(text);
    },
    [],
  );

  // Sections that can be traced back, by heading. A page has one section per
  // heading, so the heading is what identifies it.
  const traced = useMemo(() => {
    const map = new Map<string, SectionBind>();
    for (const bind of binds ?? []) {
      if (!bind.path || bind.blockIds.length === 0) continue;
      map.set(bind.heading, bind);
    }
    return map;
  }, [binds]);

  /**
   * The way from a section to the material behind it.
   *
   * Nothing unless the section can actually be traced, so a page written before the
   * app kept track of this shows no affordance rather than a dead one.
   */
  const headingSource = useCallback(
    (children: ReactNode) => {
      const bind = traced.get(headingText(children).trim());
      const blockId = bind?.blockIds[0];
      if (!bind?.path || !blockId || !onOpenSource) return null;
      const path = bind.path;
      return (
        <button
          type="button"
          aria-label={t("docs.citations.openSectionSource")}
          title={t("docs.citations.openSectionSource")}
          onClick={() => onOpenSource(path, blockId)}
          className="ml-2 shrink-0 rounded p-0.5 align-middle text-muted-foreground opacity-60 hover:bg-accent hover:text-accent-foreground hover:opacity-100"
        >
          <PanelRightOpen className="size-3" />
        </button>
      );
    },
    [traced, onOpenSource, t],
  );

  const components = useMarkdownComponents({
    onOpenLink,
    isKnownLink,
    missingTitle: t("docs.view.missingPage"),
    headings: { register: registerHeading, extra: headingSource },
  });

  // Landing on a section: which heading the request names, or which one the
  // selection it came from was written under.
  const focusHeading = useMemo(() => {
    if (!focusSection) return null;
    const wanted = focusSection.heading?.trim();
    if (wanted && traced.has(wanted)) return wanted;
    if (focusSection.collectionId) {
      const bind = (binds ?? []).find(
        (item) => item.groupId === focusSection.collectionId,
      );
      if (bind?.heading) return bind.heading;
    }
    return wanted && headingRefs.current.has(wanted) ? wanted : null;
  }, [focusSection, traced, binds]);

  useEffect(() => {
    if (!focusHeading) return undefined;
    const node = headingRefs.current.get(focusHeading);
    if (!node) return undefined;
    node.scrollIntoView({ block: "start", behavior: "smooth" });
    node.dataset.sectionFocus = "true";
    const timer = window.setTimeout(() => {
      delete node.dataset.sectionFocus;
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [focusHeading]);

  if (!body) {
    return <p className="p-3 text-xs text-muted-foreground">{t("docs.view.pageEmpty")}</p>;
  }

  const remarkLabel = t("remark.badge");
  const openLabel = t("remark.open");
  const draw = (group: Note[]) => (
    <Remarks
      notes={group}
      components={components}
      label={remarkLabel}
      openLabel={openLabel}
      onOpen={onOpenNote}
    />
  );

  return (
    <MarkdownFlow className="px-4 py-3">
      <MarkdownBody
        text={body.slice(split.preamble.start, split.preamble.end)}
        components={components}
      />
      {draw(placement.preamble)}
      {split.sections.map((section, index) => (
        <Fragment key={`${index}-${section.heading}`}>
          <MarkdownBody
            text={body.slice(section.start, section.end)}
            components={components}
          />
          {draw(placement.sections[index])}
        </Fragment>
      ))}
      {draw(placement.end)}
      {placement.unplaced.length > 0 ? (
        <Unplaced
          text={t("remark.unplaced", { count: placement.unplaced.length })}
          onOpen={onOpenRemarks}
        />
      ) : null}
    </MarkdownFlow>
  );
}

/**
 * The remarks with nowhere on the page to be drawn.
 *
 * Reported rather than put somewhere plausible, and openable when the screen they
 * are managed on can be reached: a remark whose heading has been renamed is shown
 * where it can be put back rather than only counted.
 */
function Unplaced({
  text,
  onOpen,
}: {
  text: string;
  onOpen?: () => void;
}) {
  const className =
    "mt-4 border-t border-border pt-2 text-[11px] text-muted-foreground";
  if (!onOpen) return <p className={className}>{text}</p>;
  return (
    <button
      type="button"
      aria-label={text}
      onClick={onOpen}
      className={cn(className, "block w-full text-left hover:text-foreground")}
    >
      {text}
    </button>
  );
}

/** The remarks belonging at one place on the page, as they were written. */
function Remarks({
  notes,
  components,
  label,
  openLabel,
  onOpen,
}: {
  notes: Note[];
  components: Components;
  label: string;
  openLabel: string;
  onOpen?: (note: Note) => void;
}) {
  if (notes.length === 0) return null;
  return (
    <>
      {notes.map((note) => (
        <NoteBlock
          key={note.id}
          label={label}
          openLabel={openLabel}
          onOpen={onOpen ? () => onOpen(note) : undefined}
        >
          <MarkdownBody text={note.body} components={components} />
        </NoteBlock>
      ))}
    </>
  );
}

/**
 * The words of a heading.
 *
 * A heading's children are whatever the parser made of the line — usually a plain
 * string, but emphasis or a link make it a node — so the text is gathered rather
 * than assumed. Only what a heading can actually contain is looked at.
 */
function headingText(children: ReactNode): string {
  if (typeof children === "string" || typeof children === "number") {
    return String(children);
  }
  if (Array.isArray(children)) return children.map(headingText).join("");
  if (isValidElement<{ children?: ReactNode }>(children)) {
    return headingText(children.props.children);
  }
  return "";
}
