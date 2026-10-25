import { describe, expect, it } from "vitest";
import type { Note } from "@/lib/api/notes";
import {
  ANCHOR_END,
  ANCHOR_PREAMBLE,
  placeNotes,
  type NotePlacement,
} from "@/lib/note-placement";
import { readSections } from "@/lib/sections";

/**
 * A page with a preamble, two sections, and a second section sharing the first
 * one's heading.
 */
const BODY = [
  "# 互信息",
  "",
  "引言一段。",
  "",
  "## 定义",
  "",
  "互信息衡量两个随机变量相互依赖的程度，取值非负。",
  "",
  "## 计算式",
  "",
  "离散情形按求和计算，连续情形按积分计算。",
  "",
  "## 定义",
  "",
  "第二次出现的同名标题。",
  "",
].join("\n");

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

function place(notes: Note[], body = BODY): NotePlacement {
  return placeNotes(notes, body, readSections(body).sections);
}

function ids(placed: Note[]): string[] {
  return placed.map((item) => item.id);
}

describe("placing a remark on the page it was written about", () => {
  it("puts it after the section whose heading it names", () => {
    const placement = place([note({ id: "a", anchor: "计算式" })]);
    expect(placement.sections).toHaveLength(3);
    expect(ids(placement.sections[1])).toEqual(["a"]);
    expect(ids(placement.sections[0])).toEqual([]);
    expect(placement.unplaced).toEqual([]);
  });

  it("takes the first section when a heading appears twice", () => {
    const placement = place([note({ id: "a", anchor: "定义" })]);
    expect(ids(placement.sections[0])).toEqual(["a"]);
    expect(ids(placement.sections[2])).toEqual([]);
  });

  it("reads the page's own start and end by name, not by wording", () => {
    const placement = place([
      note({ id: "a", anchor: ANCHOR_PREAMBLE }),
      note({ id: "b", anchor: ANCHOR_END }),
    ]);
    expect(ids(placement.preamble)).toEqual(["a"]);
    expect(ids(placement.end)).toEqual(["b"]);
    expect(placement.unplaced).toEqual([]);
  });

  it("leaves a remark with no place named where it can be seen", () => {
    const placement = place([note({ id: "a", anchor: "   " })]);
    expect(ids(placement.unplaced)).toEqual(["a"]);
    expect(placement.preamble).toEqual([]);
    expect(placement.end).toEqual([]);
    expect(placement.sections.flat()).toEqual([]);
  });

  it("reads several remarks sharing a place oldest first", () => {
    const placement = place([
      note({ id: "newer", anchor: "定义", created: "2026-10-11T10:00:00.000002+08:00" }),
      note({ id: "older", anchor: "定义", created: "2026-10-11T10:00:00.000001+08:00" }),
    ]);
    expect(ids(placement.sections[0])).toEqual(["older", "newer"]);
  });

  it("keeps the order it was given for remarks written in the same moment", () => {
    const placement = place([
      note({ id: "first", anchor: "定义" }),
      note({ id: "second", anchor: "定义" }),
    ]);
    expect(ids(placement.sections[0])).toEqual(["first", "second"]);
  });

  it("finds the section the remark quotes when its heading has been renamed", () => {
    const placement = place([
      note({
        id: "a",
        anchor: "从前的标题",
        quote: "离散情形按求和计算，连续情形按积分计算。",
      }),
    ]);
    expect(ids(placement.sections[1])).toEqual(["a"]);
    expect(placement.unplaced).toEqual([]);
  });

  it("shows a remark whose section is gone rather than guessing at one", () => {
    const placement = place([
      note({ id: "a", anchor: "从前的标题", quote: "这一段页面里没有留下。" }),
    ]);
    expect(ids(placement.unplaced)).toEqual(["a"]);
  });

  it("treats a quote too short to be distinctive as no match at all", () => {
    const placement = place([note({ id: "a", anchor: "从前的标题", quote: "互信息" })]);
    expect(ids(placement.unplaced)).toEqual(["a"]);
  });

  it("looks for a quote in the prose, not in the headings", () => {
    const body = [
      "## 用信息熵与联合分布来解释互信息与条件熵的关系",
      "",
      "这一节说别的。",
      "",
    ].join("\n");
    const placement = place(
      [
        note({
          id: "a",
          anchor: "从前的标题",
          quote: "用信息熵与联合分布来解释互信息与条件熵的关系",
        }),
      ],
      body,
    );
    expect(ids(placement.unplaced)).toEqual(["a"]);
  });

  it("has one group per section of the page, even with no remarks at all", () => {
    const placement = place([]);
    expect(placement.sections).toHaveLength(readSections(BODY).sections.length);
    expect(placement.preamble).toEqual([]);
    expect(placement.end).toEqual([]);
    expect(placement.unplaced).toEqual([]);
  });
});
