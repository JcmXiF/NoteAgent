import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  assertUnderWorktrees,
  branchFor,
  linkKind,
  linkPlan,
  validateSlug,
  worktreeDir,
} from "./agent-worktree.mjs";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const root = path.resolve("example-checkout");
const worktree = path.resolve("example-checkout", ".claude", "worktrees", "rag");

test("a slug is a lowercase name a branch and folder can carry", () => {
  for (const slug of ["rag", "pipeline-automation", "ticket-2", "a"]) {
    assert.equal(validateSlug(slug), slug);
  }
  for (const slug of ["", "Rag", "-rag", "rag_2", "rag/2", "../rag", "rag "]) {
    assert.throws(() => validateSlug(slug), /invalid ticket slug/, slug);
  }
  assert.throws(() => validateSlug(undefined), /invalid ticket slug/);
});

test("the branch and path are derived from the slug", () => {
  assert.equal(branchFor("rag"), "ticket/rag");
  assert.equal(worktreeDir(root, "rag"), worktree);
});

test("a path outside .claude/worktrees is refused", () => {
  assert.equal(assertUnderWorktrees(root, worktree), worktree);
  for (const target of [
    path.join(root, ".claude", "worktrees"),
    path.join(root, ".claude", "worktrees", "..", "rag"),
    path.join(root, "rag"),
    root,
  ]) {
    assert.throws(() => assertUnderWorktrees(root, target), /not under/, target);
  }
});

test("a directory link is a junction only on Windows", () => {
  assert.equal(linkKind("win32"), "junction");
  assert.equal(linkKind("linux"), "dir");
  assert.equal(linkKind("darwin"), "dir");
});

test("the plan links directories, copies docs files and skips what exists", () => {
  const plan = linkPlan({
    root,
    worktree,
    nodeModules: ["web"],
    missingNodeModules: ["desktop"],
    scratch: true,
    docs: [
      { name: "agents", kind: "dir" },
      { name: "adr", kind: "dir" },
      { name: "GLOSSARY.md", kind: "file" },
      { name: "taken.md", kind: "file" },
    ],
    exists: (target) => target === path.join(worktree, "docs", "taken.md"),
    platform: "win32",
  });

  assert.deepEqual(
    plan.links.map((item) => item.label),
    ["web/node_modules", ".scratch", "docs/adr"],
  );
  assert.deepEqual(
    plan.copies.map((item) => item.label),
    ["docs/GLOSSARY.md"],
  );
  assert.ok(plan.links.every((item) => item.kind === "junction"));
  assert.equal(
    plan.links[0].source,
    path.join(root, "web", "node_modules"),
  );
  assert.equal(
    plan.copies[0].source,
    path.join(root, "docs", "GLOSSARY.md"),
  );
  assert.ok(plan.warnings.some((line) => line.includes("desktop/node_modules")));
  assert.ok(plan.warnings.some((line) => line.includes("docs/taken.md")));
  assert.ok(!plan.links.some((item) => item.label === "docs/agents"));
});

test("the plan skips missing links with a clear warning", () => {
  const plan = linkPlan({
    root,
    worktree,
    nodeModules: [],
    missingNodeModules: ["web", "desktop"],
    scratch: false,
    docs: [],
    platform: "linux",
  });

  assert.deepEqual(plan.links, []);
  assert.deepEqual(plan.copies, []);
  assert.equal(plan.warnings.length, 3);
  assert.ok(plan.warnings.some((line) => line.includes("web/node_modules")));
  assert.ok(plan.warnings.some((line) => line.includes(".scratch")));
});

test("the CLI prints its usage without touching a worktree", () => {
  const result = spawnSync(
    process.execPath,
    ["scripts/agent-worktree.mjs", "--help"],
    { cwd: repoRoot, encoding: "utf8" },
  );

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /new <slug> \[--base <ref>\]/);
  assert.match(result.stdout, /rm <slug>/);
});
