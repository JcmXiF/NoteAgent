import { afterAll, beforeAll, describe, expect, it } from "vitest";
import i18n from "@/i18n";
import type { DoctorFinding } from "@/lib/api/types";
import { findingLines, findingSentence, severityWord } from "@/lib/doctor-findings";

/** The catalogs' own wording function, as the module is handed it. */
const t = (key: string, options?: Record<string, unknown>): string =>
  i18n.t(key, options as never) as unknown as string;

function finding(
  code: string,
  path: string | null,
  params: Record<string, string> = {},
  severity = "warning",
): DoctorFinding {
  return { severity, code, message: `core text for ${code}`, path, params };
}

beforeAll(async () => {
  await i18n.changeLanguage("zh");
});

afterAll(async () => {
  await i18n.changeLanguage("en");
});

describe("turning findings into lines", () => {
  it("leaves out what a healthy project looks like", () => {
    // An orphan page and a template overview are normal, so listing them beside
    // what is actually wrong would teach the reader to ignore the list.
    const lines = findingLines([
      finding("link.orphan", "wiki/concepts/a.md", {}, "info"),
      finding("layout.overviewLeftover", "wiki/overview.md", {}, "info"),
      finding("body.short", "wiki/concepts/a.md", { chars: "12" }),
    ]);
    expect(lines.map((line) => line.code)).toEqual(["body.short"]);
  });

  it("says one line for a page that repeats the same problem", () => {
    // What the reader acts on is the missing page, so a page linked to three
    // times is named once and counted once.
    const lines = findingLines([
      finding("link.broken", "wiki/concepts/a.md", { target: "熵" }),
      finding("link.broken", "wiki/concepts/a.md", { target: "点互信息" }),
      finding("link.broken", "wiki/concepts/a.md", { target: "熵" }),
    ]);
    expect(lines).toHaveLength(1);
    expect(lines[0]!.count).toBe(2);
    expect(lines[0]!.items).toEqual(["熵", "点互信息"]);
  });

  it("counts the findings where the code has nothing to name", () => {
    const lines = findingLines([
      finding("mark.clip_bad", "wiki/concepts/a.md"),
      finding("mark.clip_bad", "wiki/concepts/a.md"),
      finding("mark.clip_bad", "wiki/concepts/a.md"),
    ]);
    expect(lines).toHaveLength(1);
    expect(lines[0]!.count).toBe(3);
    expect(lines[0]!.items).toEqual([]);
  });

  it("keeps pages apart, and problems of a different kind apart", () => {
    const lines = findingLines([
      finding("link.broken", "wiki/concepts/a.md", { target: "熵" }),
      finding("link.broken", "wiki/concepts/b.md", { target: "熵" }),
      finding("index.ghost", "wiki/concepts/a.md", { slug: "熵" }),
    ]);
    expect(lines.map((line) => [line.code, line.path])).toEqual([
      ["link.broken", "wiki/concepts/a.md"],
      ["link.broken", "wiki/concepts/b.md"],
      ["index.ghost", "wiki/concepts/a.md"],
    ]);
  });

  it("does not fold together what a page can have only one of", () => {
    // Two of the same code here would be two different problems, so merging them
    // would hide one of them.
    const lines = findingLines([
      finding("body.short", "wiki/concepts/a.md", { chars: "12" }),
      finding("body.short", "wiki/concepts/a.md", { chars: "9" }),
    ]);
    expect(lines).toHaveLength(2);
    expect(lines.map((line) => line.params.chars)).toEqual(["12", "9"]);
  });
});

describe("saying a line", () => {
  it("uses the figures rather than the core's own words", () => {
    const [line] = findingLines([
      finding("link.broken", "wiki/concepts/a.md", { target: "熵" }),
    ]);
    const said = findingSentence(line!, t);
    expect(said).toContain("熵");
    expect(said).not.toContain("core text");
    expect(said).not.toContain("link.broken");
  });

  it("counts the repeats and names them", () => {
    const [line] = findingLines([
      finding("link.broken", "wiki/concepts/a.md", { target: "熵" }),
      finding("link.broken", "wiki/concepts/a.md", { target: "点互信息" }),
    ]);
    const said = findingSentence(line!, t);
    expect(said).toContain("2");
    expect(said).toContain("熵");
    expect(said).toContain("点互信息");
  });

  it("counts the rest once the names run out", () => {
    const targets = ["一", "二", "三", "四", "五", "六", "七"];
    const [line] = findingLines(
      targets.map((target) => finding("link.broken", "wiki/concepts/a.md", { target })),
    );
    const said = findingSentence(line!, t);
    expect(said).toContain("五");
    expect(said).not.toContain("六");
    expect(said).toContain("2");
  });

  it("says something for a code it has never heard of", () => {
    const [line] = findingLines([finding("a.code.from.the.future", "wiki/a.md")]);
    const said = findingSentence(line!, t);
    expect(said).not.toContain("core text");
    expect(said).not.toContain("a.code.from.the.future");
    expect(said.length).toBeGreaterThan(0);
  });

  it("is in the reader's language, not the core's", async () => {
    const [line] = findingLines([
      finding("link.broken", "wiki/concepts/a.md", { target: "entropy" }),
    ]);
    await i18n.changeLanguage("en");
    expect(findingSentence(line!, t)).toContain("links");
    await i18n.changeLanguage("zh");
    expect(findingSentence(line!, t)).toContain("链");
    expect(findingSentence(line!, t)).not.toContain("links");
  });
});

describe("saying how serious a line is", () => {
  it("uses the reader's word rather than the field name", () => {
    expect(severityWord("error", t)).not.toBe("error");
    expect(severityWord("warning", t)).not.toBe("warning");
    expect(severityWord("error", t)).not.toBe(severityWord("warning", t));
  });

  it("reads an unfamiliar severity as the milder one", () => {
    // Better to understate a problem than to shout about something nobody has
    // seen before.
    expect(severityWord("catastrophe", t)).toBe(severityWord("warning", t));
  });
});
