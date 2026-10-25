/// <reference types="node" />
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { readSections } from "@/lib/sections";
import { bindWikiSections } from "@/lib/wiki-evidence";
import { sliceWikiFrontmatter } from "@/lib/wiki-locators";

/**
 * The section ranges against every wiki page on this machine.
 *
 * The unit tests pin the shapes; this one answers whether the ranges really tile
 * pages written by a model — a heading with no marker, a marker with no heading,
 * a marker stacked above another, a fence that merely looks like a heading, a
 * page whose line endings are CRLF — and whether the range reader and the
 * documents view still read the headings the same way.
 *
 * `projects/` is not in git, so the whole block is skipped where it is absent.
 * Pure reading: nothing is written.
 */

const corpus = path.resolve(import.meta.dirname, "../../../projects");

function wikiPages(dir: string): string[] {
  const out: string[] = [];
  const walk = (current: string) => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
        walk(full);
      } else if (entry.isFile() && entry.name.endsWith(".md")) {
        out.push(full);
      }
    }
  };
  walk(dir);
  return out.filter((file) => /[\\/]wiki[\\/]/.test(file));
}

const pages = fs.existsSync(corpus) ? wikiPages(corpus) : [];

describe.skipIf(pages.length === 0)("section ranges across the corpus", () => {
  it("tilings every page and reads its headings the way the documents view does", () => {
    let crlfPages = 0;
    let sections = 0;
    let owned = 0;
    let withSections = 0;
    const failures: string[] = [];

    for (const file of pages) {
      const raw = fs.readFileSync(file, "utf8").replace(/^\uFEFF/, "");
      const rel = path.relative(corpus, file).replace(/\\/g, "/");
      if (raw.includes("\r\n")) crlfPages += 1;
      const body = sliceWikiFrontmatter(raw).body.text;

      for (const [style, text] of [
        ["as read", body],
        ["folded", body.replace(/\r\n/g, "\n")],
      ] as const) {
        const sectioned = readSections(text);
        // Cover: the preamble plus every section is the whole text again.
        const head = text.slice(sectioned.preamble.start, sectioned.preamble.end);
        const rebuilt = head + sectioned.sections.map((s) => text.slice(s.start, s.end)).join("");
        if (rebuilt !== text) {
          failures.push(`${rel} (${style}): the ranges did not tile the page`);
          continue;
        }
        for (const section of sectioned.sections) {
          if (section.bodyStart > section.end || section.headingEnd > section.bodyStart) {
            failures.push(`${rel} (${style}): bad offsets at "${section.heading}"`);
          }
        }
        if (style === "as read") {
          sections += sectioned.sections.length;
          owned += sectioned.sections.filter((s) => s.markerStart !== null).length;
          if (sectioned.sections.length > 0) withSections += 1;
          // Same rule as the documents view: same headings, same order, same levels.
          const bound = bindWikiSections(text, []);
          const left = bound.map((s) => `${s.level}:${s.heading}`);
          const right = sectioned.sections.map((s) => `${s.level}:${s.heading}`);
          if (JSON.stringify(left) !== JSON.stringify(right)) {
            failures.push(`${rel}: headings disagree with bindWikiSections`);
          }
        }
      }
    }

    console.log(
      `  pages ${pages.length}  CRLF ${crlfPages}  pages with sections ${withSections}` +
        `  sections ${sections}  owned by a marker ${owned}`,
    );
    for (const sample of failures.slice(0, 8)) console.log(`      ${sample}`);

    expect(failures).toEqual([]);
    expect(sections).toBeGreaterThan(0);
  });
});
