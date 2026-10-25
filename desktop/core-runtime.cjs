"use strict";

// How the shell starts the core in a packaged app, and why it does not in a
// checkout.
//
// A checkout runs the core through whatever Python the developer installed, and
// the shell is started by a command the developer typed; the ordering there
// belongs to whoever is working, not to the app. A packaged app owns the engine:
// it spawns it, gives it a home, and keeps its output.
//
// The runtime is a CPython distribution that travels with the app, so the command
// is a path inside the installed resources rather than a name looked up on PATH.
// That is also why a missing runtime is reported as such instead of being retried:
// no amount of waiting makes a file appear.

const fs = require("node:fs");
const path = require("node:path");

const CORE_DIR = "core";
const PYTHON = path.join("python", "python.exe");

class CoreMissingError extends Error {
  constructor(searched) {
    super(`no core runtime at ${searched}`);
    this.searched = searched;
  }
}

function modeFor({ isPackaged }) {
  return isPackaged ? "prod" : "dev";
}

function coreRuntimePath(resourcesPath) {
  return path.join(resourcesPath, CORE_DIR);
}

function pythonPath(resourcesPath) {
  return path.join(coreRuntimePath(resourcesPath), PYTHON);
}

/**
 * The command that starts the core, or `null` in a checkout.
 *
 * The port is the preferred one with `--fallback-port`: a busy port means "take a
 * free one", and the address is read back from the record the core writes.
 *
 * The core is told where the shell keeps its log and that it is being watched.
 * Both are facts the core cannot work out for itself, and the diagnostics screen
 * is where they are read back: a restart count with no log to look at is a number
 * without a story.
 */
function coreLaunch({
  mode,
  resourcesPath,
  configHome,
  dataHome,
  logDir,
  env = process.env,
  exists = fs.existsSync,
}) {
  if (mode !== "prod") return null;
  const python = pythonPath(resourcesPath);
  if (!exists(python)) throw new CoreMissingError(python);
  return {
    command: python,
    args: ["-m", "noteagent.api", "--port", "8765", "--fallback-port"],
    cwd: configHome,
    env: {
      ...env,
      NOTEAGENT_HOME: dataHome,
      NOTEAGENT_SUPERVISED: "1",
      ...(logDir ? { NOTEAGENT_LOG_DIR: logDir } : {}),
      // The runtime ignores this, but nothing else should be able to change what
      // the core imports by leaving it set in the environment it was started from.
      PYTHONPATH: "",
    },
  };
}

module.exports = {
  CoreMissingError,
  coreLaunch,
  coreRuntimePath,
  modeFor,
  pythonPath,
};
