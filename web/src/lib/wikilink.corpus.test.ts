/// <reference types="node" />
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { prepareMarkdown } from "@/lib/markdown";
import { codeSpans, findWikiLinks, mapOutsideCode } from "@/lib/wikilink";

/**
 * The link rule and the code rule against every wiki page on this machine.
 *
 * The unit tests pin the shapes; this one answers whether they hold on pages
 * written by a model — an unclosed fence, a fence inside a fence, a `[[link]]`
 * quoted as an example.
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

describe.skipIf(pages.length === 0)("the link rule across the corpus", () => {
  it("covers each page without losing a character, and leaves code as written", () => {
    let uncovered = 0;
    let codeRewritten = 0;
    let links = 0;
    let linksInCode = 0;
    const samples: string[] = [];

    for (const file of pages) {
      const raw = fs.readFileSync(file, "utf8").replace(/^\uFEFF/, "");
      const rel = path.relative(corpus, file).replace(/\\/g, "/");

      // Every character is either inside a code span or outside one, exactly
      // once: a bad span would drop or duplicate text when a rewriter runs.
      if (mapOutsideCode(raw, (text) => text) !== raw) {
        uncovered += 1;
        if (samples.length < 5) samples.push(`${rel}: text runs do not add up`);
        continue;
      }

      const prepared = prepareMarkdown(raw);
      for (const [start, end] of codeSpans(raw)) {
        const span = raw.slice(start, end);
        // Code is shown as written: no rewriter may touch a character of it.
        if (!prepared.includes(span)) {
          codeRewritten += 1;
          if (samples.length < 5) samples.push(`${rel}: code at ${start} was rewritten`);
          continue;
        }
        linksInCode += findWikiLinks(span).length;
      }
      links += findWikiLinks(raw).length;
    }

    console.log(
      `  pages ${pages.length}  links ${links}  links inside code ${linksInCode}` +
        `  uncovered ${uncovered}  code rewritten ${codeRewritten}`,
    );
    for (const sample of samples) console.log(`      ${sample}`);

    expect(uncovered).toBe(0);
    expect(codeRewritten).toBe(0);
    expect(linksInCode).toBe(0);
  });
});
