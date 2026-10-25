import { describe, expect, it } from "vitest";
import type { Note } from "@/lib/api/notes";
import { orderedNotes, orphanNotes, remarkPages } from "@/lib/note-list";
import { ANCHOR_END, ANCHOR_PREAMBLE } from "@/lib/note-placement";
import type { TreeFile } from "@/lib/project-tree";
import { readSections } from "@/lib/sections";

/**
 * The list of a page's remarks, and the remarks whose page has gone.
 *
 * The order has to be the page's own — a remark after the section it names — or
 * the list and the page disagree about where the same remark sits. The ones with
 * no place stay in the list rather than being dropped.
 */

const PAGE = [
  "# 开头",
  "",
  "第一段。",
  "",
  "## 甲",
  "",
  "甲这一段写得长一些，好让引文足够被认出来。",
  "",
  "## 乙",
  "",
  "乙这一段写得长一些，好让引文足够被认出来。",
  "",
].join("\n");

function note(id: string, created: string, anchor: string): Note {
  return {
    id,
    page: "wiki/互信息.md",
    anchor,
    quote: "",
    created,
    updated: created,
    body: `${id} 说的话。`,
  };
}

const ids = (listed: ReturnType<typeof orderedNotes>) => listed.map((item) => item.note.id);

describe("the order of a page's remarks", () => {
  const sections = readSections(PAGE).sections;
  const notes = [
    note("later", "2026-10-11T10:00:03+08:00", "乙"),
    note("first", "2026-10-11T10:00:01+08:00", "甲"),
    note("start", "2026-10-11T10:00:04+08:00", ANCHOR_PREAMBLE),
    note("lost", "2026-10-11T10:00:05+08:00", "早已删掉的标题"),
    note("last", "2026-10-11T10:00:02+08:00", ANCHOR_END),
  ];

  it("follows the page rather than the order they were written in", () => {
    expect(ids(orderedNotes(notes, PAGE, sections))).toEqual([
      "start",
      "first",
      "later",
      "last",
      "lost",
    ]);
  });

  it("says where each of them sits", () => {
    const places = orderedNotes(notes, PAGE, sections).map((item) => item.place);
    expect(places).toEqual([
      { kind: "preamble" },
      { kind: "section", heading: "甲" },
      { kind: "section", heading: "乙" },
      { kind: "end" },
      { kind: "none" },
    ]);
  });

  it("keeps several remarks in one place in the order they were written", () => {
    const many = [
      note("b", "2026-10-11T10:00:02+08:00", "甲"),
      note("a", "2026-10-11T10:00:01+08:00", "甲"),
    ];
    expect(ids(orderedNotes(many, PAGE, sections))).toEqual(["a", "b"]);
  });

  it("puts a remark whose heading was renamed by the words it quoted", () => {
    const moved = [
      {
        ...note("m", "2026-10-11T10:00:01+08:00", "旧标题"),
        quote: "乙这一段写得长一些，好让引文足够被认出来。",
      },
    ];
    expect(orderedNotes(moved, PAGE, sections)).toEqual([
      {
        note: moved[0],
        place: { kind: "section", heading: "乙" },
      },
    ]);
  });
});

describe("the remarks whose page is gone", () => {
  const files: TreeFile[] = [
    { path: "wiki/互信息.md", name: "互信息.md", title: "互信息" },
    { path: "wiki/熵.md", name: "熵.md", title: "熵" },
  ];

  it("finds the ones written about a page the project no longer has", () => {
    const notes = [
      note("here", "2026-10-11T10:00:01+08:00", "甲"),
      { ...note("gone", "2026-10-11T10:00:02+08:00", "甲"), page: "wiki/改过名的页.md" },
    ];
    expect(orphanNotes(notes, files).map((item) => item.id)).toEqual(["gone"]);
  });

  it("calls none of them orphaned while every page is there", () => {
    expect(orphanNotes([note("here", "2026-10-11T10:00:01+08:00", "甲")], files)).toEqual([]);
  });
});

describe("the pages a remark can be written about", () => {
  it("offers the project's notes by name, and nothing else", () => {
    const files: TreeFile[] = [
      { path: "wiki/zeta.md", name: "zeta.md", title: "" },
      { path: "wiki/alpha.md", name: "alpha.md", title: "Alpha" },
      { path: "raw/page.html", name: "page.html", title: "A page" },
      { path: "wiki/notes/kept.pdf", name: "kept.pdf", title: "Kept" },
    ];
    expect(remarkPages(files).map((file) => file.path)).toEqual([
      "wiki/alpha.md",
      "wiki/zeta.md",
    ]);
  });
});
