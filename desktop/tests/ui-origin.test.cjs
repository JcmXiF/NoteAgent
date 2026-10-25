"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { makeIsUiOrigin } = require("../ui-origin.cjs");

const CANDIDATES = [
  "http://127.0.0.1:5174",
  "http://localhost:5174",
];

const isUiOrigin = makeIsUiOrigin(CANDIDATES);

test("accepts the app's own origin, with and without a path", () => {
  assert.equal(isUiOrigin("http://127.0.0.1:5174"), true);
  assert.equal(isUiOrigin("http://127.0.0.1:5174/"), true);
  assert.equal(isUiOrigin("http://127.0.0.1:5174/docs?x=1#y"), true);
});

test("accepts an alternative name for this machine", () => {
  assert.equal(isUiOrigin("http://localhost:5174/chat"), true);
});

test("accepts the shell's own placeholder page", () => {
  assert.equal(isUiOrigin("data:text/html;charset=utf-8,<p>hi</p>"), true);
});

test("rejects a host that merely starts with the same text", () => {
  // The reason this is compared by origin: a prefix test would trust these.
  assert.equal(isUiOrigin("http://127.0.0.1:5174.example.com/x"), false);
  assert.equal(isUiOrigin("http://127.0.0.1:51740/"), false);
  assert.equal(isUiOrigin("http://127.0.0.1:5174.evil.test/"), false);
});

test("rejects other ports, hosts and schemes", () => {
  assert.equal(isUiOrigin("http://127.0.0.1:9999/"), false);
  assert.equal(isUiOrigin("https://example.com/"), false);
  assert.equal(isUiOrigin("file:///etc/passwd"), false);
  assert.equal(isUiOrigin("about:blank"), false);
});

test("rejects anything that is not a URL", () => {
  assert.equal(isUiOrigin(""), false);
  assert.equal(isUiOrigin(undefined), false);
  assert.equal(isUiOrigin("not a url"), false);
});

test("refuses to build a matcher from a bad candidate", () => {
  assert.throws(() => makeIsUiOrigin(["not a url"]), /not a URL/);
});
