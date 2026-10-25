"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { TEXT, textFor } = require("../guest-text.cjs");

test("both languages name the same things", () => {
  const keys = Object.keys(TEXT.en).sort();
  assert.deepEqual(Object.keys(TEXT.zh).sort(), keys);
  for (const key of keys) {
    assert.ok(TEXT.zh[key].length > 0, `zh.${key} is empty`);
    assert.ok(TEXT.en[key].length > 0, `en.${key} is empty`);
  }
});

test("the English catalog says nothing in Chinese", () => {
  assert.doesNotMatch(JSON.stringify(TEXT.en), /[\u4e00-\u9fff]/);
});

test("an unknown language falls back to English", () => {
  assert.equal(textFor("de"), TEXT.en);
  assert.equal(textFor(undefined), TEXT.en);
  assert.equal(textFor("zh"), TEXT.zh);
});
