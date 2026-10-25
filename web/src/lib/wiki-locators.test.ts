import { describe, expect, it } from "vitest";
import {
  isRenderableOriginal,
  normalizePath,
  parseSourceLocator,
  parseWikiSourceLocators,
  parseYamlFlowList,
  sliceWikiFrontmatter,
  splitWikiFrontmatter,
} from "@/lib/wiki-locators";

describe("normalizePath", () => {
  it("uses forward slashes and drops leading ones", () => {
    expect(normalizePath("\\raw\\a.html")).toBe("raw/a.html");
    expect(normalizePath("//raw/a.html")).toBe("raw/a.html");
    expect(normalizePath("raw/a.html")).toBe("raw/a.html");
  });
});

describe("parseSourceLocator", () => {
  it("splits a path and a passage", () => {
    expect(parseSourceLocator("raw/web/a.html#b3")).toEqual({
      path: "raw/web/a.html",
      blockId: "b3",
    });
  });

  it("treats a locator without a passage as the whole file", () => {
    expect(parseSourceLocator("raw/pdf/a.pdf")).toEqual({
      path: "raw/pdf/a.pdf",
      blockId: null,
    });
  });

  it("normalises separators and leading slashes", () => {
    expect(parseSourceLocator("\\raw\\web\\a.html#p2")?.path).toBe(
      "raw/web/a.html",
    );
    expect(parseSourceLocator("/raw/web/a.html")?.path).toBe(
      "raw/web/a.html",
    );
  });

  it("uses the last hash, so a path containing one still works", () => {
    expect(parseSourceLocator("raw/web/a#1.html#b3")).toEqual({
      path: "raw/web/a#1.html",
      blockId: "b3",
    });
  });

  it("treats an empty passage as the whole file", () => {
    expect(parseSourceLocator("raw/web/a.html#")?.blockId).toBeNull();
  });

  it("rejects empty input and a locator that is only a passage", () => {
    expect(parseSourceLocator("")).toBeNull();
    expect(parseSourceLocator("   ")).toBeNull();
    // A pure passage names no file, so there would be nothing to look up.
    expect(parseSourceLocator("#b3")).toBeNull();
  });
});

describe("isRenderableOriginal", () => {
  it("accepts the originals the documents view can show", () => {
    for (const path of [
      "raw/web/a.html",
      "raw/web/a.htm",
      "raw/pdf/a.pdf",
      "raw/office/a.docx",
      "raw/office/a.pptx",
      "raw/media/a.png",
      "raw/media/a.svg",
    ]) {
      expect(isRenderableOriginal(path)).toBe(true);
    }
  });

  it("ignores a passage suffix", () => {
    expect(isRenderableOriginal("raw/web/a.html#b3")).toBe(true);
  });

  it("rejects anything outside the raw folder", () => {
    expect(isRenderableOriginal("wiki/concepts/a.md")).toBe(false);
    expect(isRenderableOriginal("notes/a.html")).toBe(false);
  });

  it("rejects source files it cannot render", () => {
    expect(isRenderableOriginal("raw/a.md")).toBe(false);
    expect(isRenderableOriginal("raw/a.bin")).toBe(false);
  });
});

describe("parseYamlFlowList", () => {
  it("splits on commas and trims", () => {
    expect(parseYamlFlowList("a, b ,c")).toEqual(["a", "b", "c"]);
  });

  it("keeps a comma inside double quotes", () => {
    expect(parseYamlFlowList('"a, b", c')).toEqual(["a, b", "c"]);
  });

  it("keeps a comma inside single quotes", () => {
    expect(parseYamlFlowList("'a, b', c")).toEqual(["a, b", "c"]);
  });

  it("unescapes inside double quotes", () => {
    expect(parseYamlFlowList('"a\\"b"')).toEqual(['a"b']);
  });

  it("drops empty entries, including a trailing comma", () => {
    expect(parseYamlFlowList("a,, b,")).toEqual(["a", "b"]);
    expect(parseYamlFlowList("   ")).toEqual([]);
    expect(parseYamlFlowList("")).toEqual([]);
  });

  it("handles an unclosed quote without losing the text", () => {
    expect(parseYamlFlowList('"unfinished')).toEqual(["unfinished"]);
  });
});

describe("splitWikiFrontmatter", () => {
  it("splits a fenced frontmatter from the body", () => {
    const result = splitWikiFrontmatter("---\ntype: concept\ntitle: T\n---\nBody\n");
    expect(result.yaml).toContain("type: concept");
    expect(result.body.trim()).toBe("Body");
  });

  it("tolerates a missing opening fence", () => {
    const result = splitWikiFrontmatter("type: concept\ntitle: T\n---\nBody\n");
    expect(result.yaml).toContain("type: concept");
    expect(result.body.trim()).toBe("Body");
  });

  it("tolerates a leftover FILE opener", () => {
    const result = splitWikiFrontmatter(
      "---FILE: wiki/concepts/a.md\n---\ntype: concept\n---\nBody\n",
    );
    expect(result.yaml).toContain("type: concept");
    expect(result.body.trim()).toBe("Body");
  });

  it("does not mistake prose containing a rule for frontmatter", () => {
    const raw = "Some text\n---\nMore text\n";
    expect(splitWikiFrontmatter(raw)).toEqual({ yaml: "", body: raw });
  });

  it("treats a page with no frontmatter as all body", () => {
    expect(splitWikiFrontmatter("# Title\n\nProse\n").yaml).toBe("");
  });

  it("handles CRLF", () => {
    const result = splitWikiFrontmatter("---\r\ntype: concept\r\n---\r\nBody\r\n");
    expect(result.yaml).toContain("type: concept");
    expect(result.body.trim()).toBe("Body");
  });
});

describe("sliceWikiFrontmatter", () => {
  // The reader folds line endings and drops a leftover FILE opener because its
  // question is "what does this page say". The slicer's question is "which
  // characters may a writer touch", so its one promise is the reverse: the page
  // comes back whole, in the line endings it was read with.

  it("covers the page: the two runs add up to the input", () => {
    const raw = "---\ntype: concept\ntitle: T\n---\n\n## 定义\n\n正文\n";
    const { prefix, body } = sliceWikiFrontmatter(raw);
    expect(prefix.text + body.text).toBe(raw);
    expect(raw.slice(prefix.start, prefix.end)).toBe(prefix.text);
    expect(raw.slice(body.start, body.end)).toBe(body.text);
    expect(prefix.start).toBe(0);
    expect(prefix.end).toBe(body.start);
    expect(body.end).toBe(raw.length);
  });

  it("keeps the fence's own line ending with the prefix, and the blank line with the body", () => {
    const { prefix, body } = sliceWikiFrontmatter("---\ntype: concept\n---\n\n## 定义\n");
    // The cut lands on the line ending the closing fence owns, so the blank
    // line that separates the block from the body travels with the body. The
    // kernel reads it the same way, so a writer that swaps the body must carry
    // the separator itself.
    expect(prefix.text).toBe("---\ntype: concept\n---\n");
    expect(body.text).toBe("\n## 定义\n");
  });

  it("keeps a rule inside the body with the body", () => {
    const { prefix, body } = sliceWikiFrontmatter(
      "---\ntype: concept\n---\n\nAbove\n\n---\n\nBelow\n",
    );
    expect(prefix.text).toBe("---\ntype: concept\n---\n");
    expect(body.text).toBe("\nAbove\n\n---\n\nBelow\n");
  });

  it("keeps the line endings the page was read with", () => {
    const raw = "---\r\ntype: concept\r\n---\r\n\r\nBody\r\n";
    const { prefix, body } = sliceWikiFrontmatter(raw);
    expect(prefix.text).toBe("---\r\ntype: concept\r\n---\r\n");
    expect(body.text).toBe("\r\nBody\r\n");
    // The reader normalises; the slicer must not, or a write would rewrite every
    // line of a page whose writer emits CRLF.
    expect(splitWikiFrontmatter(raw).body).not.toContain("\r");
  });

  it("reports a missing opening fence where it is, without repairing it", () => {
    const raw = "type: concept\ntitle: T\n---\nBody\n";
    const { hasFrontmatter, prefix, body } = sliceWikiFrontmatter(raw);
    expect(hasFrontmatter).toBe(true);
    expect(prefix.text).toBe("type: concept\ntitle: T\n---\n");
    expect(prefix.text + body.text).toBe(raw);
  });

  it("keeps a leftover FILE opener in the prefix rather than dropping it", () => {
    const raw = "---FILE: wiki/concepts/a.md\n---\ntype: concept\n---\nBody\n";
    const { hasFrontmatter, prefix, body } = sliceWikiFrontmatter(raw);
    expect(hasFrontmatter).toBe(true);
    expect(prefix.text).toBe("---FILE: wiki/concepts/a.md\n---\ntype: concept\n---\n");
    expect(prefix.text + body.text).toBe(raw);
    // Dropping it is a repair the reader may make for display; a writer that
    // copied that would edit the page on its way past.
    expect(splitWikiFrontmatter(raw).yaml).not.toContain("FILE:");
  });

  it("treats a page with no frontmatter as all body", () => {
    const raw = "# Title\n\nProse\n";
    const { hasFrontmatter, prefix, body } = sliceWikiFrontmatter(raw);
    expect(hasFrontmatter).toBe(false);
    expect(prefix.text).toBe("");
    expect(prefix.end).toBe(0);
    expect(body.text).toBe(raw);
  });

  it("does not mistake prose containing a rule for frontmatter", () => {
    const raw = "Some text\n---\nMore text\n";
    const { hasFrontmatter, prefix, body } = sliceWikiFrontmatter(raw);
    expect(hasFrontmatter).toBe(false);
    expect(prefix.text).toBe("");
    expect(body.text).toBe(raw);
  });

  it("covers an empty page without inventing a region", () => {
    const { hasFrontmatter, prefix, body } = sliceWikiFrontmatter("");
    expect(hasFrontmatter).toBe(false);
    expect(prefix.text).toBe("");
    expect(body.text).toBe("");
    expect(body.start).toBe(0);
  });
});

describe("parseWikiSourceLocators", () => {
  const page = (sources: string) =>
    `---\ntype: concept\ntitle: T\nsources: ${sources}\n---\nBody\n`;

  it("reads the locators in order", () => {
    const found = parseWikiSourceLocators(
      page("[raw/web/a.html#b1, raw/web/a.html#b2]"),
    );
    expect(found).toEqual([
      { path: "raw/web/a.html", blockId: "b1" },
      { path: "raw/web/a.html", blockId: "b2" },
    ]);
  });

  it("removes repeats", () => {
    const found = parseWikiSourceLocators(
      page("[raw/web/a.html#b1, raw/web/a.html#b1]"),
    );
    expect(found).toHaveLength(1);
  });

  it("keeps a whole-file locator distinct from one of its passages", () => {
    const found = parseWikiSourceLocators(
      page("[raw/pdf/a.pdf, raw/pdf/a.pdf#p2]"),
    );
    expect(found).toHaveLength(2);
    expect(found.map((item) => item.blockId)).toEqual([null, "p2"]);
  });

  it("returns nothing without frontmatter or without a sources line", () => {
    expect(parseWikiSourceLocators("# Title\n")).toEqual([]);
    expect(parseWikiSourceLocators("---\ntype: concept\n---\nBody\n")).toEqual([]);
  });

  it("returns nothing when the list is empty", () => {
    expect(parseWikiSourceLocators(page("[]"))).toEqual([]);
  });

  it("reads a real page shape", () => {
    // Mirrors what the pipeline actually writes.
    const raw = [
      "---",
      "type: concept",
      "title: 信息熵",
      "created: 2026-09-17",
      "tags: [information-theory]",
      "related: []",
      "sources: [raw/web/熵_(信息论)-dd3c946915be.html#b4, raw/web/什么是信息熵-5a5592d5bf77.html#b22]",
      "---",
      "",
      "## 定义",
      "",
      "正文",
    ].join("\n");
    const found = parseWikiSourceLocators(raw);
    expect(found).toHaveLength(2);
    expect(found[0]).toEqual({
      path: "raw/web/熵_(信息论)-dd3c946915be.html",
      blockId: "b4",
    });
    expect(isRenderableOriginal(found[1].path)).toBe(true);
  });
});
