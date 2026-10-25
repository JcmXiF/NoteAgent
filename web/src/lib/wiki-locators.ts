import { isRawSourcePath, isRenderableOriginalPath } from "@/lib/source-kind";

/**
 * Reading where a wiki page says it came from.
 *
 * A page declares its sources in frontmatter (`sources: [raw/…/a.html#b3]`).
 * This module is only about reading those declarations back: turning text into
 * locators, and deciding which of them point at something the documents view can
 * actually open. Matching them against the app's selections is next door, in
 * `wiki-evidence`.
 *
 * Pure text handling: no fetching, no React, no i18n.
 */

export type SourceLocator = {
  path: string;
  /** Absent when the whole file is cited rather than one passage. */
  blockId: string | null;
};

export function normalizePath(path: string): string {
  return path.replace(/\\/g, "/").replace(/^\/+/, "");
}

/**
 * Whether a locator points at an original the documents view can render.
 *
 * Requires the sources folder as well as a renderable extension, so a stray
 * `wiki/…` or a markdown source does not look like an original.
 */
export function isRenderableOriginal(path: string): boolean {
  const clean = normalizePath(path).split("#")[0] ?? "";
  if (!isRawSourcePath(clean)) return false;
  return isRenderableOriginalPath(clean);
}

/**
 * Splits `raw/…/a.html#b3` into its file and passage.
 *
 * The result always names a file: text that is only a passage (`#b3`) is
 * rejected rather than returned with a path of `#b3`, so callers can rely on
 * `path` being something they could look up.
 */
export function parseSourceLocator(raw: string): SourceLocator | null {
  const text = raw.trim();
  if (!text) return null;
  const hash = text.lastIndexOf("#");
  if (hash <= 0) {
    // No passage part — unless the whole thing is a passage, which has no file.
    if (text.startsWith("#")) return null;
    const path = normalizePath(text);
    return path ? { path, blockId: null } : null;
  }
  const path = normalizePath(text.slice(0, hash));
  const blockId = text.slice(hash + 1).trim();
  if (!path) return null;
  return { path, blockId: blockId || null };
}

/**
 * Reads the items of a YAML flow list (`[a, "b c", 'd']`).
 *
 * Hand-rolled because the only YAML in a wiki page is this one line, and pulling
 * in a parser for it would be a dependency for a dozen lines of scanning. Quotes
 * are honoured so a title containing a comma survives.
 */
export function parseYamlFlowList(inner: string): string[] {
  const out: string[] = [];
  let i = 0;
  while (i < inner.length) {
    while (i < inner.length && /[\s,]/.test(inner[i] ?? "")) i += 1;
    if (i >= inner.length) break;

    if (inner[i] === '"') {
      i += 1;
      let value = "";
      while (i < inner.length) {
        if (inner[i] === "\\") {
          value += inner[i + 1] ?? "";
          i += 2;
          continue;
        }
        if (inner[i] === '"') {
          i += 1;
          break;
        }
        value += inner[i];
        i += 1;
      }
      if (value.trim()) out.push(value);
      continue;
    }

    if (inner[i] === "'") {
      i += 1;
      let value = "";
      while (i < inner.length && inner[i] !== "'") {
        value += inner[i];
        i += 1;
      }
      if (inner[i] === "'") i += 1;
      if (value.trim()) out.push(value);
      continue;
    }

    let value = "";
    while (i < inner.length && inner[i] !== ",") {
      value += inner[i];
      i += 1;
    }
    const trimmed = value.trim();
    if (trimmed) out.push(trimmed);
  }
  return out;
}

/** Whether a block of text looks like a wiki page's frontmatter. */
function looksLikeWikiYaml(block: string): boolean {
  const trimmed = block.trim();
  if (!trimmed || /^---FILE:/m.test(trimmed)) return false;
  return /^(type|title|tags|related|created|updated|sources):/m.test(trimmed);
}

/**
 * The shapes a page can arrive in, written once and read by both the reader and
 * the slicer below. `\r?\n` so they match text whose line endings are still the
 * file's own as well as text already folded to `\n`.
 */
const FILE_OPENER = /^(?:---[ \t]*\r?\n)?---FILE:.*\r?\n(?:---[ \t]*\r?\n)?/;
const FENCED_FRONTMATTER = /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*\r?\n?([\s\S]*)$/;
const UNFENCED_FRONTMATTER = /^([\s\S]*?)\r?\n---[ \t]*\r?\n?([\s\S]*)$/;

/**
 * Separates a page's frontmatter from its body.
 *
 * Pages written through the ingestion pipeline can arrive with the `---FILE:`
 * opener still attached, or with the frontmatter missing its opening fence, so
 * both are tolerated rather than treated as a page without frontmatter.
 */
export function splitWikiFrontmatter(raw: string): { yaml: string; body: string } {
  let text = raw.replace(/\r\n/g, "\n");
  text = text.replace(FILE_OPENER, "");

  const fenced = FENCED_FRONTMATTER.exec(text);
  if (fenced && looksLikeWikiYaml(fenced[1] ?? "")) {
    return { yaml: fenced[1] ?? "", body: fenced[2] ?? "" };
  }

  const unfenced = UNFENCED_FRONTMATTER.exec(text);
  if (unfenced && looksLikeWikiYaml(unfenced[1] ?? "")) {
    return { yaml: unfenced[1] ?? "", body: unfenced[2] ?? "" };
  }

  return { yaml: "", body: text };
}

/** A run of a page's text, together with the offsets it occupies there. */
type TextSlice = {
  /** Index of the run's first character in the page text as read. */
  start: number;
  /** Index one past the run's last character. */
  end: number;
  /** The run itself, exactly as read. */
  text: string;
};

type WikiFrontmatterSlices = {
  /** Whether a frontmatter block was recognised. */
  hasFrontmatter: boolean;
  /**
   * Everything a writer must leave alone: a leftover `---FILE:` opener when one
   * is present, the fences and the YAML between them, and the line ending that
   * follows the closing fence.
   */
  prefix: TextSlice;
  /** The body. */
  body: TextSlice;
};

/**
 * Locates the frontmatter and body as exact runs of the text that was read.
 *
 * This is the writing half of the split. `splitWikiFrontmatter` answers "what
 * does this page say", so it folds line endings and drops a leftover opener;
 * this answers "which characters may a writer touch", so it changes nothing:
 * `prefix.text + body.text` is the page text unchanged. That is what lets a
 * writer swap the body while every other character keeps the position and the
 * line ending it had on disk.
 *
 * Offsets are JavaScript string indices, not bytes, and nothing is normalised —
 * a page read with CRLF comes back with CRLF.
 *
 * A missing opening fence is reported where it is, not repaired. Adding one is a
 * decision about the page rather than about positions, and belongs to the writer
 * (the kernel makes it in `ensure_wiki_frontmatter`).
 */
export function sliceWikiFrontmatter(raw: string): WikiFrontmatterSlices {
  const opener = FILE_OPENER.exec(raw);
  const contentStart = opener ? opener[0].length : 0;
  const content = raw.slice(contentStart);

  const fenced = FENCED_FRONTMATTER.exec(content);
  if (fenced && looksLikeWikiYaml(fenced[1] ?? "")) {
    return cutAt(raw, contentStart + fenced[0].length - (fenced[2] ?? "").length, true);
  }

  const unfenced = UNFENCED_FRONTMATTER.exec(content);
  if (unfenced && looksLikeWikiYaml(unfenced[1] ?? "")) {
    return cutAt(raw, contentStart + unfenced[0].length - (unfenced[2] ?? "").length, true);
  }

  return cutAt(raw, contentStart, false);
}

/** Cuts a page at *bodyStart*, everything before it being the prefix. */
function cutAt(raw: string, bodyStart: number, hasFrontmatter: boolean): WikiFrontmatterSlices {
  return {
    hasFrontmatter,
    prefix: { start: 0, end: bodyStart, text: raw.slice(0, bodyStart) },
    body: { start: bodyStart, end: raw.length, text: raw.slice(bodyStart) },
  };
}

/** The locators a page's frontmatter claims, in order and without repeats. */
export function parseWikiSourceLocators(raw: string): SourceLocator[] {
  const { yaml } = splitWikiFrontmatter(raw);
  if (!yaml.trim()) return [];
  const match = /^sources:\s*\[([\s\S]*?)\]/m.exec(yaml);
  if (!match) return [];

  const seen = new Set<string>();
  const out: SourceLocator[] = [];
  for (const item of parseYamlFlowList(match[1] ?? "")) {
    const locator = parseSourceLocator(item);
    if (!locator) continue;
    const key = locator.blockId ? `${locator.path}#${locator.blockId}` : locator.path;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(locator);
  }
  return out;
}
