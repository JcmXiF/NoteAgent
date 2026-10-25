/**
 * What a `[[wikilink]]` is, and where one counts.
 *
 * The reader that turns a link into something clickable and the writer that puts
 * one on a page have to agree, so the rule lives here and is compiled from the
 * same pattern string as `src/noteagent/wiki/link_syntax.py`.
 *
 * The rule, in full:
 *
 * - A link is `[[target]]` or `[[target|label]]`; the target runs to the first
 *   `|` and the label is what the page shows instead of it.
 * - `![[…]]` is an embed — it asks for a file's content, which this app has no
 *   way to resolve — so it is not a link.
 * - A link never spans a line end. A `[[` with no `]]` after it is just text.
 * - Inside code — a fenced block or an inline backtick span — brackets are code.
 *
 * A target cannot hold `|`, `]` or a line end, because any of them would end the
 * link early and hand the reader a different target than the writer meant. A
 * label may hold `|` and `[`; only `]` and a line end cut it short.
 */

/** What counts as a link. The kernel compiles this same string. */
export const WIKILINK_PATTERN = String.raw`(?<!!)\[\[([^\]|\n]+)(?:\|([^\]\n]*))?\]\]`;

/** A fence opens on its own line, with three or more backticks or tildes. */
const FENCE_LINE = /^[ \t]*(`{3,}|~{3,})/;

/** Every link in the text, in order, embeds and code aside. */
const EVERY_LINK = new RegExp(WIKILINK_PATTERN, "g");

/**
 * The regions a reader treats as code, as `[start, end)` offsets.
 *
 * Fences are taken first and swallow everything between them, so a backtick
 * inside a fenced block does not open an inline span. A fence closes on a run of
 * its own character at least as long as the one that opened it — that is what
 * keeps a `` ``` ``-delimited example inside a four-backtick block. An unclosed
 * fence runs to the end of the text.
 */
export function codeSpans(text: string): Array<[number, number]> {
  const spans: Array<[number, number]> = [];
  let opened: { char: string; width: number; at: number } | null = null;
  let offset = 0;
  for (const line of (text ?? "").split(/(?<=\n)/)) {
    const fence = FENCE_LINE.exec(line);
    if (fence) {
      const run = fence[1];
      if (!opened) {
        opened = { char: run[0], width: run.length, at: offset };
      } else if (run[0] === opened.char && run.length >= opened.width) {
        spans.push([opened.at, offset + line.length]);
        opened = null;
      }
    }
    offset += line.length;
  }
  if (opened) spans.push([opened.at, text.length]);
  return spans.concat(inlineSpans(text, spans)).sort((a, b) => a[0] - b[0]);
}

/** Backtick spans outside the fences, each closed by a run of its own width. */
function inlineSpans(text: string, fenced: Array<[number, number]>): Array<[number, number]> {
  const spans: Array<[number, number]> = [];
  let index = 0;
  while (index < text.length) {
    if (text[index] !== "`" || inside(index, fenced)) {
      index += 1;
      continue;
    }
    const width = runWidth(text, index);
    const closed = closingRun(text, index + width, width);
    if (closed === null) {
      index += width;
      continue;
    }
    spans.push([index, closed]);
    index = closed;
  }
  return spans;
}

function runWidth(text: string, start: number): number {
  let index = start;
  while (index < text.length && text[index] === "`") index += 1;
  return index - start;
}

/** Index just past the run of exactly `width` backticks that closes a span. */
function closingRun(text: string, start: number, width: number): number | null {
  let index = start;
  while (index < text.length && text[index] !== "\n") {
    if (text[index] !== "`") {
      index += 1;
      continue;
    }
    const run = runWidth(text, index);
    if (run === width) return index + run;
    index += run;
  }
  return null;
}

function inside(index: number, spans: Array<[number, number]>): boolean {
  return spans.some(([start, end]) => start <= index && index < end);
}

/**
 * Applies a rewrite to everything except code.
 *
 * Code is shown as written, so a rewrite that reads prose — turning links into
 * addresses, say — must leave it alone.
 */
export function mapOutsideCode(body: string, rewrite: (text: string) => string): string {
  const spans = codeSpans(body);
  let out = "";
  let cursor = 0;
  for (const [start, end] of spans) {
    const from = Math.max(cursor, start);
    out += rewrite(body.slice(cursor, from)) + body.slice(from, end);
    cursor = end;
  }
  return out + rewrite(body.slice(cursor));
}

/** One link and the span it occupies. */
interface WikiLink {
  target: string;
  label: string | null;
  /** What the link reads as: its label when it has one, else its target. */
  display: string;
  start: number;
  end: number;
}

/** Every link in the text, in the order it appears, embeds and code aside. */
export function findWikiLinks(text: string): WikiLink[] {
  const skip = codeSpans(text ?? "");
  const found: WikiLink[] = [];
  for (const match of (text ?? "").matchAll(EVERY_LINK)) {
    const start = match.index ?? 0;
    if (inside(start, skip)) continue;
    const target = match[1].trim();
    if (!target) continue;
    const raw = match[2];
    const label = raw === undefined ? null : raw.trim();
    found.push({
      target,
      label,
      display: label || target,
      start,
      end: start + match[0].length,
    });
  }
  return found;
}

/** Characters that would end the link early, per position. A label may hold `|`
 *  and `[` — only `]` and a line end cut it short. */
const TARGET_FORBIDDEN = ["|", "]", "\n", "\r"];
const LABEL_FORBIDDEN = ["]", "\n", "\r"];

/**
 * Writes a link in the form a reader of the same text will recognise.
 *
 * A label that repeats the target is dropped rather than written out, so the
 * shortest form that still means the same thing is the one that reaches the
 * page. Throws on a target or label holding a character that would end the link:
 * writing a link that reads as a different target is worse than writing none.
 */
export function formatWikiLink(target: string, label?: string | null): string {
  const body = (target ?? "").trim();
  if (!body) throw new Error("a wikilink needs a target");
  reject(body, "target", TARGET_FORBIDDEN);
  const alias = (label ?? "").trim();
  if (alias) reject(alias, "label", LABEL_FORBIDDEN);
  if (alias && alias !== body) return `[[${body}|${alias}]]`;
  return `[[${body}]]`;
}

function reject(text: string, what: string, forbidden: string[]): void {
  for (const char of forbidden) {
    if (text.includes(char)) {
      const shown = char === "\n" || char === "\r" ? "newline" : char;
      throw new Error(`a wikilink ${what} cannot contain ${shown}: ${JSON.stringify(text)}`);
    }
  }
}
