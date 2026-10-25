import { describe, expect, it } from "vitest";
import { placements } from "@/lib/note-anchor";
import { ANCHOR_END, ANCHOR_PREAMBLE, placeNotes } from "@/lib/note-placement";
import { readSections } from "@/lib/sections";
import { normalizeProbe } from "@/lib/text-match";

/**
 * What the remarks screen offers as places, and what it writes down when one is
 * picked.
 *
 * The picker and the reading surface have to agree about what an anchor means, so
 * these check the two together: everything offered has to be a place the page
 * reads back, and a section has to come with the words that lead back to it.
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

describe("the places a remark can be put", () => {
  it("offers the page's own opening, its sections and its end, and nothing else", () => {
    expect(placements(PAGE).map((place) => place.kind)).toEqual([
      "preamble",
      "section",
      "section",
      "end",
      "none",
    ]);
    expect(placements(PAGE).map((place) => place.anchor)).toEqual([
      ANCHOR_PREAMBLE,
      "定义",
      "计算式",
      ANCHOR_END,
      "",
    ]);
  });

  it("keeps a section's depth, so the list reads in the page's shape", () => {
    // A page's own title is not a section, so the sections it has are the ones
    // below it, at the depths the page wrote them at.
    const levels = placements("## 甲\n\n### 乙\n\n#### 丙\n\n正文。\n")
      .filter((place) => place.kind === "section")
      .map((place) => place.level);
    expect(levels).toEqual([2, 3, 4]);
  });

  it("keeps the words a section opens with, as the way back to it", () => {
    const section = placements(PAGE).find((place) => place.anchor === "定义");
    expect(section?.quote).toBe("互信息衡量两个随机变量相互依赖的程度，取值非负。");
  });

  it("keeps no quote when the opening says too little to be recognised", () => {
    const section = placements("## 甲\n\n短。\n").find((place) => place.kind === "section");
    expect(section?.quote).toBe("");
  });

  it("keeps a quote short enough to be evidence rather than a copy", () => {
    const long = "这是一段很长的正文。".repeat(60);
    const section = placements(`## 甲\n\n${long}\n`).find(
      (place) => place.kind === "section",
    );
    expect(section?.quote.length).toBeLessThanOrEqual(200);
    expect(long.startsWith(section?.quote ?? "\u0000")).toBe(true);
    expect(normalizeProbe(section?.quote ?? "").length).toBeGreaterThanOrEqual(16);
  });

  it("offers only the opening, the end and nowhere when the text cannot be read", () => {
    expect(placements("").map((place) => place.kind)).toEqual(["preamble", "end", "none"]);
  });

  it("offers anchors the page reads back to the same place", () => {
    const body = PAGE;
    const sections = readSections(body).sections;
    for (const place of placements(body)) {
      const note = {
        id: "n",
        page: "wiki/互信息.md",
        anchor: place.anchor,
        quote: place.quote,
        created: "2026-10-11T10:00:00+08:00",
        updated: "2026-10-11T10:00:00+08:00",
        body: "一条备注。",
      };
      const drawn = placeNotes([note], body, sections);
      const back =
        drawn.preamble.length +
        drawn.end.length +
        drawn.unplaced.length +
        drawn.sections.reduce((count, group) => (group.length > 0 ? count + 1 : count), 0);
      // The remark is read back exactly once, wherever it was put: never nowhere
      // twice, and never both in a section and among the unplaced.
      expect(back).toBe(1);
      if (place.kind === "none") expect(drawn.unplaced).toHaveLength(1);
      if (place.kind === "preamble") expect(drawn.preamble).toHaveLength(1);
      if (place.kind === "end") expect(drawn.end).toHaveLength(1);
      if (place.kind === "section") {
        expect(drawn.sections[sections.findIndex((s) => s.heading === place.anchor)]).toHaveLength(1);
      }
    }
  });
});
