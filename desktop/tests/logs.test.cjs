"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { openLogFd, rotateOnStart, tailLines, writeLine } = require("../logs.cjs");

function tempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "noteagent-logs-test-"));
}

test("a log smaller than the limit is kept as it is", () => {
  const dir = tempDir();
  const file = path.join(dir, "core.log");
  fs.writeFileSync(file, "small", "utf8");

  assert.equal(rotateOnStart(file, { maxBytes: 1024 }), false);
  assert.equal(fs.readFileSync(file, "utf8"), "small");
  assert.equal(fs.existsSync(`${file}.1`), false);
});

test("a log over the limit keeps exactly one previous generation", () => {
  const dir = tempDir();
  const file = path.join(dir, "core.log");
  fs.writeFileSync(`${file}.1`, "older than the last one", "utf8");
  fs.writeFileSync(file, "x".repeat(2048), "utf8");

  assert.equal(rotateOnStart(file, { maxBytes: 1024 }), true);
  assert.equal(fs.readFileSync(`${file}.1`, "utf8").length, 2048);
  assert.equal(fs.existsSync(file), false);
});

test("rotating a log that is not there is not an error", () => {
  const dir = tempDir();
  assert.equal(rotateOnStart(path.join(dir, "missing.log")), false);
});

test("the tail is the last lines, oldest first", () => {
  const dir = tempDir();
  const file = path.join(dir, "core.log");
  fs.writeFileSync(file, ["one", "two", "three", "four"].join("\n") + "\n", "utf8");

  assert.deepEqual(tailLines(file, { lines: 2 }), ["three", "four"]);
});

test("a tail of a file that is missing is empty, not a crash", () => {
  assert.deepEqual(tailLines(path.join(tempDir(), "nope.log")), []);
});

test("the tail reads a bounded part of a large file", () => {
  const dir = tempDir();
  const file = path.join(dir, "core.log");
  fs.writeFileSync(file, "y".repeat(10000) + "\nlast line\n", "utf8");

  const lines = tailLines(file, { lines: 1, maxBytes: 100 });

  assert.deepEqual(lines, ["last line"]);
});

test("opening the log creates its folder and appends", () => {
  const dir = tempDir();
  const file = path.join(dir, "nested", "core.log");
  const fd = openLogFd(file);
  try {
    fs.writeSync(fd, "first\n");
  } finally {
    fs.closeSync(fd);
  }
  const fd2 = openLogFd(file);
  try {
    fs.writeSync(fd2, "second\n");
  } finally {
    fs.closeSync(fd2);
  }

  assert.equal(fs.readFileSync(file, "utf8"), "first\nsecond\n");
});

test("a shell log line never takes the shell down", () => {
  // The path is a directory, which cannot be appended to.
  const dir = tempDir();
  assert.doesNotThrow(() => writeLine(dir, "hello"));
});
