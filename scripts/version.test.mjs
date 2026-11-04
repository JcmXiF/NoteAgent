import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  checkRuntimeVersion,
  checkTagVersion,
  checkVersions,
  readCoreVersion,
  readVersions,
  stampCoreVersion,
} from "./version.mjs";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// A checkout in miniature: the three files that carry a version, in the paths
// the real repository uses.
function makeTree({ desktop = "0.1.0", pyproject = desktop, core = desktop, coreFile } = {}) {
  const root = mkdtempSync(path.join(tmpdir(), "noteagent-version-"));
  mkdirSync(path.join(root, "desktop"), { recursive: true });
  mkdirSync(path.join(root, "src", "noteagent"), { recursive: true });
  writeFileSync(
    path.join(root, "desktop", "package.json"),
    `${JSON.stringify({ name: "noteagent-desktop", version: desktop }, null, 2)}\n`,
  );
  writeFileSync(
    path.join(root, "pyproject.toml"),
    [
      "[project]",
      'name = "noteagent"',
      `version = "${pyproject}"`,
      "",
      "[project.optional-dependencies]",
      'api = ["fastapi>=0.115"]',
      "",
      "[tool.unrelated]",
      'version = "9.9.9"',
      "",
    ].join("\n"),
  );
  writeFileSync(
    path.join(root, "src", "noteagent", "__init__.py"),
    coreFile ?? `"""NOTEAGENT."""\n\n__version__ = "${core}"\n`,
  );
  return root;
}

function cleanUp(t, root) {
  t.after(() => rmSync(root, { recursive: true, force: true }));
}

test("reads each version source and accepts them when they agree", (t) => {
  const root = makeTree();
  cleanUp(t, root);

  assert.deepEqual(readVersions(root), {
    desktop: "0.1.0",
    pyproject: "0.1.0",
    core: "0.1.0",
  });
  assert.deepEqual(checkVersions(root), {
    desktop: "0.1.0",
    pyproject: "0.1.0",
    core: "0.1.0",
  });
});

test("fails when pyproject.toml drifts from the desktop version", (t) => {
  const root = makeTree({ pyproject: "0.2.0" });
  cleanUp(t, root);

  assert.throws(() => checkVersions(root), /pyproject\.toml is 0\.2\.0/);
});

test("fails when the core drifts from the desktop version", (t) => {
  const root = makeTree({ core: "0.2.0" });
  cleanUp(t, root);

  assert.throws(() => checkVersions(root), /src\/noteagent\/__init__\.py is 0\.2\.0/);
});

test("stamping rewrites only the __version__ line", (t) => {
  const coreFile = [
    '"""NOTEAGENT: automated structured note ingestion and wiki maintenance."""',
    "",
    'SUPPORTED_FORMAT = "0.1.0"',
    "",
    '__version__ = "0.1.0"',
    "",
  ].join("\n");
  const root = makeTree({ desktop: "0.2.0", pyproject: "0.2.0", coreFile });
  cleanUp(t, root);
  const init = path.join(root, "src", "noteagent", "__init__.py");

  stampCoreVersion(init, "0.2.0");

  assert.equal(
    readFileSync(init, "utf8"),
    [
      '"""NOTEAGENT: automated structured note ingestion and wiki maintenance."""',
      "",
      'SUPPORTED_FORMAT = "0.1.0"',
      "",
      '__version__ = "0.2.0"',
      "",
    ].join("\n"),
  );
  assert.equal(readCoreVersion(init), "0.2.0");
});

test("fails when the core has no __version__ line", (t) => {
  const root = makeTree({ coreFile: '"""NOTEAGENT."""\n' });
  cleanUp(t, root);
  const init = path.join(root, "src", "noteagent", "__init__.py");

  assert.throws(() => readCoreVersion(init), /__version__/);
  assert.throws(() => stampCoreVersion(init, "0.1.0"), /__version__/);
});

test("checks the built runtime against desktop/package.json", (t) => {
  const root = makeTree({ desktop: "0.1.0" });
  cleanUp(t, root);
  const runtimeInit = path.join(root, "build", "runtime", "core", "src", "noteagent", "__init__.py");
  mkdirSync(path.dirname(runtimeInit), { recursive: true });

  writeFileSync(runtimeInit, '__version__ = "0.1.0"\n');
  assert.equal(checkRuntimeVersion(runtimeInit, root), "0.1.0");

  writeFileSync(runtimeInit, '__version__ = "0.2.0"\n');
  assert.throws(() => checkRuntimeVersion(runtimeInit, root), /0\.2\.0.*0\.1\.0/);
});

test("the check command accepts the repository's versions", () => {
  const result = spawnSync(process.execPath, ["scripts/version.mjs", "check"], {
    cwd: repoRoot,
    encoding: "utf8",
  });

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Versions agree at \d+\.\d+\.\d+/);
});

test("check-tag accepts a plain version for any ref", (t) => {
  const root = makeTree({ desktop: "0.2.3" });
  cleanUp(t, root);

  assert.equal(checkTagVersion("branch", "feature/anything", root), "0.2.3");
  assert.equal(checkTagVersion("workflow_dispatch", "main", root), "0.2.3");
});

test("check-tag rejects a version the app would also reject", (t) => {
  for (const desktop of ["01.2.3", "1.2", "1.2.3.4", "not-a-version", "v1.2.3"]) {
    const root = makeTree({ desktop });
    cleanUp(t, root);
    assert.throws(
      () => checkTagVersion("branch", "main", root),
      /not plain semver/,
      `${desktop} was accepted`,
    );
  }
});

test("check-tag requires a tag ref to name the package version", (t) => {
  const root = makeTree({ desktop: "0.2.3" });
  cleanUp(t, root);

  assert.equal(checkTagVersion("tag", "v0.2.3", root), "0.2.3");
  assert.throws(
    () => checkTagVersion("tag", "v0.2.2", root),
    /tag v0\.2\.2 does not match .*\(v0\.2\.3\)/,
  );
});

test("the check-tag command prints the repository version", () => {
  const version = readVersions(repoRoot).desktop;
  const result = spawnSync(
    process.execPath,
    ["scripts/version.mjs", "check-tag", "branch", "some-branch"],
    { cwd: repoRoot, encoding: "utf8" },
  );

  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), version);
});
