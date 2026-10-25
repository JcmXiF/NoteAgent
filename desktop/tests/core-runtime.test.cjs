"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const {
  CoreMissingError,
  coreLaunch,
  coreRuntimePath,
  modeFor,
  pythonPath,
} = require("../core-runtime.cjs");

const RESOURCES = path.join("C:", "Program Files", "NoteAgent", "resources");

test("a checkout is dev and a packaged app is not", () => {
  assert.equal(modeFor({ isPackaged: false }), "dev");
  assert.equal(modeFor({ isPackaged: true }), "prod");
});

test("a checkout starts no core: the developer does", () => {
  assert.equal(
    coreLaunch({
      mode: "dev",
      resourcesPath: RESOURCES,
      configHome: "C:\\cfg",
      dataHome: "C:\\notes",
    }),
    null,
  );
});

test("the runtime is the copy inside the app, not whatever is on PATH", () => {
  assert.equal(coreRuntimePath(RESOURCES), path.join(RESOURCES, "core"));
  assert.equal(
    pythonPath(RESOURCES),
    path.join(RESOURCES, "core", "python", "python.exe"),
  );
});

test("a packaged app starts the core with the fallback port", () => {
  const launch = coreLaunch({
    mode: "prod",
    resourcesPath: RESOURCES,
    configHome: "C:\\cfg",
    dataHome: "C:\\notes",
    env: { PATH: "C:\\Windows" },
    exists: () => true,
  });

  assert.equal(launch.command, pythonPath(RESOURCES));
  assert.deepEqual(launch.args, [
    "-m",
    "noteagent.api",
    "--port",
    "8765",
    "--fallback-port",
  ]);
  assert.equal(launch.cwd, "C:\\cfg");
  assert.equal(launch.env.NOTEAGENT_HOME, "C:\\notes");
  assert.equal(launch.env.PATH, "C:\\Windows");
  // Whatever the shell was started with must not change what the core imports.
  assert.equal(launch.env.PYTHONPATH, "");
});

test("a packaged core is told it is watched, and where its log is", () => {
  const launch = coreLaunch({
    mode: "prod",
    resourcesPath: RESOURCES,
    configHome: "C:\\cfg",
    dataHome: "C:\\notes",
    logDir: "C:\\cfg\\logs",
    env: {},
    exists: () => true,
  });

  assert.equal(launch.env.NOTEAGENT_SUPERVISED, "1");
  assert.equal(launch.env.NOTEAGENT_LOG_DIR, "C:\\cfg\\logs");
});

test("an installed app that keeps no log folder says so by leaving it out", () => {
  const launch = coreLaunch({
    mode: "prod",
    resourcesPath: RESOURCES,
    configHome: "C:\\cfg",
    dataHome: "C:\\notes",
    env: {},
    exists: () => true,
  });

  assert.equal("NOTEAGENT_LOG_DIR" in launch.env, false);
});

test("a missing runtime is named, not waited for", () => {
  assert.throws(
    () =>
      coreLaunch({
        mode: "prod",
        resourcesPath: RESOURCES,
        configHome: "C:\\cfg",
        dataHome: "C:\\notes",
        exists: () => false,
      }),
    (err) => err instanceof CoreMissingError && err.searched.includes("python.exe"),
  );
});
