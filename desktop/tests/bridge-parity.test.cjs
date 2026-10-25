"use strict";

/**
 * The shell's bridge and the interface's own type have to name the same things.
 *
 * They are two files in two languages, and nothing else compares them: a method
 * renamed on one side compiles on both and then quietly does nothing at run time.
 * This reads the preload as the renderer would receive it and compares the names
 * against the type the interface is written against.
 */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const path = require("node:path");
const { test } = require("node:test");

const PRELOAD = path.join(__dirname, "..", "preload-ui.cjs");
const TYPES = path.join(
  __dirname,
  "..",
  "..",
  "web",
  "src",
  "lib",
  "desktop.ts",
);

/** Runs the preload with a stand-in for Electron and returns what it exposed. */
function exposedBridge(calls = []) {
  const exposed = {};
  const original = Module._load;
  Module._load = function (request, parent, isMain) {
    if (request === "electron") {
      return {
        contextBridge: {
          exposeInMainWorld: (name, api) => {
            exposed[name] = api;
          },
        },
        ipcRenderer: {
          invoke: (...args) => calls.push(args),
          on: () => {},
          removeListener: () => {},
        },
      };
    }
    return original.call(this, request, parent, isMain);
  };
  try {
    delete require.cache[require.resolve(PRELOAD)];
    require(PRELOAD);
  } finally {
    Module._load = original;
  }
  return exposed;
}

/**
 * The names in the interface's `NoteagentDesktop` type.
 *
 * Read as text rather than compiled: this package does not build the frontend, and
 * a list of names is all this check needs. A field with a `?` is optional, which is
 * what the interface allows a shell to be missing.
 */
function declaredNames() {
  const source = fs.readFileSync(TYPES, "utf8");
  const start = source.indexOf("export type NoteagentDesktop = {");
  assert.notEqual(start, -1, "NoteagentDesktop type not found in desktop.ts");
  const end = source.indexOf("\n};", start);
  assert.notEqual(end, -1, "NoteagentDesktop type is not closed");
  const body = source.slice(start, end);

  const required = [];
  const optional = [];
  for (const line of body.split("\n")) {
    const match = /^\s{2}([A-Za-z][A-Za-z0-9]*)(\??):/.exec(line);
    if (!match) continue;
    (match[2] === "?" ? optional : required).push(match[1]);
  }
  return { required, optional };
}

test("the shell exposes everything the interface requires", () => {
  const exposed = exposedBridge().noteagentDesktop;
  assert.ok(exposed, "the preload exposed no noteagentDesktop");
  const { required } = declaredNames();

  const missing = required.filter((name) => typeof exposed[name] !== "function");
  assert.deepEqual(
    missing,
    [],
    `the shell does not provide these required methods: ${missing.join(", ")}`,
  );
});

test("the interface knows about everything the shell exposes", () => {
  const exposed = exposedBridge().noteagentDesktop;
  const { required, optional } = declaredNames();
  const known = new Set([...required, ...optional]);

  const unknown = Object.keys(exposed).filter((name) => !known.has(name));
  assert.deepEqual(
    unknown,
    [],
    `the shell exposes methods the interface cannot use: ${unknown.join(", ")}`,
  );
});

test("everything the shell exposes is a method", () => {
  const exposed = exposedBridge().noteagentDesktop;
  // The interface describes calls; a value where a call is expected would fail only
  // when that call is made.
  for (const [name, value] of Object.entries(exposed)) {
    assert.equal(typeof value, "function", `${name} is not a method`);
  }
});

test("the startup screens get their own bridge, and only a retry", () => {
  const exposed = exposedBridge().noteagentScreen;

  assert.ok(exposed, "the preload exposed no noteagentScreen");
  assert.deepEqual(Object.keys(exposed), ["retry"]);
  assert.equal(typeof exposed.retry, "function");
});

test("a retry from the failure screen asks the shell to start again", () => {
  const calls = [];
  const exposed = exposedBridge(calls);

  exposed.noteagentScreen.retry();

  assert.deepEqual(calls, [["shell:retry"]]);
});
