import { RAW_BUCKETS, isGeneratedWikiPage, isRawSourcePath, rawBucket, type RawBucket } from "@/lib/source-kind";

/**
 * Turning the project's flat file list into a tree.
 *
 * The API returns a flat list of paths; the sidebar needs a nested one. This tree
 * mirrors the project as it is on disk — the notes the app writes, and the files it
 * saved — so it answers "where is it". What a file *is* is the other list's job.
 */

export type TreeFile = {
  path: string;
  name: string;
  title: string;
};

export type TreeNode = {
  /** `null` for a group this app invented rather than a real directory. */
  path: string | null;
  name: string;
  /** Label to show; groups get a translated one, folders their name. */
  title: string;
  kind: "group" | "folder" | "file";
  children: TreeNode[];
  /** Present on files only. */
  file?: TreeFile;
  /** Groups only: which section this is. */
  group?: ProjectSection;
};

/** The two parts of a project that hold documents. */
type ProjectSection = "wiki" | "sources";

const WIKI_SECTION = "wiki";
const SOURCES_SECTION = "sources";

/**
 * The section a path belongs to, or null when the tree does not show it.
 *
 * Only the notes and the stored sources are listed. Anything else in the project
 * directory (its configuration, caches, the files the program maintains) is not a
 * document, so it stays out of the sidebar.
 */
export function sectionOf(path: string): ProjectSection | null {
  const clean = path.replace(/\\/g, "/");
  if (clean.startsWith("wiki/")) return WIKI_SECTION;
  if (clean.startsWith("raw/")) return SOURCES_SECTION;
  return null;
}

/** Groups the files under a common prefix into folders, recursively. */
function nest(files: TreeFile[], depth: number): TreeNode[] {
  const folders = new Map<string, TreeFile[]>();
  const leaves: TreeFile[] = [];

  for (const file of files) {
    const segments = file.path.split("/");
    // `depth` is how much of the path the caller has already consumed; a file
    // with nothing left at this level is a leaf.
    if (segments.length <= depth + 1) {
      leaves.push(file);
      continue;
    }
    const folder = segments[depth] as string;
    folders.set(folder, [...(folders.get(folder) ?? []), file]);
  }

  const nodes: TreeNode[] = [];
  for (const [name, children] of [...folders.entries()].sort(([a], [b]) =>
    a.localeCompare(b, "zh-Hans-CN"),
  )) {
    nodes.push({
      path: children[0]?.path.split("/").slice(0, depth + 1).join("/") ?? name,
      name,
      title: name,
      kind: "folder",
      children: nest(children, depth + 1),
    });
  }
  for (const file of [...leaves].sort((a, b) => a.title.localeCompare(b.title, "zh-Hans-CN"))) {
    nodes.push({
      path: file.path,
      name: file.name,
      title: file.title || file.name,
      kind: "file",
      children: [],
      file,
    });
  }
  return nodes;
}

/**
 * Builds the sidebar tree.
 *
 * The project's own folders are kept — below `wiki/` and below `raw/`, the
 * nesting is whatever is on disk, including the kind folder an original was
 * filed under. The notes come first because they are what the project is for.
 * A section with nothing in it is left out rather than shown empty.
 */
export function buildProjectTree(files: TreeFile[]): TreeNode[] {
  const bySection = new Map<ProjectSection, TreeFile[]>();
  for (const file of files) {
    const section = sectionOf(file.path);
    if (!section) continue;
    bySection.set(section, [...(bySection.get(section) ?? []), file]);
  }

  const nodes: TreeNode[] = [];
  for (const section of [WIKI_SECTION, SOURCES_SECTION] as const) {
    const members = bySection.get(section);
    if (!members || members.length === 0) continue;
    // The section's own folder is consumed here, so nesting starts below it:
    // everything under `wiki/`, and the kind folders under `raw/`.
    const depth = 1;
    nodes.push({
      path: null,
      name: section,
      title: section,
      kind: "group",
      group: section,
      children: nest(members, depth),
    });
  }
  return nodes;
}

/* ------------------------------------------------------- the notes library */

/** One heading's worth of files in the tidier list. */
export type KnowledgeGroup = {
  /** Stable within a build; what a folded heading is remembered by. */
  id: string;
  /** i18n key for the heading. */
  labelKey: string;
  files: TreeFile[];
};

/** The two headings a wiki's pages are listed under. */
const NOTE_HEADING = "docs.knowledge.notes";
const SUMMARY_HEADING = "docs.knowledge.summaries";

/** i18n keys for the kinds of stored original. */
const KIND_LABEL: Record<RawBucket, string> = {
  web: "docs.kind.web",
  pdf: "docs.kind.pdf",
  office: "docs.kind.office",
  media: "docs.kind.media",
  other: "docs.kind.other",
};

/**
 * The notes, and the originals they are written from, grouped by what each is.
 *
 * This is the counterpart to the tree: the tree says where a file is, this says
 * what it is. A wiki page is a note, unless it is one source's summary, so the
 * heading comes from the kind rather than from the folder: the pages the program
 * maintains are left out because nobody reads them, and a page still sitting in
 * a retired type folder is listed with the other notes. The stored originals
 * follow, grouped by kind rather than by the folder they happen to sit in, so a
 * PDF filed under `web/` is still listed with the PDFs.
 */
export function buildKnowledgeGroups(files: TreeFile[]): KnowledgeGroup[] {
  const notes: TreeFile[] = [];
  const summaries: TreeFile[] = [];
  const originals = new Map<RawBucket, TreeFile[]>();

  for (const file of files) {
    if (file.path.startsWith("wiki/")) {
      if (isGeneratedWikiPage(file.path)) continue;
      if (file.path.slice("wiki/".length).startsWith("sources/")) {
        summaries.push(file);
      } else {
        notes.push(file);
      }
      continue;
    }
    if (!isRawSourcePath(file.path)) continue;
    const bucket = rawBucket(file.path);
    originals.set(bucket, [...(originals.get(bucket) ?? []), file]);
  }

  const byTitle = (a: TreeFile, b: TreeFile) =>
    (a.title || a.name).localeCompare(b.title || b.name, "zh-Hans-CN");

  const noteGroups: KnowledgeGroup[] = [
    { id: "wiki:notes", labelKey: NOTE_HEADING, files: notes },
    { id: "wiki:sources", labelKey: SUMMARY_HEADING, files: summaries },
  ]
    .filter((group) => group.files.length > 0)
    .map((group) => ({ ...group, files: [...group.files].sort(byTitle) }));

  const originalGroups: KnowledgeGroup[] = RAW_BUCKETS.filter((bucket) =>
    originals.has(bucket),
  ).map((bucket) => ({
    id: `raw:${bucket}`,
    labelKey: KIND_LABEL[bucket],
    files: [...(originals.get(bucket) ?? [])].sort(byTitle),
  }));

  return [...noteGroups, ...originalGroups];
}

/** Every file in the tree, in the order the tree shows them. */
export function flattenTree(nodes: TreeNode[]): TreeFile[] {
  const out: TreeFile[] = [];
  for (const node of nodes) {
    if (node.file) out.push(node.file);
    else out.push(...flattenTree(node.children));
  }
  return out;
}

/**
 * A note's slug: the identity the graph and the plan use for it.
 *
 * `wiki/entropy.md` → `entropy`; a page still in a retired folder keeps that folder
 * in its identity. Lower-cased nowhere, because the identity is what it is; callers
 * that compare case-insensitively do that themselves.
 */
export function wikiPathSlug(path: string): string {
  const clean = path.replace(/\\/g, "/").trim();
  if (!clean.startsWith("wiki/")) return "";
  return clean.slice("wiki/".length).replace(/\.md$/i, "").replace(/^\/+/, "");
}

/** The titles and slugs a page can be linked to by, for resolving `[[links]]`. */
export function linkIndex(files: TreeFile[]): Map<string, TreeFile> {
  const index = new Map<string, TreeFile>();
  for (const file of files) {
    const slug = wikiPathSlug(file.path);
    if (!slug) continue;
    index.set(slug.toLowerCase(), file);
    if (file.title) index.set(file.title.toLowerCase(), file);
    index.set(file.name.replace(/\.md$/, "").toLowerCase(), file);
  }
  return index;
}

/**
 * The page a `[[link]]` refers to, or null when there is none.
 *
 * A link may name a page's title, its file, or its path without the extension.
 * Returning null rather than guessing keeps the caller from opening the wrong
 * page when a link is stale.
 *
 * Targets arrive percent-encoded because that is what Markdown parsers do to
 * non-ASCII in a link destination, so the decoding happens here rather than
 * leaving every caller to remember it.
 */
export function resolveWikiLink(
  target: string,
  index: Map<string, TreeFile>,
): TreeFile | null {
  const decoded = decodeLinkTarget(target);
  const wanted = decoded.replace(/\\/g, "/").replace(/^\.?\//, "");
  if (!wanted) return null;
  return (
    index.get(wanted.toLowerCase()) ??
    index.get(wanted.replace(/\.md$/, "").toLowerCase()) ??
    null
  );
}

/**
 * Reads a link target back out of its URL form.
 *
 * A target that is not valid percent-encoding (a stray `%`, say) is used as it
 * stands rather than thrown away, since a page title may legitimately contain
 * one.
 */
export function decodeLinkTarget(target: string): string {
  const trimmed = target.trim();
  if (!trimmed.includes("%")) return trimmed;
  try {
    return decodeURIComponent(trimmed);
  } catch {
    return trimmed;
  }
}

/**
 * The name to show for a document.
 *
 * The project's own title for the file when it has one, and otherwise the last
 * part of the path — somewhere a name is needed, a file the tree does not
 * describe still has to say something.
 */
export function titleOfPath(path: string, files: TreeFile[]): string {
  const known = files.find((file) => file.path === path)?.title;
  if (known) return known;
  const name = path.split("/").pop() ?? path;
  return name.replace(/\.(md|html|htm|pdf|docx|pptx)$/i, "") || name;
}
