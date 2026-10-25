import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import { REMARK_PLUGINS, REHYPE_PLUGINS } from "@/lib/markdown";

/**
 * How a display formula a page wrote on one line is read.
 *
 * These go through a reader rather than through the rule's own tree, because the
 * rule is only ever asked for by a reader and what a reader hands it is part of
 * the contract: it has to receive the page's own text, and the positions it reads
 * the delimiters from have to belong to that text.
 */

function read(body: string): string {
  return renderToStaticMarkup(
    <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS}>
      {body}
    </ReactMarkdown>,
  );
}

/** The display formulae in a rendered page. */
function displayed(html: string): number {
  return html.match(/katex-display/g)?.length ?? 0;
}

/** The inline formulae in a rendered page. */
function inline(html: string): number {
  return (html.match(/class="katex"/g)?.length ?? 0) - displayed(html);
}

describe("a formula the page wrote in the display form", () => {
  it("is display when it stands on a line of its own", () => {
    expect(displayed(read("文\n\n$$f(p)=1$$\n\n文"))).toBe(1);
  });

  it("is display in a list, where the prose above it continues the item", () => {
    // The formula stands on its own line, so the page asked for display maths
    // even though the line above it is prose.
    const body = "- 熵的公式可以表示为\n  $$H(X) = -\\sum_i p_i \\log p_i$$\n";
    expect(displayed(read(body))).toBe(1);
    expect(inline(read(body))).toBe(0);
  });

  it("is display at the very start of a page", () => {
    expect(displayed(read("$$x=\\{x_1,x_2\\}$$\n"))).toBe(1);
  });

  it("is display when it is indented no further than a list may indent it", () => {
    expect(displayed(read("- 项目：\n\n  $$\n  a = b\n  $$\n"))).toBe(1);
  });

  it("stays inline when it shares its line with prose", () => {
    // Read as maths inside the sentence, which is what the sentence asked for.
    const body = "其形式为 $$x = y$$ 时成立。";
    expect(displayed(read(body))).toBe(0);
    expect(inline(read(body))).toBe(1);
  });

  it("stays inline when several share one line", () => {
    const body = "$$a$$ 与 $$b$$";
    expect(displayed(read(body))).toBe(0);
    expect(inline(read(body))).toBe(2);
  });
});

describe("what is not maths", () => {
  it("leaves a formula written in a fenced block alone", () => {
    expect(read("```md\n$$f(p)=1$$\n```")).not.toContain("katex");
  });

  it("leaves a formula written in inline code alone", () => {
    expect(read("写 `$$f(p)=1$$` 即可。")).not.toContain("katex");
  });

  it("leaves the delimiters of a formula already written on its own lines", () => {
    expect(displayed(read("$$\n\\begin{aligned}a\\end{aligned}\n$$"))).toBe(1);
  });
});

describe("an environment left without delimiters", () => {
  it("is display when it stands alone in its paragraph", () => {
    const body = "前文\n\n\\begin{aligned}\na &= b\\\\\nc &= d\n\\end{aligned}\n\n后文";
    expect(displayed(read(body))).toBe(1);
  });

  it("is left as text when it shares its paragraph with prose", () => {
    // Ambiguous — the environment could belong to the sentence — so it is not
    // guessed at.
    expect(read("前文 \\begin{aligned}a\\end{aligned} 后文")).not.toContain("katex");
  });
});
