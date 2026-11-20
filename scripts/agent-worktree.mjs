// Create and remove the worktree an agent ticket works in.
//
// A plain `git worktree add` gives an empty checkout: no `node_modules`, no
// `.scratch` issue tracker and none of the local docs. An agent then hand-links
// each one and, at cleanup, has to unlink them before `git worktree remove` or
// the removal follows a junction into the main `node_modules`. This command
// makes the links and removes them in the safe order.
//
// Usage:
//   node scripts/agent-worktree.mjs new <slug> [--base <ref>]
//   node scripts/agent-worktree.mjs rm <slug>
//
// `new` creates `.claude/worktrees/<slug>` on branch `ticket/<slug>` from
// `--base` (default: the current HEAD of the main checkout). It links
// `web/node_modules` and `desktop/node_modules` (skipped with a warning when
// that checkout has not run `npm install`), links `.scratch`, and makes the
// local docs visible: local doc folders are linked, local `docs/*.md` files are
// copied. Only entries in the worktree that do not already exist are touched,
// so the tracked `docs/agents` directory is never replaced or overwritten.
//
// `rm` unlinks those junctions and symlinks first (removing the link, never
// its target), then runs `git worktree remove --force`. The branch is kept.

import { spawnSync } from "node:child_process";
import {
  copyFileSync,
  lstatSync,
  mkdirSync,
  readdirSync,
  rmdirSync,
  statSync,
  symlinkSync,
  unlinkSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

//: A slug names one ticket, so it has to survive a branch name and a folder.
export const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]*$/;

//: The workspaces whose dependencies a ticket may run.
const NODE_MODULES = ["web", "desktop"];

//: The tracked docs entry. The worktree has its own copy from git.
const TRACKED_DOCS = "agents";

export function validateSlug(slug) {
  if (typeof slug !== "string" || !SLUG_PATTERN.test(slug)) {
    throw new Error(
      `invalid ticket slug "${slug ?? ""}": use lowercase letters, digits and ` +
        "dashes, starting with a letter or digit",
    );
  }
  return slug;
}

export function branchFor(slug) {
  return `ticket/${validateSlug(slug)}`;
}

export function worktreesRoot(root) {
  return path.join(path.resolve(root), ".claude", "worktrees");
}

export function worktreeDir(root, slug) {
  return path.join(worktreesRoot(root), validateSlug(slug));
}

/** Refuse a path that is not a direct child of `.claude/worktrees`. */
export function assertUnderWorktrees(root, target) {
  const base = worktreesRoot(root);
  const resolved = path.resolve(target);
  const relative = path.relative(base, resolved);
  if (
    relative === "" ||
    relative === ".." ||
    relative.startsWith(`..${path.sep}`) ||
    path.isAbsolute(relative)
  ) {
    throw new Error(`refusing to touch ${resolved}: it is not under ${base}`);
  }
  return resolved;
}

export function linkKind(platform = process.platform) {
  return platform === "win32" ? "junction" : "dir";
}

/**
 * Plan the links and copies for a new worktree.
 *
 * Pure: every fact about what exists is passed in, so the test can describe a
 * checkout without creating one. `exists` is given a target path and answers
 * whether the worktree already has it; a target that exists is left alone.
 */
export function linkPlan({
  root,
  worktree,
  nodeModules = [],
  missingNodeModules = [],
  scratch = false,
  docs = [],
  exists = () => false,
  platform = process.platform,
}) {
  const links = [];
  const copies = [];
  const warnings = [];
  const kind = linkKind(platform);
  const addLink = (label, source, target) => {
    if (exists(target)) {
      warnings.push(`${label} already exists in the worktree; left untouched.`);
      return;
    }
    links.push({ label, source, target, kind });
  };

  for (const workspace of nodeModules) {
    const label = `${workspace}/node_modules`;
    addLink(
      label,
      path.join(root, workspace, "node_modules"),
      path.join(worktree, workspace, "node_modules"),
    );
  }
  for (const workspace of missingNodeModules) {
    warnings.push(
      `${workspace}/node_modules is missing in the main checkout; run ` +
        `\`npm install\` in ${workspace}/ before builds or tests that need it.`,
    );
  }

  if (scratch) {
    addLink(".scratch", path.join(root, ".scratch"), path.join(worktree, ".scratch"));
  } else {
    warnings.push(
      ".scratch is missing in the main checkout; the worktree has no issue tracker.",
    );
  }

  for (const entry of docs) {
    if (entry.name === TRACKED_DOCS) continue;
    const label = `docs/${entry.name}`;
    const source = path.join(root, "docs", entry.name);
    const target = path.join(worktree, "docs", entry.name);
    if (entry.kind === "dir") {
      addLink(label, source, target);
    } else if (exists(target)) {
      warnings.push(`${label} already exists in the worktree; left untouched.`);
    } else {
      // A file symlink needs Developer Mode or elevation on Windows, and a
      // copy cannot overwrite a tracked file. The docs are small.
      copies.push({ label, source, target });
    }
  }

  return { links, copies, warnings };
}

function git(args, cwd) {
  const result = spawnSync("git", args, { cwd, encoding: "utf8" });
  if (result.error) {
    throw new Error(`git ${args.join(" ")}: ${result.error.message}`);
  }
  return result;
}

/** The checkout that owns `.git`, even when the script runs from a worktree. */
export function mainCheckoutRoot(scriptRoot = repoRoot) {
  const result = git(["rev-parse", "--git-common-dir"], scriptRoot);
  if (result.status !== 0) {
    throw new Error(`not a git checkout: ${scriptRoot}`);
  }
  const common = path.resolve(scriptRoot, result.stdout.trim());
  return path.basename(common) === ".git" ? path.dirname(common) : scriptRoot;
}

function pathExists(target) {
  try {
    lstatSync(target);
    return true;
  } catch (err) {
    if (err.code === "ENOENT") return false;
    throw err;
  }
}

function branchExists(root, branch) {
  const result = git(["show-ref", "--verify", "--quiet", `refs/heads/${branch}`], root);
  if (result.status === 0) return true;
  if (result.status === 1) return false;
  throw new Error(`git show-ref failed: ${(result.stderr || "").trim()}`);
}

function resolveBase(root, base) {
  const ref = base ?? "HEAD";
  const result = git(
    ["rev-parse", "--verify", "--quiet", "--end-of-options", `${ref}^{commit}`],
    root,
  );
  if (result.status !== 0) {
    throw new Error(`unknown base ref "${ref}"`);
  }
  return { ref, commit: result.stdout.trim() };
}

/** The direct children of `docs/` that git does not track. */
function localDocs(root) {
  const docs = path.join(root, "docs");
  if (!pathExists(docs)) return [];
  const listed = git(["ls-files", "-z", "--", "docs"], root);
  if (listed.status !== 0) {
    throw new Error(`git ls-files failed: ${(listed.stderr || "").trim()}`);
  }
  const tracked = new Set(
    listed.stdout
      .split("\0")
      .filter(Boolean)
      .map((file) => file.split("/")[1]),
  );
  return readdirSync(docs, { withFileTypes: true })
    .filter((entry) => entry.name !== TRACKED_DOCS && !tracked.has(entry.name))
    .sort((left, right) => left.name.localeCompare(right.name))
    .map((entry) => {
      let kind = entry.isDirectory() ? "dir" : "file";
      if (entry.isSymbolicLink()) {
        try {
          kind = statSync(path.join(docs, entry.name)).isDirectory() ? "dir" : "file";
        } catch {
          // A dangling link is copied and fails with a clear warning.
        }
      }
      return { name: entry.name, kind };
    });
}

function applyLinkPlan(plan) {
  const warnings = [];
  for (const item of plan.links) {
    try {
      mkdirSync(path.dirname(item.target), { recursive: true });
      symlinkSync(item.source, item.target, item.kind);
    } catch (err) {
      warnings.push(
        `could not link ${item.label} (${err.code ?? err.message}); set that ` +
          "folder up in the worktree by hand if it is needed.",
      );
    }
  }
  for (const item of plan.copies) {
    try {
      mkdirSync(path.dirname(item.target), { recursive: true });
      copyFileSync(item.source, item.target);
    } catch (err) {
      warnings.push(`could not copy ${item.label} (${err.code ?? err.message})`);
    }
  }
  return warnings;
}

/** Remove a link itself. The target is never entered or followed. */
function removeLinkIfPresent(link) {
  let info;
  try {
    info = lstatSync(link);
  } catch (err) {
    if (err.code === "ENOENT") return false;
    throw err;
  }
  if (!info.isSymbolicLink()) return false;

  let directory = false;
  try {
    directory = statSync(link).isDirectory();
  } catch {
    // A dangling link has no target to inspect; its type comes from the
    // failed unlink below.
  }
  if (directory) {
    rmdirSync(link);
  } else {
    try {
      unlinkSync(link);
    } catch (err) {
      if (err.code === "EPERM" || err.code === "EISDIR" || err.code === "EACCES") {
        rmdirSync(link);
      } else {
        throw err;
      }
    }
  }
  return true;
}

function candidateLinks(worktree) {
  const links = [
    path.join(worktree, "web", "node_modules"),
    path.join(worktree, "desktop", "node_modules"),
    path.join(worktree, ".scratch"),
  ];
  const docs = path.join(worktree, "docs");
  let docsIsDirectory = false;
  try {
    const info = lstatSync(docs);
    docsIsDirectory = info.isDirectory() && !info.isSymbolicLink();
  } catch (err) {
    if (err.code !== "ENOENT") throw err;
  }
  if (docsIsDirectory) {
    for (const name of readdirSync(docs)) {
      links.push(path.join(docs, name));
    }
  }
  return links;
}

function removeWorktreeLinks(worktree) {
  const removed = [];
  for (const link of candidateLinks(worktree)) {
    if (removeLinkIfPresent(link)) {
      removed.push(path.relative(worktree, link).split(path.sep).join("/"));
    }
  }
  return removed;
}

function parseNewArgs(args) {
  let slug;
  let base;
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--base") {
      base = args[index + 1];
      index += 1;
      if (base === undefined || base === "") throw new Error("--base needs a ref");
    } else if (arg.startsWith("--base=")) {
      base = arg.slice("--base=".length);
      if (base === "") throw new Error("--base needs a ref");
    } else if (arg.startsWith("-")) {
      throw new Error(`unknown option: ${arg}`);
    } else if (slug === undefined) {
      slug = arg;
    } else {
      throw new Error(`unexpected argument: ${arg}`);
    }
  }
  if (slug === undefined) throw new Error("new needs a ticket slug");
  return { slug, base };
}

function newWorktree({ slug, base }) {
  const root = mainCheckoutRoot();
  const branch = branchFor(slug);
  const target = worktreeDir(root, slug);

  if (pathExists(target)) {
    throw new Error(`worktree path already exists: ${target}`);
  }
  if (branchExists(root, branch)) {
    throw new Error(
      `branch ${branch} already exists; choose another slug or remove the ` +
        "branch and try again",
    );
  }
  const baseRef = resolveBase(root, base);
  mkdirSync(worktreesRoot(root), { recursive: true });

  const added = git(
    ["worktree", "add", "-b", branch, target, baseRef.commit],
    root,
  );
  if (added.status !== 0) {
    throw new Error(
      `git worktree add failed: ${(added.stderr || added.stdout).trim()}`,
    );
  }

  const nodeModules = NODE_MODULES.filter((workspace) =>
    pathExists(path.join(root, workspace, "node_modules")),
  );
  const missingNodeModules = NODE_MODULES.filter(
    (workspace) => !nodeModules.includes(workspace),
  );
  const plan = linkPlan({
    root,
    worktree: target,
    nodeModules,
    missingNodeModules,
    scratch: pathExists(path.join(root, ".scratch")),
    docs: localDocs(root),
    exists: pathExists,
  });
  const applyWarnings = applyLinkPlan(plan);

  console.log(`Created worktree ${target}`);
  console.log(`  branch: ${branch}`);
  console.log(`  base:   ${baseRef.ref} (${baseRef.commit})`);
  for (const item of plan.links) console.log(`  linked  ${item.label}`);
  for (const item of plan.copies) console.log(`  copied  ${item.label}`);
  for (const warning of [...plan.warnings, ...applyWarnings]) {
    console.warn(`warning: ${warning}`);
  }
  console.log(`\nNext: cd ${target}`);
}

function removeWorktree(slug) {
  const root = mainCheckoutRoot();
  const target = assertUnderWorktrees(root, worktreeDir(root, slug));
  const branch = branchFor(slug);

  if (!pathExists(target)) {
    throw new Error(`no worktree at ${target}`);
  }

  // Unlink first: `git worktree remove` must never walk into the main
  // checkout's node_modules through a junction.
  const removed = removeWorktreeLinks(target);
  const result = git(["worktree", "remove", "--force", target], root);
  if (result.status !== 0) {
    throw new Error(
      `git worktree remove failed: ${(result.stderr || result.stdout).trim()}`,
    );
  }

  console.log(`Removed worktree ${target}`);
  if (removed.length > 0) console.log(`  unlinked ${removed.join(", ")}`);
  console.log(`Branch ${branch} kept.`);
}

function usage() {
  console.log("Usage: node scripts/agent-worktree.mjs new <slug> [--base <ref>]");
  console.log("       node scripts/agent-worktree.mjs rm <slug>");
  console.log("");
  console.log("new  Creates .claude/worktrees/<slug> on branch ticket/<slug>.");
  console.log("     Links web/node_modules and desktop/node_modules from the main");
  console.log("     checkout (a missing one is skipped with a warning), links");
  console.log("     .scratch, links local docs folders, and copies local docs/*.md");
  console.log("     files. The tracked docs/agents directory is left as the");
  console.log("     worktree's own; an existing target is never overwritten.");
  console.log("rm   Unlinks those junctions and symlinks (never their targets),");
  console.log("     then runs git worktree remove --force. The branch stays.");
}

function main(argv) {
  const [command, ...rest] = argv;
  if (command === "--help" || command === "-h" || command === "help") {
    usage();
    return 0;
  }
  if (command === "new") {
    newWorktree(parseNewArgs(rest));
    return 0;
  }
  if (command === "rm") {
    if (rest.length !== 1) throw new Error("rm needs exactly one ticket slug");
    removeWorktree(rest[0]);
    return 0;
  }
  usage();
  return 1;
}

const isMain =
  process.argv[1] !== undefined &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMain) {
  try {
    process.exitCode = main(process.argv.slice(2));
  } catch (err) {
    console.error(err.message);
    process.exitCode = 1;
  }
}
