import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { extractReleaseNotes, normalizeVersion } from "./release-notes.mjs";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function runCli(...args) {
  return spawnSync(process.execPath, ["scripts/release-notes.mjs", ...args], {
    cwd: repoRoot,
    encoding: "utf8",
  });
}

const CHANGELOG = [
  "# Changelog",
  "",
  "## v0.2.0",
  "",
  "- Second release.",
  "",
  "## v0.1.0",
  "",
  "- First release.",
  "",
].join("\n");

test("normalizeVersion accepts a version with or without a leading v", () => {
  assert.equal(normalizeVersion("0.1.0"), "0.1.0");
  assert.equal(normalizeVersion("v0.1.0"), "0.1.0");
});

test("extracts the requested version's section from several versions", () => {
  assert.equal(extractReleaseNotes(CHANGELOG, "0.2.0"), "## v0.2.0\n\n- Second release.");
  assert.equal(extractReleaseNotes(CHANGELOG, "v0.1.0"), "## v0.1.0\n\n- First release.");
});

test("returns null when the changelog has no section for the version", () => {
  assert.equal(extractReleaseNotes(CHANGELOG, "0.3.0"), null);
});

test("stops at the next version heading", () => {
  const notes = extractReleaseNotes(CHANGELOG, "0.2.0");
  assert.ok(!notes.includes("v0.1.0"));
  assert.ok(!notes.includes("First release"));
});

test("stops at any level-2 heading or a level-1 heading", () => {
  const markdown = [
    "## v0.1.0",
    "",
    "- Kept.",
    "",
    "## Unreleased",
    "",
    "- Not this version.",
    "",
    "# Appendix",
    "",
    "- Not this version either.",
    "",
  ].join("\n");
  assert.equal(extractReleaseNotes(markdown, "0.1.0"), "## v0.1.0\n\n- Kept.");
});

test("keeps level-3 subheadings inside the section", () => {
  const markdown = [
    "## v0.1.0",
    "",
    "### Added",
    "",
    "- A thing.",
    "",
    "## v0.0.9",
    "",
    "- Older.",
    "",
  ].join("\n");
  assert.equal(
    extractReleaseNotes(markdown, "0.1.0"),
    "## v0.1.0\n\n### Added\n\n- A thing.",
  );
});

test("matches a date-suffixed heading", () => {
  const markdown = "## v0.1.0 — 2026-11-03\n\n- First release.\n";
  assert.equal(extractReleaseNotes(markdown, "0.1.0"), "## v0.1.0 — 2026-11-03\n\n- First release.");
});

test("does not match a longer version that starts with the requested one", () => {
  const markdown = "## v0.1.0-rc1\n\n- Prerelease.\n";
  assert.equal(extractReleaseNotes(markdown, "0.1.0"), null);
});

test("trims trailing blank lines from the section", () => {
  const markdown = "## v0.1.0\n\n- First release.\n\n\n## v0.0.9\n\n- Older.\n";
  assert.equal(extractReleaseNotes(markdown, "0.1.0"), "## v0.1.0\n\n- First release.");
});

test("the CLI prints only the v0.1.0 section from the repository changelog", () => {
  const withV = runCli("v0.1.0");
  const withoutV = runCli("0.1.0");

  assert.equal(withV.status, 0, withV.stderr);
  assert.equal(withoutV.status, 0, withoutV.stderr);
  assert.equal(withV.stdout, withoutV.stdout);
  assert.match(withV.stdout, /^## v0\.1\.0\b/);
  assert.match(withV.stdout, /^- /m);
  assert.ok(!withV.stdout.includes("Every release has one section"));
  assert.ok(!/^## v(?!0\.1\.0\b)/m.test(withV.stdout));
});

test("the CLI fails with a clear message when the version has no section", () => {
  const result = runCli("9.9.9");

  assert.equal(result.status, 1);
  assert.match(result.stderr, /9\.9\.9/);
  assert.match(result.stderr, /CHANGELOG\.md/);
});

