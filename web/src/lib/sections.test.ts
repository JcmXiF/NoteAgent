/// <reference types="node" />
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  readSections,
  type SectionedBody,
} from "@/lib/sections";

/**
 * The cases both languages are held to, so "the two agree" is a file rather than
 * a claim. Written by hand and reviewed; `tests/test_sections.py` reads the same
 * file.
 */
const CASES = JSON.parse(
  fs.readFileSync(
    path.resolve(import.meta.dirname, "../../../tests/fixtures/section_ranges_cases.json"),
    "utf8",
  ),
) as {
  name: string;
  text: string;
  preamble: [number, number];
  sections: {
    level: number;
    heading: string;
    start: number;
    markerStart: number | null;
    headingStart: number;
    headingEnd: number;
    bodyStart: number;
    end: number;
    collectionId: string;
    blockIds: string[];
  }[];
}[];

function rebuilt(text: string, sectioned: SectionedBody): string {
  const head = text.slice(sectioned.preamble.start, sectioned.preamble.end);
  return head + sectioned.sections.map((s) => text.slice(s.start, s.end)).join("");
}

/**
 * The same sections measured on text with a character outside the basic plane.
 *
 * A shared case cannot carry the offsets here: JavaScript counts UTF-16 code
 * units and the Python mirror counts code points, so the two produce different
 * numbers for the same section. What both must agree on is the text each range
 * cuts out, which is what this file holds.
 */
const UNIT_CASES = JSON.parse(
  fs.readFileSync(
    path.resolve(
      import.meta.dirname,
      "../../../tests/fixtures/section_ranges_units_cases.json",
    ),
    "utf8",
  ),
) as { name: string; text: string; headings: string[]; levels: number[]; slices: string[] }[];

/**
 * Which marker owns a heading, and what may stand in the gap between the two.
 * Held apart from the range cases because it answers a different question about
 * the same text; `tests/test_sections.py` reads the same file.
 */
const MARKER_CASES = JSON.parse(
  fs.readFileSync(
    path.resolve(import.meta.dirname, "../../../tests/fixtures/section_marker_cases.json"),
    "utf8",
  ),
) as {
  name: string;
  text: string;
  sections: { heading: string; start: number; markerStart: number | null }[];
}[];

describe("readSections against the shared cases", () => {
  it.each(CASES.map((item) => [item.name, item] as const))("%s", (_name, item) => {
    const sectioned = readSections(item.text);
    expect([sectioned.preamble.start, sectioned.preamble.end]).toEqual(item.preamble);
    expect(
      sectioned.sections.map((s) => ({
        level: s.level,
        heading: s.heading,
        start: s.start,
        markerStart: s.markerStart,
        headingStart: s.headingStart,
        headingEnd: s.headingEnd,
        bodyStart: s.bodyStart,
        end: s.end,
        collectionId: s.collectionId,
        blockIds: s.blockIds,
      })),
    ).toEqual(item.sections);
  });

  it.each(CASES.map((item) => [item.name, item] as const))(
    "%s covers the text exactly",
    (_name, item) => {
      expect(rebuilt(item.text, readSections(item.text))).toBe(item.text);
    },
  );
});

describe("which marker owns a heading, against the shared cases", () => {
  it.each(MARKER_CASES.map((item) => [item.name, item] as const))("%s", (_name, item) => {
    expect(
      readSections(item.text).sections.map((s) => ({
        heading: s.heading,
        start: s.start,
        markerStart: s.markerStart,
      })),
    ).toEqual(item.sections);
  });
});

describe("the regions never overlap and never leave a gap", () => {
  const TEXTS = [
    "",
    "\n",
    "\n\n\n",
    "只有一行",
    "# 页名\n\n引言\n",
    "## 甲\n\n正文\n\n## 乙\n\ny\n",
    "<!-- na-clip:c-1 -->\n## 甲\n\nx\n",
    "<!-- na-clip:c-1 -->\ntext\n\n## 甲\n\nx\n",
    "# 页名\r\n\r\n## 甲\r\n\r\nx\r\n",
    "## 甲",
    "## 甲\n\n正文🙂\n\n## 乙\n\n尾🌍\n",
    "<!-- na-clip:c-1 b1 -->\n## 定义🙂\n\nx\n",
    "<!-- na-clip:c-9 -->\n<!-- na-note -->\n## Defined\n\nbody\n",
  ];

  it.each(TEXTS)("%j", (text) => {
    const sectioned = readSections(text);
    expect(sectioned.preamble.start).toBe(0);
    let cursor = sectioned.preamble.end;
    for (const section of sectioned.sections) {
      expect(section.start).toBe(cursor);
      expect(section.start).toBeLessThan(section.end);
      cursor = section.end;
    }
    expect(cursor).toBe(text.length);
  });
});

describe("the same text is cut no matter how it is counted", () => {
  it.each(UNIT_CASES.map((item) => [item.name, item] as const))("%s", (_name, item) => {
    const sectioned = readSections(item.text);
    const { start, end } = sectioned.preamble;
    const slices = [item.text.slice(start, end), ...sectioned.sections.map((s) => item.text.slice(s.start, s.end))];
    expect(slices).toEqual(item.slices);
    expect(sectioned.sections.map((s) => s.heading)).toEqual(item.headings);
    expect(sectioned.sections.map((s) => s.level)).toEqual(item.levels);
    // A wrong count would show up as a cut that does not cover the text.
    expect(slices.join("")).toBe(item.text);
  });
});

describe("a section's marker comes from the same rule as the reading side", () => {
  it("owns the next heading only", () => {
    const section = readSections("<!-- na-clip:c-1 b1,b2 -->\n## 定义\n\nx\n").sections[0];
    expect(section.collectionId).toBe("c-1");
    expect(section.blockIds).toEqual(["b1", "b2"]);
    expect(section.markerStart).toBe(0);
  });

  it("owns nothing when prose sits under it", () => {
    const section = readSections("<!-- na-clip:c-1 -->\n\n一句正文。\n\n## 定义\n\nx\n").sections[0];
    expect(section.markerStart).toBeNull();
    expect(section.collectionId).toBe("");
  });

  it("leaves the compile's marker line to the section above it", () => {
    // A `na-clip` marker is the only thing a range follows, so this heading
    // starts at its own line and the marker line above it lies inside the range
    // of the section before it.
    const text = "## 甲\n\nx\n\n<!-- na-ecg -->\n## 相关\n\n- y\n";
    const sections = readSections(text).sections;
    const [above, related] = sections;
    expect(text.slice(above.start, above.end)).toBe("## 甲\n\nx\n\n<!-- na-ecg -->\n");
    expect(related.markerStart).toBeNull();
    expect(related.start).toBe(text.indexOf("## 相关"));
    expect(related.headingStart).toBe(text.indexOf("## 相关"));
  });
});
