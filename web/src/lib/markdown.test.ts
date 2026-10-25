import { describe, expect, it } from "vitest";
import { prepareMarkdown, transformWikiLinks, WIKI_SCHEME } from "@/lib/markdown";

describe("transformWikiLinks", () => {
  it("carries a bare target and uses it as the text", () => {
    expect(transformWikiLinks("见 [[信息熵]]。")).toBe(
      `见 [信息熵](<${WIKI_SCHEME}信息熵>)。`,
    );
  });

  it("uses the label when one is given", () => {
    expect(transformWikiLinks("见 [[concepts/信息熵|熵]]。")).toBe(
      `见 [熵](<${WIKI_SCHEME}concepts/信息熵>)。`,
    );
  });

  it("keeps a target with spaces inside angle brackets", () => {
    // Without the brackets the parser would end the address at the space.
    expect(transformWikiLinks("[[Some Page]]")).toBe(
      `[Some Page](<${WIKI_SCHEME}Some Page>)`,
    );
  });

  it("turns several links in one line", () => {
    expect(transformWikiLinks("[[a]] 与 [[b]]")).toBe(
      `[a](<${WIKI_SCHEME}a>) 与 [b](<${WIKI_SCHEME}b>)`,
    );
  });

  it("escapes a bracket in the link text", () => {
    expect(transformWikiLinks("[[a|[b]]")).toBe(`[\\[b](<${WIKI_SCHEME}a>)`);
  });

  it("leaves links written in a fenced block alone", () => {
    const body = "如何写链接：\n\n```md\n[[信息熵]]\n```\n";
    expect(transformWikiLinks(body)).toBe(body);
  });

  it("leaves links written in inline code alone", () => {
    const body = "写 `[[信息熵]]` 就能链接。";
    expect(transformWikiLinks(body)).toBe(body);
  });

  it("leaves an embed alone", () => {
    // Nothing resolves embeds, so it stays as written rather than becoming a
    // link to an image that cannot be found.
    expect(transformWikiLinks("![[diagram.png]]")).toBe("![[diagram.png]]");
  });

  it("still links around an embed on the same line", () => {
    expect(transformWikiLinks("![[a.png]] 见 [[b]]")).toBe(
      `![[a.png]] 见 [b](<${WIKI_SCHEME}b>)`,
    );
  });
});

describe("prepareMarkdown", () => {
  it("turns a page's links into addresses for the view to resolve", () => {
    const body = "见 [[信息熵]]\n\n![[图片.png]]";
    expect(prepareMarkdown(body)).toBe(
      `见 [信息熵](<${WIKI_SCHEME}信息熵>)\n\n![[图片.png]]`,
    );
  });

  it("returns an empty body unchanged", () => {
    expect(prepareMarkdown("")).toBe("");
  });
});
