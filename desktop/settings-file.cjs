"use strict";

// The settings file a packaged install starts with.
//
// The settings screen writes this file once the app is running, but it only
// writes the keys it has fields for. First run seeds the whole list, commented
// out, so the settings that screen does not show — where the notes go, where an
// update check looks — are still named somewhere the user can find them. An
// existing file is never touched: it holds the user's key.

const fs = require("node:fs");
const path = require("node:path");

function ensureSettingsFile({ target, template, fileSystem = fs }) {
  try {
    if (fileSystem.existsSync(target)) return false;
    fileSystem.mkdirSync(path.dirname(target), { recursive: true });
    const content = fileSystem.readFileSync(template, "utf8");
    fileSystem.writeFileSync(target, content, "utf8");
    return true;
  } catch {
    // A missing settings file is not a startup failure; the app still opens, and
    // the failure screen names this path when a request needs a model.
    return false;
  }
}

module.exports = { ensureSettingsFile };
