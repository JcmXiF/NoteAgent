"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { TEXT, bootScreen, escapeHtml, failureScreen } = require("../screens.cjs");

function decoded(dataUrl) {
  assert.ok(dataUrl.startsWith("data:text/html;charset=utf-8,"));
  return decodeURIComponent(dataUrl.slice("data:text/html;charset=utf-8,".length));
}

test("the starting page says what is happening and nothing else", () => {
  const html = decoded(bootScreen("zh"));

  assert.match(html, /正在启动/);
  // No commands, paths, ports or stage names: this is a product state.
  assert.doesNotMatch(html, /npm|serve|5174|8765|终端|terminal|http/i);
});

test("the starting page has an English version", () => {
  assert.match(decoded(bootScreen("en")), /Starting the local engine/);
});

test("a failure says what happened, where the log is, and offers a retry", () => {
  const html = decoded(
    failureScreen({
      language: "zh",
      reason: "core-missing",
      logPath: "C:\\Users\\x\\AppData\\Roaming\\NoteAgent\\logs\\core.log",
      logTail: ["line one", "line two"],
    }),
  );

  assert.match(html, /启动失败/);
  assert.match(html, /没有随应用一起安装/);
  assert.match(html, /core\.log/);
  assert.match(html, /line one/);
  assert.match(html, /id="retry"/);
});

test("the retry button asks the shell, not the page", () => {
  const html = decoded(failureScreen({ language: "en" }));

  assert.match(html, /noteagentScreen/);
  assert.match(html, /retry\(\)/);
});

test("an unknown reason still says something rather than nothing", () => {
  const html = decoded(failureScreen({ language: "en", reason: "not-a-reason" }));

  assert.match(html, /could not be started/);
});

test("each reason has a sentence in both languages", () => {
  const keys = Object.keys(TEXT.zh.reasons).sort();
  assert.deepEqual(Object.keys(TEXT.en.reasons).sort(), keys);
  for (const key of keys) {
    assert.ok(TEXT.zh.reasons[key].length > 0, `zh.${key} is empty`);
    assert.ok(TEXT.en.reasons[key].length > 0, `en.${key} is empty`);
  }
});

test("a log line cannot inject markup into the failure page", () => {
  const html = decoded(
    failureScreen({
      language: "en",
      reason: "core-timeout",
      logTail: ["<script>alert(1)</script>"],
    }),
  );

  assert.doesNotMatch(html, /<script>alert\(1\)<\/script>/);
  assert.match(html, /&lt;script&gt;/);
});

test("a settings path is only shown when there is no settings file yet", () => {
  const withHint = decoded(
    failureScreen({
      language: "en",
      configPath: "C:\\cfg\\.env",
      configMissing: true,
    }),
  );
  const withoutHint = decoded(
    failureScreen({
      language: "en",
      configPath: "C:\\cfg\\.env",
      configMissing: false,
    }),
  );

  assert.match(withHint, /C:\\cfg\\\.env/);
  assert.doesNotMatch(withoutHint, /C:\\cfg\\\.env/);
});

test("escaping covers the characters that can end an attribute", () => {
  assert.equal(escapeHtml('<a href="x">&'), "&lt;a href=&quot;x&quot;&gt;&amp;");
});
