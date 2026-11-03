// Extract one release's notes from CHANGELOG.md.
//
// The release workflow uses this as the only source of a Release body: every
// version gets exactly one `## v<version>` section, and a version without a
// section is not releasable.
//
// Usage: node scripts/release-notes.mjs <version>

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const CHANGELOG_PATH = path.join(repoRoot, "CHANGELOG.md");

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function normalizeVersion(input) {
  if (typeof input !== "string") throw new Error("a version is required");
  const version = input.trim().replace(/^v/i, "");
  if (version === "") throw new Error("a version is required");
  return version;
}

export function extractReleaseNotes(markdown, version) {
  const wanted = normalizeVersion(version);
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  const heading = new RegExp(`^##\\s+v?${escapeRegExp(wanted)}(?:\\s|$)`);
  const start = lines.findIndex((line) => heading.test(line));
  if (start === -1) return null;

  let end = lines.length;
  for (let index = start + 1; index < lines.length; index += 1) {
    // A level-1 or level-2 heading starts another section; deeper headings
    // belong to this one.
    if (/^#{1,2}\s/.test(lines[index])) {
      end = index;
      break;
    }
  }
  return lines.slice(start, end).join("\n").trimEnd();
}

export function readReleaseNotes(version, changelogPath = CHANGELOG_PATH) {
  const notes = extractReleaseNotes(readFileSync(changelogPath, "utf8"), version);
  if (notes === null) {
    throw new Error(`CHANGELOG.md has no "## v${normalizeVersion(version)}" section`);
  }
  return notes;
}

function main(argv) {
  const version = argv[2];
  if (!version) {
    console.error("Usage: node scripts/release-notes.mjs <version>");
    process.exit(1);
  }
  try {
    process.stdout.write(`${readReleaseNotes(version)}\n`);
  } catch (err) {
    console.error(err.message);
    process.exit(1);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv);
}
