// Keep the three version strings in a checkout from drifting apart.
//
// `desktop/package.json` is the one source: the installer, the release tag and
// the app's own version all come from it. The Python package reports a version
// too, and `pyproject.toml` names it, so both have to say the same thing. The
// build stamps the copy of the core it ships; this module supplies that stamp
// and the checks that run before and after it.
//
// Usage: node scripts/version.mjs check
//        node scripts/version.mjs check-runtime <path-to-__init__.py>
//        node scripts/version.mjs check-tag <ref-type> <ref-name>

import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
// The app decides which release tags it understands with this parser. Using
// the same one here means the workflow cannot publish a version the app would
// refuse to read.
const { parseVersion } = require("../desktop/version-checker.cjs");

const DESKTOP_PACKAGE = path.join(repoRoot, "desktop", "package.json");
const PYPROJECT = path.join(repoRoot, "pyproject.toml");
const CORE_INIT = path.join(repoRoot, "src", "noteagent", "__init__.py");

function readText(file) {
  return readFileSync(file, "utf8").replace(/\r\n/g, "\n");
}

export function readDesktopVersion(file = DESKTOP_PACKAGE) {
  const data = JSON.parse(readText(file));
  if (typeof data.version !== "string" || data.version === "") {
    throw new Error(`No version in ${file}`);
  }
  return data.version;
}

export function readPyprojectVersion(file = PYPROJECT) {
  let inProject = false;
  for (const line of readText(file).split("\n")) {
    const section = /^\s*\[([^\]]+)\]\s*$/.exec(line);
    if (section) {
      inProject = section[1].trim() === "project";
      continue;
    }
    if (!inProject) continue;
    const match = /^\s*version\s*=\s*(?:"([^"]*)"|'([^']*)')\s*(?:#.*)?$/.exec(line);
    if (match) return match[1] ?? match[2];
  }
  throw new Error(`No [project] version in ${file}`);
}

const CORE_VERSION = /^[ \t]*__version__[ \t]*=[ \t]*(?:"([^"\n]*)"|'([^'\n]*)')[ \t]*(?:#.*)?$/m;

export function readCoreVersion(file = CORE_INIT) {
  const match = CORE_VERSION.exec(readText(file));
  if (!match) throw new Error(`No __version__ assignment in ${file}`);
  return match[1] ?? match[2];
}

const CORE_VERSION_LINE = /^([ \t]*__version__[ \t]*=[ \t]*)(["'])([^"'\r\n]*)\2([ \t]*(?:#.*)?\r?)$/m;

export function stampCoreVersion(file, version) {
  const content = readFileSync(file, "utf8");
  if (!CORE_VERSION_LINE.test(content)) {
    throw new Error(`No __version__ assignment in ${file}`);
  }
  const stamped = content.replace(
    CORE_VERSION_LINE,
    (_, prefix, quote, _old, suffix) => `${prefix}${quote}${version}${quote}${suffix}`,
  );
  writeFileSync(file, stamped, "utf8");
  return version;
}

export function readVersions(root = repoRoot) {
  return {
    desktop: readDesktopVersion(path.join(root, "desktop", "package.json")),
    pyproject: readPyprojectVersion(path.join(root, "pyproject.toml")),
    core: readCoreVersion(path.join(root, "src", "noteagent", "__init__.py")),
  };
}

const SOURCE_FILES = {
  desktop: "desktop/package.json",
  pyproject: "pyproject.toml",
  core: "src/noteagent/__init__.py",
};

export function checkVersions(root = repoRoot) {
  const versions = readVersions(root);
  const drift = Object.entries(versions).filter(([, value]) => value !== versions.desktop);
  if (drift.length > 0) {
    const found = Object.entries(versions)
      .map(([source, value]) => `${SOURCE_FILES[source]} is ${value}`)
      .join(", ");
    throw new Error(`Version drift: ${found}`);
  }
  return versions;
}

export function checkRuntimeVersion(runtimeInit, root = repoRoot) {
  const expected = readDesktopVersion(path.join(root, "desktop", "package.json"));
  const actual = readCoreVersion(runtimeInit);
  if (actual !== expected) {
    throw new Error(`Runtime core is ${actual} but desktop/package.json is ${expected}`);
  }
  return actual;
}

/**
 * Decide whether a workflow ref may release the version in desktop/package.json.
 *
 * The version has to be plain semver in the parser's own terms, and a tag ref
 * has to be exactly `v<version>`. Anything else is a release built from a ref
 * that does not name it.
 */
export function checkTagVersion(refType, refName, root = repoRoot) {
  const version = readDesktopVersion(path.join(root, "desktop", "package.json"));
  const parsed = parseVersion(version);
  if (!parsed || version !== parsed.join(".")) {
    throw new Error(`desktop/package.json version "${version}" is not plain semver`);
  }
  if (refType === "tag" && refName !== `v${version}`) {
    throw new Error(`tag ${refName} does not match desktop/package.json (v${version})`);
  }
  return version;
}

function usage() {
  console.error("Usage: node scripts/version.mjs check");
  console.error("       node scripts/version.mjs check-runtime <path-to-__init__.py>");
  console.error("       node scripts/version.mjs check-tag <ref-type> <ref-name>");
  process.exit(1);
}

function main(argv) {
  const [command, first, second] = argv.slice(2);
  try {
    if (command === "check") {
      const versions = checkVersions();
      console.log(`Versions agree at ${versions.desktop}`);
      return;
    }
    if (command === "check-runtime") {
      if (!first) usage();
      const version = checkRuntimeVersion(path.resolve(first));
      console.log(`Runtime core version is ${version}`);
      return;
    }
    if (command === "check-tag") {
      if (!first || !second) usage();
      console.log(checkTagVersion(first, second));
      return;
    }
    usage();
  } catch (err) {
    console.error(err.message);
    process.exit(1);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv);
}
