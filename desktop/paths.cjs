"use strict";

// Where the shell writes what the user may need to find by hand.
//
// Two homes, and the split is deliberate. The configuration home is hidden and
// holds secrets and logs, which nobody opens for their own sake; the data home
// is visible and holds the notes, which people do open — in Explorer, or in
// another editor. Keeping them together would put one of the two in the wrong
// place.

const os = require("node:os");
const path = require("node:path");

const APP_FOLDER = "NoteAgent";

function configHome(env = process.env) {
  const appData = env.APPDATA || path.join(os.homedir(), "AppData", "Roaming");
  return path.join(appData, APP_FOLDER);
}

function dataHome(env = process.env) {
  const explicit = String(env.NOTEAGENT_HOME || "").trim();
  return explicit || path.join(os.homedir(), APP_FOLDER);
}

function logsDir(env = process.env) {
  return path.join(configHome(env), "logs");
}

function coreLogPath(env = process.env) {
  return path.join(logsDir(env), "core.log");
}

function shellLogPath(env = process.env) {
  return path.join(logsDir(env), "shell.log");
}

function envFilePath(env = process.env) {
  return path.join(configHome(env), ".env");
}

module.exports = {
  configHome,
  coreLogPath,
  dataHome,
  envFilePath,
  logsDir,
  shellLogPath,
};
