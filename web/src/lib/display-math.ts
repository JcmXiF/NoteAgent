/**
 * Reading a display formula that was written on one line.
 *
 * Whether a formula is display or inline is decided by where it stands, not by
 * how many delimiters it carries: `$$x$$` alone on its line means the same thing
 * as `$$\nx\n$$`. A markdown reader only looks at the delimiters and where the
 * line breaks fall, so it reads the one-line form as inline maths — small, in the
 * middle of a sentence there is no sentence for, and read differently again when
 * a list happens to continue the line above it.
 *
 * This walks the document tree and puts the intent back: a `$$` formula standing
 * on a line of its own becomes a block, whichever paragraph it was written into.
 * Every surface that reads a page — the reading view, the editor, a chat answer —
 * therefore reads it the same way.
 *
 * It works on the tree rather than on the text because an editor must not rewrite
 * what it read: a page that says `$$x$$` keeps saying it, and still comes out the
 * size the page asked for.
 *
 * The same goes for a display environment the writer left without delimiters at
 * all (`\begin{aligned} … \end{aligned}`), which would otherwise be shown as the
 * source text it is.
 *
 * What counts, in full:
 *
 * - A formula is display when its delimiters are `$$`, only whitespace stands
 *   before them on their line, and only whitespace follows them on theirs. Up to
 *   three spaces of indentation are allowed, as inside a list item; four would be
 *   an indented code block, which the reader never hands over as maths.
 * - A formula sharing its line with prose stays inline: `其形式为 $$x$$ 时` asks
 *   for a formula inside the sentence.
 * - An environment counts when the paragraph is nothing but one, which is the
 *   shape a writer leaves when they forget the delimiters. It is taken from the
 *   page's own text and not from the tree, because a reader resolves the two
 *   backslashes of a LaTeX line break into one escaped one.
 * - Both are taken out of the paragraph they stood in, because a display formula
 *   is a block and a paragraph cannot hold one. What is left of the prose stays
 *   as the paragraphs it now is, and the line ending that joined prose to formula
 *   goes with it — it separated the two and has nothing left to separate.
 */

/** The document tree, as far as this rule needs to see it. */
export type MarkdownTreeNode = {
  type: string;
  value?: string;
  children?: MarkdownTreeNode[];
  meta?: string | null;
  data?: unknown;
  position?: { start?: { offset?: number }; end?: { offset?: number } };
};

/** An environment the writer left without delimiters, standing on its own line. */
const BARE_ENVIRONMENT = /^([ \t]*)(\\begin\{[^}]+\}[\s\S]*?\\end\{[^}]+\})[ \t]*(?=\n|$)/m;

/**
 * What a display formula looks like in the tree.
 *
 * This is the shape the markdown reader itself builds for `$$\n…\n$$` — the
 * surrounding element and its class are what tell a renderer to draw the formula
 * on a line of its own, so they are reproduced rather than invented. The value is
 * what stands between the delimiters, which is what a renderer draws.
 *
 * The block also keeps the position of the text it was put together from, because
 * that text is what a writer writes back out: the page said `$$x$$` and has to go
 * on saying it, however the reader chose to read it.
 */
function displayFormula(
  inner: string,
  position?: MarkdownTreeNode["position"],
): MarkdownTreeNode {
  return {
    type: "math",
    meta: null,
    value: inner,
    position,
    data: {
      hName: "pre",
      hChildren: [
        {
          type: "element",
          tagName: "code",
          properties: { className: ["language-math", "math-display"] },
          children: [{ type: "text", value: inner }],
        },
      ],
    },
  };
}

/**
 * The source a tree was read from.
 *
 * A remark transformer is handed the file alongside the tree, and which of the
 * two depends on the reader: `react-markdown` passes a file, and the editor
 * passes the markdown itself.
 */
export function sourceOf(file: unknown): string {
  if (typeof file === "string") return file;
  const value = (file as { value?: unknown } | null)?.value;
  return typeof value === "string" ? value : "";
}

/**
 * Whether a formula stands on a line of its own, as `source` records it.
 *
 * The delimiters are what the formula was read from, so the source is what says
 * whether they were `$$` — the node itself only carries what stood between them.
 */
function standsAlone(node: MarkdownTreeNode, source: string): boolean {
  const start = node.position?.start?.offset;
  const end = node.position?.end?.offset;
  if (start === undefined || end === undefined) return false;
  if (!source.slice(start, end).startsWith("$$")) return false;
  const before = source.slice(0, start).split("\n").pop() ?? "";
  const after = source.slice(end).split("\n")[0] ?? "";
  // Four or more spaces of indentation would make it an indented code block,
  // which the reader never hands over as maths in the first place.
  return before.trim() === "" && !before.startsWith("    ") && after.trim() === "";
}

/** Whether a stretch of the tree's text is nothing but an environment. */
function isEnvironmentOnly(text: string): boolean {
  const match = BARE_ENVIRONMENT.exec(text);
  return match !== null && match[0].trim() === text.trim() && text.trim().startsWith("\\begin");
}

/**
 * The text a paragraph reads as, when it is nothing but words and line endings.
 *
 * The two readers keep a line ending differently — the reading view leaves it in
 * the text it was written in, the editor makes a node of it — so what a paragraph
 * says has to be gathered rather than read off one node. Anything that is not
 * words or a line ending makes it `null`: a paragraph with a formula or emphasis
 * in it is not the environment it happens to look like.
 */
function plainText(nodes: MarkdownTreeNode[]): string | null {
  let text = "";
  for (const node of nodes) {
    if (node.type === "text") text += String(node.value ?? "");
    else if (node.type === "break") text += "\n";
    else return null;
  }
  return text;
}

/**
 * An environment as the page wrote it, which is what a drawer has to be given.
 *
 * The tree carries the text as a reader made sense of it, and a reader takes the
 * two backslashes of a LaTeX line break for one escaped backslash — a formula
 * drawn from that would lose every row but the last. What the page wrote is what
 * the position says, so the source is read for it, and the gathered text is kept
 * only when there is no position to read from.
 */
function environmentWritten(
  node: MarkdownTreeNode,
  source: string,
  gathered: string,
): string {
  const start = node.position?.start?.offset;
  const end = node.position?.end?.offset;
  if (start === undefined || end === undefined) return gathered.trim();
  const written = source.slice(start, end).trim();
  return isEnvironmentOnly(written) ? written : gathered.trim();
}

/** A paragraph's children without the text that only spaces a line out. */
function meaningful(nodes: MarkdownTreeNode[]): MarkdownTreeNode[] {
  return nodes.filter((node) => node.type !== "text" || String(node.value ?? "").trim() !== "");
}

/**
 * The prose left when the line ending that led into the formula is taken away.
 *
 * The formula stood at the end of its line, so the break before it is the one
 * the formula was given and not a break in the prose: kept, it would keep the
 * block formula and the sentence around it in one paragraph, which is the shape
 * this rule exists to undo — and the shape would come back different on the next
 * read, so a page would not survive a second trip through the editor.
 */
function withoutTrailingBreak(nodes: MarkdownTreeNode[]): MarkdownTreeNode[] {
  const kept = [...nodes];
  while (kept.length > 0) {
    const last = kept[kept.length - 1];
    if (last.type === "break") {
      kept.pop();
      continue;
    }
    if (last.type === "text") {
      const value = String(last.value ?? "");
      const trimmed = value.replace(/[ \t]*\r?\n[ \t]*$/, "");
      if (trimmed === value) break;
      if (trimmed === "") {
        kept.pop();
        continue;
      }
      kept[kept.length - 1] = { ...last, value: trimmed };
    }
    break;
  }
  return kept;
}

/**
 * The prose left when the line ending that followed the formula is taken away.
 *
 * Takes the node the prose now starts with, or nothing when that node was only
 * the break out of the formula's line.
 */
function withoutLeadingBreak(node: MarkdownTreeNode): MarkdownTreeNode | undefined {
  if (node.type === "break") return undefined;
  if (node.type !== "text") return node;
  const value = String(node.value ?? "");
  const trimmed = value.replace(/^[ \t]*\r?\n[ \t]*/, "");
  if (trimmed === value) return node;
  return trimmed === "" ? undefined : { ...node, value: trimmed };
}

/**
 * Reads the tree, turning every display formula written on one line into the
 * shape a formula standing on lines of its own already has.
 */
export function liftDisplayMath(tree: MarkdownTreeNode, source: string): void {
  if (!Array.isArray(tree.children)) return;
  for (const child of tree.children) liftDisplayMath(child, source);

  const lifted: MarkdownTreeNode[] = [];
  for (const child of tree.children) {
    if (child.type !== "paragraph" || !Array.isArray(child.children)) {
      lifted.push(child);
      continue;
    }
    // A paragraph that is nothing but an environment is that environment: the
    // reader never handed it over as maths, and the whole of it is one formula.
    const gathered = plainText(child.children);
    if (gathered !== null && isEnvironmentOnly(gathered)) {
      lifted.push(displayFormula(environmentWritten(child, source, gathered), child.position));
      continue;
    }
    const parts: MarkdownTreeNode[] = [];
    let prose: MarkdownTreeNode[] = [];
    // Whether a formula was just taken out of this paragraph, so that what
    // follows it is prose again and no longer carries the break into it.
    let afterFormula = false;
    const flush = (beforeFormula: boolean) => {
      const kept = beforeFormula ? withoutTrailingBreak(prose) : prose;
      if (meaningful(kept).length > 0) parts.push({ ...child, children: kept });
      prose = [];
    };
    for (const node of child.children) {
      if (node.type === "inlineMath" && standsAlone(node, source)) {
        flush(true);
        parts.push(displayFormula(String(node.value ?? ""), node.position));
        afterFormula = true;
        continue;
      }
      if (afterFormula) {
        afterFormula = false;
        const first = withoutLeadingBreak(node);
        if (first === undefined) continue;
        prose.push(first);
        continue;
      }
      prose.push(node);
    }
    flush(false);
    lifted.push(...(parts.length > 0 ? parts : [child]));
  }
  tree.children = lifted;
}
