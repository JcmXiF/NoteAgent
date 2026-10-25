import { describe, expect, it } from "vitest";
import {
  buildKnowledgeGroups,
  buildProjectTree,
  decodeLinkTarget,
  flattenTree,
  linkIndex,
  resolveWikiLink,
  sectionOf,
  wikiPathSlug,
  type TreeFile,
  type TreeNode,
} from "@/lib/project-tree";

function file(path: string, title = ""): TreeFile {
  const name = path.split("/").pop() ?? path;
  return { path, name, title };
}

/** The shape the assertions care about: kind and title, indented by depth. */
function outline(nodes: TreeNode[]): string[] {
  return nodes.flatMap((node) => [
    node.file ? node.file.path : `${node.kind}:${node.title}`,
    ...outline(node.children).map((line) => `  ${line}`),
  ]);
}

describe("sectionOf", () => {
  it("puts wiki pages in their own section", () => {
    expect(sectionOf("wiki/concepts/a.md")).toBe("wiki");
    expect(sectionOf("wiki/index.md")).toBe("wiki");
  });

  it("puts the stored files in the other one, whatever they are", () => {
    // The section mirrors the project: everything saved lives under one folder,
    // and what each file *is* is the tidy list's business, not this one's.
    expect(sectionOf("raw/web/a.html")).toBe("sources");
    expect(sectionOf("raw/pdf/a.pdf")).toBe("sources");
    expect(sectionOf("raw/web/misfiled.pdf")).toBe("sources");
    expect(sectionOf("raw/unknown/a.bin")).toBe("sources");
    expect(sectionOf("raw/sources/web/legacy.html")).toBe("sources");
  });

  it("leaves out anything that is not a document", () => {
    expect(sectionOf("purpose.md")).toBeNull();
    expect(sectionOf(".noteagent/clips.json")).toBeNull();
    expect(sectionOf("config/brief.md")).toBeNull();
  });
});

describe("buildProjectTree", () => {
  it("builds the two sections, the notes first", () => {
    const tree = buildProjectTree([
      file("raw/media/a.png"),
      file("wiki/concepts/c.md"),
    ]);
    expect(tree.map((node) => node.group)).toEqual(["wiki", "sources"]);
  });

  it("nests folders the way the project has them", () => {
    const tree = buildProjectTree([
      file("wiki/concepts/a.md"),
      file("raw/web/example.com/b.html"),
      file("raw/pdf/c.pdf"),
    ]);
    expect(outline(tree)).toEqual([
      "group:wiki",
      "  folder:concepts",
      "    wiki/concepts/a.md",
      "group:sources",
      "  folder:pdf",
      "    raw/pdf/c.pdf",
      "  folder:web",
      "    folder:example.com",
      "      raw/web/example.com/b.html",
    ]);
  });

  it("sorts folders and files predictably", () => {
    const tree = buildProjectTree([
      file("wiki/concepts/b.md", "bbb"),
      file("wiki/concepts/a.md", "aaa"),
      file("wiki/entities/z.md", "zzz"),
    ]);
    const wiki = tree[0];
    expect(wiki?.children.map((node) => node.title)).toEqual(["concepts", "entities"]);
    expect(wiki?.children[0]?.children.map((node) => node.title)).toEqual(["aaa", "bbb"]);
  });

  it("falls back to the file name when there is no title", () => {
    const tree = buildProjectTree([file("wiki/concepts/a.md")]);
    expect(tree[0]?.children[0]?.children[0]?.title).toBe("a.md");
  });

  it("leaves out empty sections", () => {
    const tree = buildProjectTree([file("wiki/concepts/a.md")]);
    expect(tree.map((node) => node.group)).toEqual(["wiki"]);
  });

  it("shows a folder of its own for files it cannot classify", () => {
    // The tree mirrors the project rather than sorting by kind, so a folder it
    // has never seen is simply a folder.
    const tree = buildProjectTree([file("raw/unknown/a.bin")]);
    expect(outline(tree)).toEqual([
      "group:sources",
      "  folder:unknown",
      "    raw/unknown/a.bin",
    ]);
  });

  it("ignores files that are not documents", () => {
    expect(buildProjectTree([file("purpose.md"), file("schema.md")])).toEqual([]);
  });

  it("handles an empty project", () => {
    expect(buildProjectTree([])).toEqual([]);
  });
});

describe("flattenTree", () => {
  it("returns the files in the order the tree shows them", () => {
    const tree = buildProjectTree([
      file("wiki/concepts/b.md", "bbb"),
      file("wiki/concepts/a.md", "aaa"),
      file("raw/pdf/c.pdf", "ccc"),
    ]);
    expect(flattenTree(tree).map((item) => item.path)).toEqual([
      "wiki/concepts/a.md",
      "wiki/concepts/b.md",
      "raw/pdf/c.pdf",
    ]);
  });
});

describe("buildKnowledgeGroups", () => {
  it("lists notes and source summaries under their own headings", () => {
    const groups = buildKnowledgeGroups([
      file("wiki/entropy.md", "熵"),
      file("wiki/mutual-information.md", "互信息"),
      file("wiki/sources/intro-clips.md", "导论"),
    ]);
    expect(groups.map((group) => [group.labelKey, group.files.length])).toEqual([
      ["docs.knowledge.notes", 2],
      ["docs.knowledge.summaries", 1],
    ]);
  });

  it("lists a note filed in a retired type folder with the other notes", () => {
    // The folder is part of the page's identity, not a kind of its own.
    const groups = buildKnowledgeGroups([
      file("wiki/concepts/a.md", "alpha"),
      file("wiki/entities/b.md", "beta"),
      file("wiki/loose.md", "gamma"),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0]?.labelKey).toBe("docs.knowledge.notes");
    expect(groups[0]?.files.map((item) => item.title)).toEqual(["alpha", "beta", "gamma"]);
  });

  it("leaves out the pages the program maintains", () => {
    const groups = buildKnowledgeGroups([
      file("wiki/index.md", "Index"),
      file("wiki/log.md", "Log"),
      file("wiki/overview.md", "Overview"),
      file("wiki/entropy.md", "熵"),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0]?.files.map((item) => item.title)).toEqual(["熵"]);
  });

  it("lists the stored originals by kind, after the notes", () => {
    const groups = buildKnowledgeGroups([
      file("raw/media/a.png", "图"),
      file("raw/web/b.html", "网"),
      file("wiki/entropy.md", "熵"),
    ]);
    expect(groups.map((group) => group.id)).toEqual([
      "wiki:notes",
      "raw:web",
      "raw:media",
    ]);
    expect(groups.map((group) => group.labelKey)).toEqual([
      "docs.knowledge.notes",
      "docs.kind.web",
      "docs.kind.media",
    ]);
  });

  it("files a misplaced original by what it is, not by where it sits", () => {
    // This project really does keep a PDF under web/, and listing it with the
    // PDFs is what makes the tidy list readable.
    const groups = buildKnowledgeGroups([file("raw/web/misfiled.pdf", "误放的")]);
    expect(groups.map((group) => group.id)).toEqual(["raw:pdf"]);
  });

  it("keeps an original whose kind it does not know", () => {
    const groups = buildKnowledgeGroups([file("raw/unknown/a.bin", "怪文件")]);
    expect(groups.map((group) => group.id)).toEqual(["raw:other"]);
    expect(groups[0]?.files.map((item) => item.title)).toEqual(["怪文件"]);
  });

  it("reads an older project's raw/sources layout as the same sources", () => {
    const groups = buildKnowledgeGroups([
      file("raw/sources/web/example.com/a.html", "旧页"),
      file("raw/sources/pdf/paper.pdf", "旧文"),
    ]);
    expect(groups.map((group) => group.id)).toEqual(["raw:web", "raw:pdf"]);
  });

  it("sorts each group by title", () => {
    const groups = buildKnowledgeGroups([
      file("wiki/b.md", "bbb"),
      file("wiki/a.md", "aaa"),
      file("wiki/sources/z.md", "zzz"),
      file("wiki/sources/y.md", "yyy"),
    ]);
    expect(groups[0]?.files.map((item) => item.title)).toEqual(["aaa", "bbb"]);
    expect(groups[1]?.files.map((item) => item.title)).toEqual(["yyy", "zzz"]);
  });

  it("sorts the originals by their name when they have no title", () => {
    const groups = buildKnowledgeGroups([
      file("raw/pdf/b.pdf"),
      file("raw/pdf/a.pdf"),
    ]);
    expect(groups[0]?.files.map((item) => item.path)).toEqual([
      "raw/pdf/a.pdf",
      "raw/pdf/b.pdf",
    ]);
  });

  it("leaves out files the lists do not show", () => {
    const groups = buildKnowledgeGroups([
      file(".noteagent/clips.json", "内部记录"),
      file("purpose.md", "说明"),
      file("wiki/index.md", "Index"),
    ]);
    expect(groups).toEqual([]);
  });

  it("is empty when there is nothing but the program's own pages", () => {
    expect(buildKnowledgeGroups([file("wiki/index.md", "Index")])).toEqual([]);
  });
});

describe("wikiPathSlug", () => {
  it("reads a note's identity the way the graph does", () => {
    expect(wikiPathSlug("wiki/concepts/entropy.md")).toBe("concepts/entropy");
    expect(wikiPathSlug("wiki/overview.md")).toBe("overview");
  });

  it("keeps the folders, because they are part of the identity", () => {
    // Flattening them would make two pages in different folders collide.
    expect(wikiPathSlug("wiki/entities/alpha.md")).toBe("entities/alpha");
    expect(wikiPathSlug("wiki/entities/alpha.md")).not.toBe(
      wikiPathSlug("wiki/concepts/alpha.md"),
    );
  });

  it("has nothing to say about a file outside the wiki", () => {
    expect(wikiPathSlug("raw/web/a.html")).toBe("");
    expect(wikiPathSlug("")).toBe("");
  });
});

describe("linkIndex and resolveWikiLink", () => {
  const files = [
    file("wiki/concepts/KL散度.md", "KL散度"),
    file("wiki/concepts/信息熵.md", "信息熵"),
    file("raw/web/a.html"),
  ];
  const index = linkIndex(files);

  it("indexes wiki pages only", () => {
    expect(index.has("concepts/kl散度")).toBe(true);
    expect(index.has("kl散度")).toBe(true);
    expect(index.has("a.html")).toBe(false);
  });

  it("resolves a link by title, by file name and by path", () => {
    expect(resolveWikiLink("KL散度", index)?.path).toBe("wiki/concepts/KL散度.md");
    expect(resolveWikiLink("concepts/KL散度", index)?.path).toBe("wiki/concepts/KL散度.md");
    expect(resolveWikiLink("KL散度.md", index)?.path).toBe("wiki/concepts/KL散度.md");
  });

  it("tolerates the spacing and separators a link may carry", () => {
    expect(resolveWikiLink("  KL散度  ", index)?.path).toBe("wiki/concepts/KL散度.md");
    expect(resolveWikiLink("concepts\\KL散度", index)?.path).toBe("wiki/concepts/KL散度.md");
    expect(resolveWikiLink("./KL散度", index)?.path).toBe("wiki/concepts/KL散度.md");
  });

  it("returns null for an unknown link rather than guessing", () => {
    expect(resolveWikiLink("不存在的页", index)).toBeNull();
    expect(resolveWikiLink("", index)).toBeNull();
  });

  it("resolves a target the markdown parser percent-encoded", () => {
    // CommonMark encodes non-ASCII in link destinations, which is how a
    // Chinese page title reaches this function.
    expect(resolveWikiLink("concepts/%E4%BF%A1%E6%81%AF%E7%86%B5", index)?.path).toBe(
      "wiki/concepts/信息熵.md",
    );
    expect(resolveWikiLink("%E4%BF%A1%E6%81%AF%E7%86%B5", index)?.path).toBe(
      "wiki/concepts/信息熵.md",
    );
  });

  it("keeps a target that is not valid encoding as it stands", () => {
    // A title may contain a stray percentage sign; it must not be discarded.
    expect(decodeLinkTarget("100% probability")).toBe("100% probability");
    expect(decodeLinkTarget("50%")).toBe("50%");
    expect(decodeLinkTarget("plain")).toBe("plain");
  });
});
