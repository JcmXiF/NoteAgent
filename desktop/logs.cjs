"use strict";

// The two log files, and the one rule that makes them useful.
//
// The core's output is written through a file descriptor shared with the child
// process, so it captures what Python says before it can run any of its own
// logging — a missing DLL, a wheel that will not load, an import error. That
// output has no timestamps and is never parsed: it is there to be read by a
// person after something went wrong.
//
// Rotation happens once, at startup, and keeps exactly one previous file. The
// run that failed hardest is often the one that could not write anything about
// itself; the previous file is then the only evidence left.

const fs = require("node:fs");
const path = require("node:path");

const MAX_BYTES = 5 * 1024 * 1024;
const TAIL_LINES = 30;

function rotateOnStart(file, { maxBytes = MAX_BYTES, fileSystem = fs } = {}) {
  let size = 0;
  try {
    size = fileSystem.statSync(file).size;
  } catch {
    return false;
  }
  if (size <= maxBytes) return false;
  try {
    fileSystem.renameSync(file, `${file}.1`);
  } catch {
    // A log that cannot be rotated is still better than no log.
    try {
      fileSystem.truncateSync(file, 0);
      return false;
    } catch {
      return false;
    }
  }
  return true;
}

function openLogFd(file, { fileSystem = fs } = {}) {
  fileSystem.mkdirSync(path.dirname(file), { recursive: true });
  return fileSystem.openSync(file, "a");
}

// The last lines of a file, as text. Used by the failure screen, so it must not
// throw for any of the ways a file can be missing or unreadable.
function tailLines(file, { lines = TAIL_LINES, maxBytes = 64 * 1024, fileSystem = fs } = {}) {
  let content;
  try {
    const stat = fileSystem.statSync(file);
    const start = Math.max(0, stat.size - maxBytes);
    const length = stat.size - start;
    const buffer = Buffer.alloc(length);
    const fd = fileSystem.openSync(file, "r");
    try {
      fileSystem.readSync(fd, buffer, 0, length, start);
    } finally {
      fileSystem.closeSync(fd);
    }
    content = buffer.toString("utf8");
  } catch {
    return [];
  }
  const all = content.split(/\r?\n/);
  if (all.length && all[all.length - 1] === "") all.pop();
  return all.slice(-lines);
}

function writeLine(file, line, { fileSystem = fs } = {}) {
  try {
    fileSystem.mkdirSync(path.dirname(file), { recursive: true });
    fileSystem.appendFileSync(file, `${line}\n`, "utf8");
  } catch {
    // Losing a shell log line must never take the window down with it.
  }
}

module.exports = {
  openLogFd,
  rotateOnStart,
  tailLines,
  writeLine,
};
