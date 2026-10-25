/// <reference types="node" />
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { sliceWikiFrontmatter } from "@/lib/wiki-locators";

/**
 * The slicer against every wiki page on this machine.
 *
 * The unit tests pin the shapes; this one answers whether a writer could really
 * rely on the cut point, because the failures that matter here only show up in
 * the corpus — a page whose frontmatter is not the shape the examples use.
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

/**
 * Where a page's frontmatter really is, measured without the module's help: the
 * opening fence through the closing fence and the newline after it.
 */
const OPENING_FRONTMATTER = /^---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/;

describe.skipIf(pages.length === 0)("sliceWikiFrontmatter across the corpus", () => {
  it("reassembles every page and agrees on where the block ends", () => {
    let reassemblyFailures = 0;
    let cutDisagreements = 0;
    let fencedButUnrecognised = 0;
    let crlfPages = 0;
    const samples: string[] = [];

    for (const file of pages) {
      const raw = fs.readFileSync(file, "utf8").replace(/^\uFEFF/, "");
      const rel = path.relative(corpus, file).replace(/\\/g, "/");
      const { hasFrontmatter, prefix, body } = sliceWikiFrontmatter(raw);
      if (raw.includes("\r\n")) crlfPages += 1;

      // The one promise: a writer that swaps `body` leaves every other
      // character where it was, line endings included.
      const covered =
        prefix.start === 0 &&
        prefix.end === body.start &&
        body.end === raw.length &&
        prefix.text + body.text === raw &&
        raw.slice(prefix.start, prefix.end) === prefix.text &&
        raw.slice(body.start, body.end) === body.text;
      if (!covered) {
        reassemblyFailures += 1;
        if (samples.length < 5) samples.push(`${rel}: runs do not add up to the page`);
        continue;
      }

      const opening = OPENING_FRONTMATTER.exec(raw);
      if (opening && hasFrontmatter && prefix.end !== opening[0].length) {
        cutDisagreements += 1;
        if (samples.length < 5) {
          samples.push(`${rel}: cut at ${prefix.end}, block ends at ${opening[0].length}`);
        }
      }
      if (opening && !hasFrontmatter) fencedButUnrecognised += 1;
    }

    console.log(
      `  pages ${pages.length}  CRLF ${crlfPages}  reassembly failures ${reassemblyFailures}` +
        `  cut disagreements ${cutDisagreements}` +
        `  fenced but not recognised as frontmatter ${fencedButUnrecognised}`,
    );
    for (const sample of samples) console.log(`      ${sample}`);

    expect(reassemblyFailures).toBe(0);
    expect(cutDisagreements).toBe(0);
  });
});
