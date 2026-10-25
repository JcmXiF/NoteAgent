// @vitest-environment happy-dom
import ReactMarkdown from "react-markdown";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { REMARK_PLUGINS, REHYPE_PLUGINS } from "@/lib/markdown";
import { prepareMarkdown } from "@/lib/markdown";

/**
 * The marks a page's own prose carries, read by the reading view.
 *
 * A page is drawn by the editor while it is being written and read elsewhere —
 * in a conversation, in whatever comes next — and every surface has to read the
 * same characters the same way. The editor's half of that is checked in
 * `inline-marks.test.ts`; this is the reading view's half, judged by the elements
 * it puts out for each mark.
 */

/** What the reading view draws `body` as. */
function drawn(body: string): string {
  return renderToStaticMarkup(
    <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS}>
      {prepareMarkdown(body)}
    </ReactMarkdown>,
  );
}

describe("a mark the reading view is handed", () => {
  it("is drawn as the element it stands for", () => {
    expect(drawn("a ==marked== word")).toContain("<mark>marked</mark>");
    expect(drawn("a <u>under</u> word")).toContain("<u>under</u>");
    expect(drawn("x^2^")).toContain("<sup>2</sup>");
    expect(drawn("H~2~O")).toContain("<sub>2</sub>");
  });

  it("keeps markdown's own strike, which the lowered mark must not take", () => {
    expect(drawn("a ~~cut~~ word")).toContain("<del>cut</del>");
    expect(drawn("a ~low~ word")).toContain("<sub>low</sub>");
  });

  it("holds its words, and the marks among them", () => {
    const html = drawn("==H~2~O==");
    expect(html).toContain("<mark>");
    expect(html).toContain("<sub>2</sub>");
  });

  it("is drawn inside the prose it stands in", () => {
    const html = drawn("a **bold ==marked== word**");
    expect(html).toContain("<strong>");
    expect(html).toContain("<mark>marked</mark>");
  });

  it("is not read inside code", () => {
    expect(drawn("`==marked==`")).not.toContain("<mark>");
    expect(drawn("```\n==marked==\n```")).not.toContain("<mark>");
  });

  it("is not read when it is not written as a mark", () => {
    expect(drawn("a == b")).not.toContain("<mark>");
    expect(drawn("a <u>under")).not.toContain("<u>");
    expect(drawn("^a b^")).not.toContain("<sup>");
  });
});
