"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const os = require("node:os");
const path = require("node:path");

const {
  configFilePath,
  configHome,
  coreLogPath,
  dataHome,
  logsDir,
  shellLogPath,
} = require("../paths.cjs");

const ENV = { APPDATA: path.join("C:", "Users", "someone", "AppData", "Roaming") };

test("settings and logs live under the roaming profile", () => {
  assert.equal(configHome(ENV), path.join(ENV.APPDATA, "NoteAgent"));
  assert.equal(logsDir(ENV), path.join(ENV.APPDATA, "NoteAgent", "logs"));
  assert.equal(coreLogPath(ENV), path.join(ENV.APPDATA, "NoteAgent", "logs", "core.log"));
  assert.equal(shellLogPath(ENV), path.join(ENV.APPDATA, "NoteAgent", "logs", "shell.log"));
  assert.equal(configFilePath(ENV), path.join(ENV.APPDATA, "NoteAgent", "config.json"));
  assert.equal(
    configFilePath({ ...ENV, NOTEAGENT_CONFIG_DIR: path.join("D:", "portable") }),
    path.join("D:", "portable", "config.json"),
  );
});

test("notes live in a folder a person can find", () => {
  const home = dataHome({ NOTEAGENT_HOME: "" });
  assert.equal(home, path.join(os.homedir(), "NoteAgent"));
});

test("a configured home is used exactly as given", () => {
  const home = path.join("D:", "notes-home");
  assert.equal(dataHome({ NOTEAGENT_HOME: home }), home);
  assert.equal(dataHome({ NOTEAGENT_HOME: "  " + home + "  " }), home);
});

test("the two homes are different places", () => {
  // Secrets and logs are hidden; the notes are not. One folder for both would put
  // one of them somewhere it does not belong.
  const env = { APPDATA: path.join("C:", "roaming") };
  assert.notEqual(configHome(env), dataHome(env));
});
