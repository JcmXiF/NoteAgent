// @vitest-environment happy-dom
import { renderToStaticMarkup } from "react-dom/server";
import { beforeAll, describe, expect, it } from "vitest";
import { MarkdownPage } from "@/components/docs/MarkdownPage";
import i18n from "@/i18n";
import type { Note } from "@/lib/api/notes";
import { ANCHOR_END, ANCHOR_PREAMBLE } from "@/lib/note-placement";

/**
 * Where a remark is drawn among the page it was written about.
 *
 * A page is drawn a section at a time so a remark can sit at the join between two
 * of them. What that has to get right is order — the remark after the section it
 * belongs to and before the next heading — and that a remark with nowhere to go is
 * said to be without a place rather than put somewhere. So the markup is judged
 * where the two sit relative to each other, not searched for on its own.
 */

const PAGE = [
  "# 互信息",
  "",
  "开头一段。",
  "",
  "## 定义",
  "",
  "互信息衡量两个随机变量相互依赖的程度，取值非负。",
  "",
  "## 计算式",
  "",
  "离散情形按求和计算，连续情形按积分计算。",
  "",
].join("\n");

beforeAll(async () => {
  // The wording is part of what is being checked, so it is pinned rather than
  // left to whichever language the test machine reports.
  await i18n.changeLanguage("zh");
});

function note(over: Partial<Note> & { id: string }): Note {
  return {
    page: "wiki/concepts/互信息.md",
    anchor: "",
    quote: "",
    created: "2026-10-11T10:00:00.000000+08:00",
    updated: "2026-10-11T10:00:00.000000+08:00",
    body: "一条备注。",
    ...over,
  };
}

function drawn(notes: Note[] = []): string {
  return renderToStaticMarkup(<MarkdownPage raw={PAGE} notes={notes} />);
}

/** Where `needle` was drawn, failing the test when it was not drawn at all. */
function at(html: string, needle: string): number {
  const index = html.indexOf(needle);
  expect(index, `"${needle}" is drawn at all`).toBeGreaterThanOrEqual(0);
  return index;
}

describe("drawing a page with remarks about it", () => {
  it("draws a remark past the section it names and before the next one", () => {
    const html = drawn([note({ id: "a", anchor: "定义", body: "这里有个疑问。" })]);
    expect(at(html, "互信息衡量两个随机变量")).toBeLessThan(at(html, "这里有个疑问。"));
    expect(at(html, "这里有个疑问。")).toBeLessThan(at(html, "计算式"));
  });

  it("draws the page's own start and end where they are named", () => {
    const html = drawn([
      note({ id: "a", anchor: ANCHOR_PREAMBLE, body: "最前面的一条。" }),
      note({ id: "b", anchor: ANCHOR_END, body: "最后面的一条。" }),
    ]);
    expect(at(html, "最前面的一条。")).toBeGreaterThan(at(html, "开头一段。"));
    expect(at(html, "最前面的一条。")).toBeLessThan(at(html, "定义"));
    expect(at(html, "最后面的一条。")).toBeGreaterThan(at(html, "按积分计算。"));
  });

  it("reads a remark's own text the way it reads the page", () => {
    const html = drawn([
      note({ id: "a", anchor: "定义", body: "关于 **互信息** 的疑问。" }),
    ]);
    expect(html).toContain("<aside");
    expect(html).toContain("<strong>互信息</strong>");
  });

  it("keeps a link in a remark working like a link in the page", () => {
    const html = drawn([note({ id: "a", anchor: "定义", body: "见 [[计算式]]。" })]);
    expect(html).toMatch(/<a href="#[^"]*"[^>]*>计算式<\/a>/);
  });

  it("says a remark has nowhere to go instead of putting it somewhere", () => {
    const html = drawn([
      note({
        id: "a",
        anchor: "已经删掉的标题",
        quote: "页面上没有留下这句话。",
        body: "无处安放的一条。",
      }),
    ]);
    expect(html).not.toContain("无处安放的一条。");
    expect(html).toContain("还没放进正文");
  });

  it("draws the page without remarks as it always did", () => {
    const html = drawn();
    expect(html).not.toContain("<aside");
    expect(html).not.toContain("还没放进正文");
    expect(at(html, "互信息衡量两个随机变量")).toBeLessThan(at(html, "按积分计算。"));
  });
});

/**
 * Pointing at a remark from the page it was written about.
 *
 * The page is read-only, so the only thing a remark can do here is lead to the
 * screen it is written on — and a page read with nowhere to lead to must not look
 * like it does.
 */
describe("opening a remark from the page", () => {
  it("offers the way to the screen it is written on", () => {
    const html = renderToStaticMarkup(
      <MarkdownPage
        raw={PAGE}
        notes={[note({ id: "a", anchor: "定义", body: "这里有个疑问。" })]}
        onOpenNote={() => undefined}
      />,
    );
    expect(html).toContain('role="button"');
    expect(html).toContain("打开这条备注");
  });

  it("leaves the page read-only when there is nowhere to lead to", () => {
    const html = renderToStaticMarkup(
      <MarkdownPage raw={PAGE} notes={[note({ id: "a", anchor: "定义" })]} />,
    );
    expect(html).not.toContain('role="button"');
  });

  it("makes a remark with nowhere to go a way into the screen that manages them", () => {
    const home = note({
      id: "a",
      anchor: "已经删掉的标题",
      quote: "页面上没有留下这句话。",
      body: "无处安放的一条。",
    });
    const withWay = renderToStaticMarkup(
      <MarkdownPage raw={PAGE} notes={[home]} onOpenRemarks={() => undefined} />,
    );
    expect(withWay).toContain('aria-label="1 条备注还没放进正文"');
    expect(withWay).toContain("<button");

    const without = renderToStaticMarkup(<MarkdownPage raw={PAGE} notes={[home]} />);
    expect(without).not.toContain("<button");
  });
});
