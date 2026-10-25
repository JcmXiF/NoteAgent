// @vitest-environment happy-dom
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it } from "vitest";
import { MarkdownPage } from "@/components/docs/MarkdownPage";
import { NotePreview } from "@/components/notes/NotePreview";
import "@/i18n";
import { useDocsStore } from "@/stores/docs-store";

/**
 * A remark as it is written and as it is read have to be the same text.
 *
 * What is written on one screen is previewed on it and drawn again beside the page
 * it was written about, and a reader who sees a formula, a table or a link work
 * while writing and fail once it is written has been told something untrue. The
 * two readings are therefore compared to each other, not each to what it happens
 * to produce: that is the only way this catches one of them being changed alone.
 *
 * The comparison leaves out the room each reading gives the text and the space a
 * renderer puts between two blocks, since neither is anything a reader sees. What
 * is compared is what the text becomes — every element, every class, every word.
 */

const PROSE = [
  "互信息用 **KL** 度量，见 [[散度]]，其中 $a^2$ 是常数。",
  "",
  "> 引用一句。",
  "",
  "1. 第一项",
  "2. 第二项",
  "",
  "| 项 | 值 |",
  "| --- | --- |",
  "| 下界 | 0 |",
].join("\n");

const SECTIONS = ["# 互信息", "", "开头一段。", "", "## 定义", "", PROSE].join("\n");

const PAGE = ["# 互信息", "", "开头一段。", "", "## 定义", "", "互信息衡量依赖的程度。", ""].join(
  "\n",
);

beforeEach(() => {
  // Both readings resolve links the same way here, as they do when the app draws
  // them: the page is given the store's own answer, and the preview takes it.
  useDocsStore.setState({
    files: [{ path: "wiki/概念/散度.md", name: "散度.md", title: "散度" }],
  });
});

/** What the text became, without the room around it or between its blocks. */
function shape(html: string): string {
  return html
    .replace(/^<div[^>]*>/, "")
    .replace(/<\/div>$/, "")
    .replace(/>\s+</g, "><");
}

function preview(body: string): string {
  return shape(renderToStaticMarkup(<NotePreview body={body} />));
}

function page(raw: string): string {
  const isKnownLink = useDocsStore.getState().hasPage;
  return shape(renderToStaticMarkup(<MarkdownPage raw={raw} isKnownLink={isKnownLink} />));
}

describe("a remark as it is written and as it is read", () => {
  it("reads the same whichever of the two is drawing it", () => {
    expect(preview(PROSE)).toBe(page(PROSE));
  });

  it("reads the same when the page it is written about has sections", () => {
    expect(preview(SECTIONS)).toBe(page(SECTIONS));
  });

  it("draws a remark in the page the way the writing screen previews it", () => {
    const drawn = renderToStaticMarkup(
      <MarkdownPage
        raw={PAGE}
        isKnownLink={useDocsStore.getState().hasPage}
        notes={[
          {
            id: "n1",
            page: "wiki/互信息.md",
            anchor: "定义",
            quote: "",
            created: "2026-10-11T10:00:00+08:00",
            updated: "2026-10-11T10:00:00+08:00",
            body: PROSE,
          },
        ]}
      />,
    );
    expect(shape(drawn)).toContain(preview(PROSE));
  });

  it("draws a link as a link in both, so following one works either way", () => {
    expect(preview(PROSE)).toMatch(/<a href="#[^"]*"[^>]*>散度<\/a>/);
    expect(page(SECTIONS)).toContain('<a href="#');
  });

  it("draws a formula as a formula in both", () => {
    expect(preview(PROSE)).toContain("katex");
    expect(page(SECTIONS)).toContain("katex");
  });
});
