"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { LABELS, labels, menuTemplate } = require("../menu.cjs");
const { languageFromLocale } = require("../locale.cjs");

const noop = () => {};

function actions() {
  return {
    reloadInterface: noop,
    openNotes: noop,
    openSettings: noop,
    openLogs: noop,
    openBrowse: noop,
    checkForUpdates: noop,
    showAbout: noop,
  };
}

test("the menu follows the system language", () => {
  assert.equal(languageFromLocale("zh-CN"), "zh");
  assert.equal(languageFromLocale("zh-TW"), "zh");
  assert.equal(languageFromLocale("en-US"), "en");
  assert.equal(languageFromLocale("de-DE"), "en");
  assert.equal(languageFromLocale(""), "en");
});

test("both languages name every entry", () => {
  const keys = Object.keys(LABELS.en).sort();
  assert.deepEqual(Object.keys(LABELS.zh).sort(), keys);
  for (const key of keys) {
    assert.ok(LABELS.zh[key].length > 0, `zh.${key} is empty`);
    assert.ok(LABELS.en[key].length > 0, `en.${key} is empty`);
  }
});

test("the menu offers the folders the app cannot open for you", () => {
  const text = labels("en");
  const flat = JSON.stringify(menuTemplate("en", actions()));

  for (const name of [text.notebook, text.file, text.browse, text.view, text.help]) {
    assert.ok(flat.includes(name), `missing top-level entry ${name}`);
  }
  for (const name of [text.openNotes, text.openSettings, text.openLogs]) {
    assert.ok(flat.includes(name), `missing folder entry ${name}`);
  }
});

test("Help ends the menu with a manual check and the version", () => {
  for (const language of ["zh", "en"]) {
    const text = labels(language);
    const template = menuTemplate(language, actions());
    const last = template[template.length - 1];

    assert.equal(last.label, text.help);
    assert.deepEqual(
      last.submenu.map((item) => item.label),
      [text.checkUpdates, text.about],
    );
  }
});

test("every menu entry that is not a system role does something", () => {
  for (const language of ["zh", "en"]) {
    const template = menuTemplate(language, actions());
    for (const top of template) {
      assert.ok(top.label, "a top-level entry has no label");
      for (const item of top.submenu) {
        if (item.type === "separator") continue;
        assert.ok(item.label, "an entry has no label");
        if (item.role) continue;
        assert.equal(typeof item.click, "function", `${item.label} does nothing`);
      }
    }
  }
});

test("no entry leaks a command or a stage name", () => {
  const flat = JSON.stringify([LABELS.zh, LABELS.en]);
  assert.doesNotMatch(flat, /npm|noteagent serve|端口|构建阶段|terminal|5174|8765/i);
});
