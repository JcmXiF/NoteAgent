import type { MarkdownTreeNode } from "./display-math";
import { findInlineMarks, outerMarks, type InlineMarkKind } from "./inline-marks";

/**
 * Reading a page's own marks the way the editor draws them.
 *
 * A page can underline a word, set it high or low, lay a highlighter over it, and
 * none of those are markdown — the editor draws them from `inline-marks`, the
 * rule both sides of a page share. A markdown reader handed the same page would
 * show the delimiters as written, so every mark the rule finds is turned into the
 * element it stands for before the page is drawn.
 *
 * The work is done on the tree rather than on the text, because the tree is where
 * the parts of a page are already told apart: a code span is a node of its own,
 * so a reader never looks inside one, and a mark's words are the nodes between
 * its delimiters. What is put back is the element, not the characters: a page
 * that says `==x==` keeps saying it.
 *
 * An underline arrives as a pair of tags rather than as text — markdown hands raw
 * HTML over as its own nodes — so a `<u>` and the `</u>` that closes it are
 * paired here and turned into the element they name. A tag standing alone is left
 * as it was, which is what a page that meant to write a tag as text gets.
 *
 * One thing this cannot do: a mark's words have to arrive as one run of prose.
 * Where another construct splits them (`==*a*==`, a mark around a code span or a
 * link), the reader hands the delimiters over in separate pieces and the mark is
 * read as the text it is written as. The editor, reading the page's characters
 * directly, does draw that case — so it is a difference between the two readers,
 * narrow and noted rather than hidden.
 */

/** The element each mark is drawn as. */
const DRAWN_AS: Record<InlineMarkKind, string> = {
  highlight: "mark",
  underline: "u",
  sup: "sup",
  sub: "sub",
};

/** The tags an underline is written with, which a reader hands over as HTML. */
const OPEN_TAG = "<u>";
const CLOSE_TAG = "</u>";

/** The node types whose children are not prose to be marked. */
const OPAQUE = new Set(["code", "inlineCode", "math", "inlineMath", "html"]);

/** Turns every mark `tree` holds into the element it stands for. */
export function liftInlineMarks(tree: MarkdownTreeNode): void {
  if (!Array.isArray(tree.children)) return;
  for (const child of tree.children) {
    if (OPAQUE.has(child.type)) continue;
    liftInlineMarks(child);
  }
  tree.children = readChildren(tree.children);
}

/** The children, with the marks among them turned into elements. */
function readChildren(nodes: MarkdownTreeNode[]): MarkdownTreeNode[] {
  const out: MarkdownTreeNode[] = [];
  let index = 0;
  while (index < nodes.length) {
    const node = nodes[index];
    if (node.type === "html" && written(node) === OPEN_TAG) {
      const close = closingTag(nodes, index + 1);
      if (close !== -1 && words(nodes.slice(index + 1, close))) {
        out.push(element(DRAWN_AS.underline, readChildren(nodes.slice(index + 1, close))));
        index = close + 1;
        continue;
      }
    }
    if (node.type === "text") {
      out.push(...readText(written(node)));
      index += 1;
      continue;
    }
    out.push(node);
    index += 1;
  }
  return out;
}

/** Where the `</u>` closing the tag at `from` stands, or nothing. */
function closingTag(nodes: MarkdownTreeNode[], from: number): number {
  for (let index = from; index < nodes.length; index += 1) {
    const node = nodes[index];
    if (node.type === "html" && written(node) === CLOSE_TAG) return index;
  }
  return -1;
}

/** Whether between two tags there is anything to underline. */
function words(nodes: MarkdownTreeNode[]): boolean {
  return nodes.some((node) => written(node).trim() !== "");
}

/** The run of prose `value`, with the marks among it turned into elements. */
function readText(value: string): MarkdownTreeNode[] {
  const marks = outerMarks(findInlineMarks(value));
  if (!marks.length) return [text(value)];
  const out: MarkdownTreeNode[] = [];
  let cursor = 0;
  for (const mark of marks) {
    if (mark.from > cursor) out.push(text(value.slice(cursor, mark.from)));
    const inner = value.slice(mark.innerFrom, mark.innerTo);
    out.push(element(DRAWN_AS[mark.kind], readText(inner)));
    cursor = mark.to;
  }
  if (cursor < value.length) out.push(text(value.slice(cursor)));
  return out;
}

/**
 * The element a mark is drawn as.
 *
 * It is built as an emphasis node carrying the element's name, which is how a
 * reader is told to draw an element the markdown tree has no name for: the
 * reader's own node is what gives the element its children, and the name is what
 * decides which element it is. Nothing is added around it, so what a page shows
 * is the element and the words, with nothing of this in between.
 */
function element(tagName: string, children: MarkdownTreeNode[]): MarkdownTreeNode {
  return { type: "emphasis", data: { hName: tagName }, children };
}

/** A run of prose. */
function text(value: string): MarkdownTreeNode {
  return { type: "text", value };
}

/** What a node says, words or tags alike. */
function written(node: MarkdownTreeNode): string {
  return String(node.value ?? "");
}
