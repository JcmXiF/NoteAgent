import { describe, expect, it } from "vitest";
import {
  codeSpans,
  findWikiLinks,
  formatWikiLink,
  mapOutsideCode,
  WIKILINK_PATTERN,
} from "@/lib/wikilink";

const targets = (text: string) => findWikiLinks(text).map((link) => link.target);

describe("findWikiLinks", () => {
  it("reads a bare link and uses the target as its text", () => {
    const [link] = findWikiLinks("见 [[信息熵]]。");
    expect(link).toEqual({
      target: "信息熵",
      label: null,
      display: "信息熵",
      start: 2,
      end: 9,
    });
    expect(targets("见 [[信息熵]]。")).toEqual(["信息熵"]);
  });

  it("reads the label when one is given", () => {
    const [link] = findWikiLinks("见 [[concepts/信息熵|熵]]。");
    expect(link.target).toBe("concepts/信息熵");
    expect(link.label).toBe("熵");
  });

  it("reads a blank label as the target", () => {
    expect(findWikiLinks("[[a|]]")[0].label).toBe("");
  });

  it("trims the target and the label", () => {
    expect(findWikiLinks("[[  a  |  A  ]]")[0]).toMatchObject({ target: "a", label: "A" });
  });

  it("turns several links in one line", () => {
    expect(targets("[[a]] 与 [[b]] [[c]]")).toEqual(["a", "b", "c"]);
  });

  // An embed asks for a file's content, which this app cannot resolve. Reading
  // one as a link would point at a page named after the image.
  it("does not read an embed as a link", () => {
    expect(targets("![[diagram.png]]")).toEqual([]);
    expect(targets("![[a.png]] 见 [[b]]")).toEqual(["b"]);
  });

  it("does not let a link span a line", () => {
    expect(targets("[[a\nb]]")).toEqual([]);
    expect(targets("[[a\nb]] [[c]]")).toEqual(["c"]);
  });

  it("does not read an empty target as a link", () => {
    expect(targets("[[]]")).toEqual([]);
    expect(targets("[[   ]]")).toEqual([]);
  });

  it("reads a link inside a fenced block as code", () => {
    expect(targets("如何写：\n\n```md\n[[信息熵]]\n```\n\n[[真实]]\n")).toEqual(["真实"]);
  });

  it("reads a link inside an inline span as code", () => {
    expect(targets("写 `[[信息熵]]` 就能链接，见 [[熵]]。")).toEqual(["熵"]);
    expect(targets("``[[a]]``")).toEqual([]);
  });

  // A four-backtick fence holds a three-backtick example, so the inner fence
  // neither closes the block nor opens one of its own.
  it("lets a wider fence hold a narrower one", () => {
    expect(targets("````md\n```\n[[a]]\n```\n````\n\n[[b]]\n")).toEqual(["b"]);
  });

  it("reads a tilde fence", () => {
    expect(targets("~~~\n[[a]]\n~~~\n\n[[b]]\n")).toEqual(["b"]);
  });

  it("takes an unclosed fence to the end of the text", () => {
    expect(targets("```\n[[a]]\n[[b]]")).toEqual([]);
  });

  it("reads carriage-return line endings", () => {
    expect(targets("如何写：\r\n\r\n```md\r\n[[a]]\r\n```\r\n\r\n[[b]]\r\n")).toEqual(["b"]);
  });
});

describe("codeSpans", () => {
  it("reports offsets into the text", () => {
    const body = "前 `[[a]]` 后";
    expect(codeSpans(body)).toEqual([[2, 9]]);
    expect(body.slice(2, 9)).toBe("`[[a]]`");
  });

  it("leaves text with no code alone", () => {
    expect(codeSpans("只有正文")).toEqual([]);
  });
});

describe("mapOutsideCode", () => {
  it("rewrites everything but the code", () => {
    const body = "a `b` c";
    expect(mapOutsideCode(body, (text) => text.toUpperCase())).toBe("A `b` C");
  });

  it("keeps the text as it was when nothing matches", () => {
    const body = "```\n[[a]]\n```\n\n见 [[b]]\n";
    expect(mapOutsideCode(body, (text) => text)).toBe(body);
  });
});

describe("formatWikiLink", () => {
  it("writes the bare form", () => {
    expect(formatWikiLink("concepts/kl")).toBe("[[concepts/kl]]");
  });

  it("writes the label form", () => {
    expect(formatWikiLink("concepts/kl", "KL 散度")).toBe("[[concepts/kl|KL 散度]]");
  });

  it("drops a label that repeats the target", () => {
    expect(formatWikiLink("kl", "kl")).toBe("[[kl]]");
    expect(formatWikiLink("kl", "  ")).toBe("[[kl]]");
    expect(formatWikiLink("  kl  ")).toBe("[[kl]]");
  });

  it("refuses what it cannot write", () => {
    for (const target of ["a|b", "a]b", "a\nb", "a\rb"]) {
      expect(() => formatWikiLink(target)).toThrow();
    }
    for (const label of ["a]b", "a\nb", "a\rb"]) {
      expect(() => formatWikiLink("a", label)).toThrow();
    }
    expect(() => formatWikiLink("   ")).toThrow();
  });

  // Only the characters that really end a link are turned away. A `[` or a `|`
  // inside a label ends nothing, so a label may hold one.
  it("lets a label hold a bracket or a bar", () => {
    expect(formatWikiLink("a", "[b")).toBe("[[a|[b]]");
    expect(formatWikiLink("a", "b|c")).toBe("[[a|b|c]]");
    expect(findWikiLinks("[[a|b|c]]")[0]).toMatchObject({ target: "a", label: "b|c" });
  });

  // The whole point of having the rule in one place: what a writer puts on the
  // page is what a reader of that page finds, target and display alike.
  it("reads back as what it wrote", () => {
    for (const [target, label] of [
      ["concepts/kl", null],
      ["concepts/kl", "KL 散度"],
      ["信息熵", null],
      ["raw/web/x.html#b2", null],
      ["Some Page", "带空格的页"],
    ] as Array<[string, string | null]>) {
      const written = formatWikiLink(target, label);
      expect(findWikiLinks(`前缀 ${written} 后缀`)[0]).toMatchObject({ target });
      expect(targets(written)).toEqual([target]);
    }
  });
});

describe("WIKILINK_PATTERN", () => {
  it("matches a link and its label", () => {
    expect("[[a|b]]".match(new RegExp(WIKILINK_PATTERN))).toMatchObject({ 1: "a", 2: "b" });
  });
});
