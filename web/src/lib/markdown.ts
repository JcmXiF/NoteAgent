import rehypeKatex from "rehype-katex";
import type { Options } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { liftDisplayMath, sourceOf, type MarkdownTreeNode } from "./display-math";
import { liftInlineMarks } from "./remark-inline-marks";
import { findWikiLinks, mapOutsideCode } from "./wikilink";

/**
 * What every markdown reader in the app has to agree on.
 *
 * A page must read the same wherever it is shown — the document view, the editor,
 * a chat transcript — so the rules and the text preparation live here instead of
 * in whichever component happens to render first. The maths stylesheet is loaded
 * with the app shell, next to the other global styles.
 */

/**
 * Reads a display formula written on one line as the display formula it is.
 *
 * The rule itself lives beside the tree it reads; this is only the reader's way
 * of asking for it. It has to come after maths is read, which is what the node it
 * looks for comes from.
 */
const remarkDisplayMath = () => (tree: MarkdownTreeNode, file: unknown) => {
  liftDisplayMath(tree, sourceOf(file));
};

/** Reads the marks a page carries that markdown has no name for. */
const remarkInlineMarks = () => (tree: MarkdownTreeNode) => {
  liftInlineMarks(tree);
};

/**
 * One entry of a reader's plugin list: a plugin, or a plugin with the options it
 * takes.
 *
 * Written out rather than inferred, because a plugin that takes options is called
 * to make one and the types of the plugin itself say that call returns nothing —
 * so a list that hands `remark-gfm` its options has to say what an entry is.
 */
type ReaderPlugin = NonNullable<Options["remarkPlugins"]>[number];

/**
 * Remark plugins, in the order a page is read in.
 *
 * Maths has to be read before the display rule looks for it, which is what puts
 * those two in this order; the page's own marks are read from the tree once it is
 * built, so they come last.
 */
export const REMARK_PLUGINS: ReaderPlugin[] = [
  // A doubled tilde is strikethrough and a single one raises no meaning of its
  // own, so the reader is told not to read one as the other: `~x~` is the
  // editor's lowered mark, and markdown's own strike is written `~~x~~`.
  [remarkGfm, { singleTilde: false }],
  remarkMath,
  remarkDisplayMath,
  remarkInlineMarks,
];

/** Rehype plugins for react-markdown, in order. */
export const REHYPE_PLUGINS = [rehypeKatex];

/**
 * The scheme wiki links carry.
 *
 * A page names another by title, file or path, and only the view can turn that
 * into a file. A scheme of our own keeps those links apart from the addresses a
 * page may also contain, and gives the renderer a reason to pass them through
 * instead of stripping them as an unknown scheme.
 */
export const WIKI_SCHEME = "wiki:";

/** A bracket in a link's text would end the text early. */
function escapeLinkText(text: string): string {
  return text.replace(/[[\]]/g, "\\$&");
}

/**
 * Turns wiki links into links the renderer styles, carrying their target for the
 * view to resolve. Code is left as written, so an example stays an example.
 */
export function transformWikiLinks(body: string): string {
  return mapOutsideCode(body, (text) => {
    const links = findWikiLinks(text);
    if (!links.length) return text;
    let out = "";
    let cursor = 0;
    for (const link of links) {
      // Angle brackets let the target keep spaces, parentheses and non-ASCII
      // characters; the parser takes them off again before the view sees it.
      out +=
        text.slice(cursor, link.start) +
        `[${escapeLinkText(link.display)}](<${WIKI_SCHEME}${link.target}>)`;
      cursor = link.end;
    }
    return out + text.slice(cursor);
  });
}

/** Everything a renderer needs done to a page before showing it. */
export function prepareMarkdown(body: string): string {
  return transformWikiLinks(body);
}
