"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { ensureSettingsFile } = require("../settings-file.cjs");

function tempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "noteagent-settings-test-"));
}

test("a first run leaves a settings file with the keys in it", () => {
  const dir = tempDir();
  const template = path.join(dir, "template.txt");
  const target = path.join(dir, "config", ".env");
  fs.writeFileSync(template, "NOTEAGENT_LLM_API_KEY=\n", "utf8");

  assert.equal(ensureSettingsFile({ target, template }), true);
  assert.match(fs.readFileSync(target, "utf8"), /NOTEAGENT_LLM_API_KEY=/);
});

test("a settings file that exists is never overwritten", () => {
  const dir = tempDir();
  const template = path.join(dir, "template.txt");
  const target = path.join(dir, ".env");
  fs.writeFileSync(template, "template\n", "utf8");
  fs.writeFileSync(target, "user key\n", "utf8");

  assert.equal(ensureSettingsFile({ target, template }), false);
  assert.equal(fs.readFileSync(target, "utf8"), "user key\n");
});

test("a missing template is not a reason to fail startup", () => {
  const dir = tempDir();
  assert.equal(
    ensureSettingsFile({
      target: path.join(dir, ".env"),
      template: path.join(dir, "missing.txt"),
    }),
    false,
  );
});
