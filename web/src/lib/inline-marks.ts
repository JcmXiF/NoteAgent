import { codeSpans } from "./wikilink";

/**
 * The marks a page's prose can carry that markdown itself does not know.
 *
 * A page can underline a word, set it high or low, or lay a highlighter over it,
 * and none of those are markdown. They are accepted here rather than left to a
 * dialect, because the editors this one is measured against have them and a
 * writer moving between them expects the words to keep meaning what they meant.
 *
 * The rule lives in one place on purpose. A page is read in more than one way —
 * written in the editor, quoted into a conversation, read by whatever comes next
 * — and each of those reads the page's text for itself. A rule written twice
 * would drift, and the drift would show up as a page meaning one thing where it
 * was written and another where it was read, which is the one outcome worth
 * paying a shared scanner for.
 *
 * What counts, in full, with `code` spans and fenced blocks set aside:
 *
 * - `==words==` is a highlight. The inner words carry no `=` and no line end,
 *   and neither end of them is a space: a `=` beside them would make the pair
 *   ambiguous, and `== a ==` is a writer spacing their marks out rather than
 *   highlighting ` a `.
 * - `<u>words</u>` is an underline. The words are whatever stands between the
 *   two tags — tags are explicit about where they begin and end, so spaces are
 *   the writer's business — and a newline inside them is not.
 * - `^words^` is raised and `~words~` is lowered. These mirror the reader of
 *   the syntax tree, which breaks at a space and takes a backslash to mean the
 *   character after it is ordinary: `^2^` is `2` raised, `^a b^` is text as
 *   written, and `^a\ b^` is `a b` raised.
 * - A doubled delimiter is not one of these: `~~words~~` is strikethrough, which
 *   markdown does know.
 *
 * A mark's words may hold another mark (`==H~2~O==`), and both are reported: the
 * outer one first, then the ones its words hold. What a reader wants is the
 * nesting, and a rule that reported only the outer one would leave the reading
 * view drawing less than the editor does.
 */

/** What one of the page's own marks is called. */
export type InlineMarkKind = "highlight" | "underline" | "sup" | "sub";

/** A mark found in the text, delimiters and all. */
export type InlineMark = {
  kind: InlineMarkKind;
  /** The run the mark occupies, both delimiters included. */
  from: number;
  /** Just past the closing delimiter. */
  to: number;
  /** Where the words begin. */
  innerFrom: number;
  /** Where the words end. */
  innerTo: number;
};

/** The marks written between a pair of fixed delimiters. */
const PAIRED: ReadonlyArray<{ kind: InlineMarkKind; open: string; close: string }> = [
  { kind: "highlight", open: "==", close: "==" },
  { kind: "underline", open: "<u>", close: "</u>" },
];

/** The marks written between a single character used twice. */
const SINGLE: ReadonlyArray<{ kind: InlineMarkKind; char: string }> = [
  { kind: "sup", char: "^" },
  { kind: "sub", char: "~" },
];

/**
 * Every mark in `text`, in the order they stand, a mark's own words included.
 *
 * The order is the one a page is read in: a mark comes before the marks written
 * inside its words, so a reader walking the list can tell nesting from sequence.
 */
export function findInlineMarks(text: string): InlineMark[] {
  const source = text ?? "";
  const found: InlineMark[] = [];
  scan(source, 0, source.length, codeSpans(source), found);
  return found;
}

/**
 * The marks of `marks` that are not written inside another mark's words.
 *
 * What is being read for decides which of the two answers it wants: an editor
 * dresses every mark it can find, and a reader laying one mark inside another
 * wants the outer mark first, so it is handed them one at a time.
 */
export function outerMarks(marks: readonly InlineMark[]): InlineMark[] {
  const kept: InlineMark[] = [];
  let outer: InlineMark | null = null;
  for (const mark of marks) {
    if (outer && mark.from >= outer.innerFrom && mark.to <= outer.innerTo) continue;
    kept.push(mark);
    outer = mark;
  }
  return kept;
}

/** Reads the marks between `from` and `to`, and the ones their words hold. */
function scan(text: string, from: number, to: number, skip: Span[], out: InlineMark[]): void {
  let index = from;
  while (index < to) {
    const mark = readAt(text, index, skip);
    // A mark closing beyond what is being read is not this run's mark: it
    // reaches out of the words it stands in.
    if (!mark || mark.to > to) {
      index += 1;
      continue;
    }
    out.push(mark);
    scan(text, mark.innerFrom, mark.innerTo, skip, out);
    index = mark.to;
  }
}

/** The mark written at `index`, or nothing when none begins there. */
function readAt(text: string, index: number, skip: Span[]): InlineMark | null {
  if (inside(index, skip)) return null;
  for (const pair of PAIRED) {
    if (!text.startsWith(pair.open, index)) continue;
    const innerFrom = index + pair.open.length;
    const innerTo = closingPair(text, innerFrom, pair, skip);
    if (innerTo === null) continue;
    if (!wordsReadable(text.slice(innerFrom, innerTo), pair.kind)) continue;
    return {
      kind: pair.kind,
      from: index,
      to: innerTo + pair.close.length,
      innerFrom,
      innerTo,
    };
  }
  for (const single of SINGLE) {
    if (text[index] !== single.char) continue;
    // Two of them in a row are the doubled delimiter markdown knows, and the
    // second of the two is not the beginning of a mark of its own.
    if (text[index + 1] === single.char) continue;
    if (index > 0 && text[index - 1] === single.char) continue;
    const innerTo = closingSingle(text, index + 1, single.char);
    if (innerTo === null) continue;
    return { kind: single.kind, from: index, to: innerTo + 1, innerFrom: index + 1, innerTo };
  }
  return null;
}

/** Where the closing delimiter of a paired mark stands, or nothing. */
function closingPair(text: string, start: number, pair: Pair, skip: Span[]): number | null {
  let index = start;
  while (index < text.length) {
    if (inside(index, skip)) return null;
    if (pair.close === "==") {
      if (text[index] === "\n") return null;
      if (text[index] === "=") {
        const width = runWidth(text, index, "=");
        // Only two of them close this mark; a run of another width is prose.
        if (width === 2) return index;
        index += width;
        continue;
      }
      index += 1;
      continue;
    }
    if (text.startsWith(pair.close, index)) return index;
    if (text[index] === "\n") return null;
    index += 1;
  }
  return null;
}

/** Where the closing character of a raised or lowered mark stands, or nothing. */
function closingSingle(text: string, start: number, char: string): number | null {
  let index = start;
  while (index < text.length) {
    const at = text[index];
    if (at === "\\") {
      index += 2;
      continue;
    }
    if (at === "\n") return null;
    if (at === char) return index > start ? index : null;
    if (WHITESPACE.test(at)) return null;
    index += 1;
  }
  return null;
}

/** Whether what stands between a pair of delimiters reads as a mark's words. */
function wordsReadable(inner: string, kind: InlineMarkKind): boolean {
  if (!inner.trim()) return false;
  if (inner.includes("\n")) return false;
  if (kind !== "highlight") return true;
  return inner.includes("=") === false && inner.trim() === inner;
}

/** How many of `char` stand at `start`. */
function runWidth(text: string, start: number, char: string): number {
  let index = start;
  while (index < text.length && text[index] === char) index += 1;
  return index - start;
}

/** Whether `index` falls inside one of the code spans. */
function inside(index: number, spans: Span[]): boolean {
  return spans.some(([start, end]) => start <= index && index < end);
}

/** A stretch of the page that is code rather than prose. */
type Span = [number, number];

/** A pair of delimiters that open and close a mark. */
type Pair = { kind: InlineMarkKind; open: string; close: string };

const WHITESPACE = /\s/;
