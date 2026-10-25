import { describe, expect, it } from "vitest";
import { readableName, readableTitleFromHtml, readableTitles } from "@/lib/source-title";
import type { TreeFile } from "@/lib/project-tree";

function file(path: string, title = ""): TreeFile {
  return { path, name: path.split("/").pop() ?? path, title };
}

function page(head: string): string {
  return `<!DOCTYPE html><html><head>${head}</head><body><p>x</p></body></html>`;
}

describe("readableName", () => {
  it("drops the code suffix a stored file carries", () => {
    // These are the names this project's files really have on disk.
    expect(readableName("熵_(信息论)-dd3c946915be")).toBe("熵_(信息论)");
    expect(readableName("KL-DivergenceIntroduction-e6281c941d9b")).toBe(
      "KL-DivergenceIntroduction",
    );
    expect(readableName("什么是信息熵、交叉熵和相对熵-5a5592d5bf77")).toBe(
      "什么是信息熵、交叉熵和相对熵",
    );
    expect(readableName("互信息-a88681e266a6")).toBe("互信息");
  });

  it("drops the extension too, when the address had one", () => {
    // The name kept the address's leaf, so `12906163.html` is the page's address
    // rather than any part of its name.
    expect(readableName("12906163.html-9dd0e3d5e910.html")).toBe("12906163");
    expect(readableName("10741462.html-7d86a2166fec.html")).toBe("10741462");
  });

  it("leaves a name that has no suffix as it is", () => {
    expect(readableName("Elements of Information Theory.pdf")).toBe(
      "Elements of Information Theory",
    );
    expect(readableName("互信息")).toBe("互信息");
  });

  it("only drops a suffix of the shape the writer adds", () => {
    // Twelve hex characters are the writer's; anything else is part of the name.
    const name = "熵-信息论-dd3c946915b";
    expect(readableName(name)).toBe(name);
    expect(readableName("熵-信息论-gggggggggggg")).toBe("熵-信息论-gggggggggggg");
  });

  it("drops one suffix and no more", () => {
    // The name is the address leaf plus the writer's suffix, so a leaf that looks
    // like a suffix is the address and stays.
    expect(readableName("aaaaaaaabbbb-cccccccccccc.html")).toBe("aaaaaaaabbbb");
  });

  it("reads the last segment of a path", () => {
    expect(readableName("raw/web/zh.wikipedia.org/相对熵-d112ccdf1344.html")).toBe(
      "相对熵",
    );
  });
});

describe("readableTitleFromHtml", () => {
  it("reads the name the page gives itself", () => {
    const html = page("<title>机器学习笔记-信息熵、条件熵和 KL 散度</title>");
    expect(readableTitleFromHtml(html)).toBe("机器学习笔记-信息熵、条件熵和 KL 散度");
  });

  it("decodes escapes and flattens the whitespace", () => {
    expect(readableTitleFromHtml(page("<title>A &amp; B\n   &#39;C&#39;</title>"))).toBe(
      "A & B 'C'",
    );
  });

  it("says nothing when the head says nothing", () => {
    expect(readableTitleFromHtml(page("<meta charset=\"utf-8\">"))).toBeNull();
    expect(readableTitleFromHtml(page("<title>   </title>"))).toBeNull();
    expect(readableTitleFromHtml("<html><body>no head</body></html>")).toBeNull();
    expect(readableTitleFromHtml("")).toBeNull();
    expect(readableTitleFromHtml(null)).toBeNull();
  });

  it("does not look past the head", () => {
    const html = `<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><title>late</title></body></html>`;
    expect(readableTitleFromHtml(html)).toBeNull();
  });

  it("takes a document inside the head, and its attributes", () => {
    expect(readableTitleFromHtml(page('<TITLE data-x="1">标题</TITLE>'))).toBe("标题");
  });

  it("treats a page that offers its own address as having no title", () => {
    // The writer falls back to the address, and the file name says more.
    expect(readableTitleFromHtml(page("<title>https://example.com/p/1</title>"))).toBeNull();
  });
});

describe("readableTitles", () => {
  it("keeps the name the document gave itself", () => {
    // The core reads it; cleaning it up here would only damage it.
    const files = [
      file("raw/web/zh.wikipedia.org/相对熵-d112ccdf1344.html", "相对熵 - 维基百科"),
      file("wiki/concepts/KL散度.md", "KL散度"),
    ];
    expect(readableTitles(files).map((item) => item.title)).toEqual([
      "相对熵 - 维基百科",
      "KL散度",
    ]);
  });

  it("reads a name off the file name when the document has none", () => {
    // An image carries no title, and the file name is all there is.
    const files = [
      file("raw/web/zh.wikipedia.org/相对熵-d112ccdf1344.html", ""),
      file("raw/media/图-a1b2c3d4e5f6.png", ""),
      file("raw/web/12906163.html-9dd0e3d5e910.html", ""),
    ];
    expect(readableTitles(files).map((item) => item.title)).toEqual([
      "相对熵",
      "图",
      "12906163",
    ]);
  });

  it("leaves a markdown source named by its file name", () => {
    // The core names it, since a markdown source's file name is its name.
    const files = [file("raw/1. 矩阵与线性方程.md", "1. 矩阵与线性方程")];
    expect(readableTitles(files)[0]?.title).toBe("1. 矩阵与线性方程");
  });

  it("keeps everything else about a file", () => {
    const [only] = readableTitles([file("raw/web/a-000000000001.html", "")]);
    expect(only?.path).toBe("raw/web/a-000000000001.html");
    expect(only?.name).toBe("a-000000000001.html");
  });
});
