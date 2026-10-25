import { CLIP_MARK_LINE, HEADING_LINE } from "@/lib/marker-lines";
import { codeSpans } from "@/lib/wikilink";

/**
 * Where the sections of a page body begin and end.
 *
 * A section is read the way the rest of the app reads it: a heading (`##`
 * through `######`) together with the stamped marker that owns it, running up to
 * the next such heading. Which marker owns a heading, and what the gap between
 * the two may hold, is the binding rule in `marker-lines`; what counts as code —
 * where a line that looks like a heading is a sample, not a boundary — is in
 * `wikilink`.
 *
 * A `na-clip` marker names the material a section was written from, and it is
 * what puts the range where it is: a section a clip marker owns starts at that
 * marker's line rather than at its heading. No other line does that — an
 * `na-ecg` marker, a comment that is not a marker, or a line of prose is a line
 * in the gap like any other, and the heading below it stands on its own.
 *
 * Offsets are into the text exactly as it was handed in — line endings are not
 * folded and a missing opening frontmatter fence is not repaired — so a caller
 * holding the text read off disk can name the same stretch again.
 *
 * **Offsets are character offsets, not byte offsets**, and the character is this
 * language's own: JavaScript indexes a string by UTF-16 code unit. The Python
 * mirror (`noteagent.ingest.sections`) indexes by code point, so on text with a
 * character outside the basic plane the two produce different numbers for the
 * same section. That is fine because the numbers never cross the language
 * boundary — a caller sends the section's text and the core locates it again by
 * itself. It does mean a number is only meaningful next to the text it was
 * measured on, and that anything comparing the two sides has to compare the text
 * they cut out, not the offsets.
 *
 * Two ranges never overlap and together they cover the text: the part before the
 * first heading (`preamble`) plus every section. That is the invariant the tests
 * check, because a gap or an overlap would let an edit move text nobody looked
 * at.
 */

export type Section = {
  level: number;
  heading: string;
  /** First character of the boundary: the owning `na-clip` marker's line, or the heading's. */
  start: number;
  /** First character of the owning `na-clip` marker line, or `null` when none. */
  markerStart: number | null;
  headingStart: number;
  /** End of the heading's text, before its line ending. */
  headingEnd: number;
  /** First character after the heading's line — where the section's prose begins. */
  bodyStart: number;
  /** First character of the next boundary, or `text.length` for the last section. */
  end: number;
  collectionId: string;
  blockIds: string[];
};

export type SectionedBody = {
  /** `[start, end)` of everything before the first heading. */
  preamble: { start: number; end: number };
  sections: Section[];
};

type Line = { start: number; content: string; contentEnd: number; nextStart: number };

/** Lines with their offsets, keeping every line ending as it was. */
function splitLines(text: string): Line[] {
  const lines: Line[] = [];
  let start = 0;
  while (start < text.length) {
    const newline = text.indexOf("\n", start);
    if (newline < 0) {
      const raw = text.slice(start);
      const content = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
      lines.push({ start, content, contentEnd: start + content.length, nextStart: text.length });
      break;
    }
    const raw = text.slice(start, newline);
    const content = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
    lines.push({ start, content, contentEnd: start + content.length, nextStart: newline + 1 });
    start = newline + 1;
  }
  return lines;
}

/**
 * Walk up over the blank lines a marker may still bind across.
 *
 * A marker owns the heading below it when only blank lines stand between the
 * two; a line of prose, a list or another marker ends the wait, so the earlier
 * marker owns nothing. Returns the index of the line that ended the walk — the
 * candidate owner, or `-1`.
 */
function gapAbove(lines: Line[], headingIndex: number): number {
  let index = headingIndex - 1;
  while (index >= 0 && lines[index].content.trim() === "") index -= 1;
  return index;
}

/** Whether `line` is the marker a boundary follows. */
function isClipMarker(line: string): boolean {
  return CLIP_MARK_LINE.test(line);
}

function parseBlocks(raw: string | undefined): string[] {
  if (!raw) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of raw.split(",")) {
    const value = item.trim().toLowerCase();
    if (value && !seen.has(value)) {
      seen.add(value);
      out.push(value);
    }
  }
  return out;
}

/** Whether an offset falls inside one of `spans`. */
function inside(offset: number, spans: Array<[number, number]>): boolean {
  return spans.some(([start, end]) => start <= offset && offset < end);
}

/** Splits `text` into a preamble and its sections, by exact offsets. */
export function readSections(text: string): SectionedBody {
  const lines = splitLines(text);
  const code = codeSpans(text);

  const boundaries: {
    start: number;
    headingIndex: number;
    marker: number | null;
  }[] = [];
  lines.forEach((line, index) => {
    // A heading inside code — a fenced block or an inline backtick span — is a
    // sample, not a boundary: splitting on it would hand a caller a section the
    // page does not have.
    if (inside(line.start, code)) return;
    if (!HEADING_LINE.test(line.content)) return;
    const above = gapAbove(lines, index);
    // Only a clip marker moves the boundary, and only from the line directly
    // above: the core pairs a section with itself by the text of the range, so a
    // range opening anywhere else would no longer start at the heading's text.
    const marker = above >= 0 && isClipMarker(lines[above].content) ? above : null;
    boundaries.push({
      start: marker === null ? line.start : lines[marker].start,
      headingIndex: index,
      marker,
    });
  });

  const sections: Section[] = boundaries.map((boundary, position) => {
    const isLast = position + 1 >= boundaries.length;
    const nextStart = isLast ? text.length : boundaries[position + 1].start;
    const headingLine = lines[boundary.headingIndex];
    const heading = HEADING_LINE.exec(headingLine.content);
    let collectionId = "";
    let blockIds: string[] = [];
    if (boundary.marker !== null) {
      const markerMatch = CLIP_MARK_LINE.exec(lines[boundary.marker].content);
      if (markerMatch) {
        collectionId = (markerMatch[1] ?? "").trim();
        blockIds = parseBlocks(markerMatch[2]);
      }
    }
    return {
      level: (heading?.[1] ?? "##").length,
      heading: (heading?.[2] ?? "").trim(),
      start: boundary.start,
      markerStart: boundary.marker === null ? null : lines[boundary.marker].start,
      headingStart: headingLine.start,
      headingEnd: headingLine.contentEnd,
      bodyStart: headingLine.nextStart,
      end: nextStart,
      collectionId,
      blockIds,
    };
  });

  return {
    preamble: { start: 0, end: boundaries.length > 0 ? boundaries[0].start : text.length },
    sections,
  };
}
