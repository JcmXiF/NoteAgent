"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  showAbout,
  showAvailableNotice,
  showManualResult,
} = require("../version-notice.cjs");

const DOWNLOAD_URL = "https://github.com/JcmXiF/NoteAgent/releases/tag/v0.2.0";

function fakeDialog(response = 0) {
  const calls = [];
  return {
    calls,
    showMessageBox: async (options) => {
      calls.push(options);
      return { response };
    },
  };
}

function available(overrides = {}) {
  return {
    kind: "available",
    version: "0.2.0",
    currentVersion: "0.1.0",
    notes: "Faster startup and fewer crashes.",
    url: DOWNLOAD_URL,
    ...overrides,
  };
}

test("an available update shows its version, notes and a download button", async () => {
  const dialog = fakeDialog(0);
  const opened = [];

  await showManualResult({
    result: available(),
    language: "zh",
    dialog,
    openExternal: async (url) => opened.push(url),
  });

  assert.equal(dialog.calls.length, 1);
  const options = dialog.calls[0];
  assert.match(options.message, /0\.2\.0/);
  assert.match(options.detail, /Faster startup and fewer crashes\./);
  assert.match(options.detail, /Windows 可能提示未知发布者/);
  assert.deepEqual(options.buttons, ["打开下载页", "稍后"]);
  assert.deepEqual(opened, [DOWNLOAD_URL]);
});

test("the English dialog carries the same warning", async () => {
  const dialog = fakeDialog(0);
  const opened = [];

  await showManualResult({
    result: available(),
    language: "en",
    dialog,
    openExternal: async (url) => opened.push(url),
  });

  const options = dialog.calls[0];
  assert.match(options.message, /0\.2\.0/);
  assert.match(options.detail, /Faster startup and fewer crashes\./);
  assert.match(options.detail, /unknown publisher/);
  assert.match(options.detail, /quit NoteAgent before installing/i);
  assert.deepEqual(options.buttons, ["Open download page", "Later"]);
  assert.deepEqual(opened, [DOWNLOAD_URL]);
});

test("choosing Later leaves the browser alone", async () => {
  const dialog = fakeDialog(1);
  const opened = [];

  await showManualResult({
    result: available(),
    language: "en",
    dialog,
    openExternal: async (url) => opened.push(url),
  });

  assert.deepEqual(opened, []);
});

test("a long release note is cut at 300 characters and keeps the warning", async () => {
  const dialog = fakeDialog(1);
  const notes = "x".repeat(4000);

  await showManualResult({
    result: available({ notes }),
    language: "zh",
    dialog,
    openExternal: async () => {},
  });

  const options = dialog.calls[0];
  const summary = options.detail.split("\n\n")[0];
  assert.ok(summary.length <= 300, `the summary was ${summary.length} characters`);
  assert.match(summary, /…$/);
  assert.match(options.detail, /Windows 可能提示未知发布者/);
});

test("release headings and markup are turned into plain text", async () => {
  const dialog = fakeDialog(1);
  const notes =
    "## v0.1.0\n\n" +
    "- **Faster** [startup](https://example.com/startup) and `fewer` crashes.\n" +
    "* Also _quieter_.\n" +
    "1. Numbered item.\n";

  await showManualResult({
    result: available({ notes }),
    language: "zh",
    dialog,
    openExternal: async () => {},
  });

  const detail = dialog.calls[0].detail;
  assert.doesNotMatch(detail, /##/);
  assert.doesNotMatch(detail, /\*\*|`|\[startup\]/);
  assert.doesNotMatch(detail, /^- /m);
  assert.match(detail, /Faster startup and fewer crashes\./);
  assert.match(detail, /Also quieter\./);
  assert.match(detail, /Numbered item\./);
  assert.match(detail, /Windows 可能提示未知发布者/);
});

test("the latest version says so instead of offering a download", async () => {
  for (const [language, expected, ok] of [
    ["zh", "已是最新版本 v0.1.0", "确定"],
    ["en", "Already the latest version v0.1.0", "OK"],
  ]) {
    const dialog = fakeDialog(0);
    const opened = [];

    await showManualResult({
      result: { kind: "none", currentVersion: "0.1.0" },
      language,
      dialog,
      openExternal: async (url) => opened.push(url),
    });

    const options = dialog.calls[0];
    assert.equal(options.message, expected);
    assert.deepEqual(options.buttons, [ok]);
    assert.deepEqual(opened, []);
  }
});

test("a failed check shows a short message and opens nothing", async () => {
  for (const [language, expected, ok] of [
    ["zh", "无法检查更新（网络或 API 错误），请稍后再试。", "确定"],
    ["en", "Couldn't check for updates (network or API error). Try again later.", "OK"],
  ]) {
    const dialog = fakeDialog(0);
    const opened = [];

    await showManualResult({
      result: { kind: "error", reason: "network" },
      language,
      dialog,
      openExternal: async (url) => opened.push(url),
    });

    const options = dialog.calls[0];
    assert.equal(options.message, expected);
    assert.deepEqual(options.buttons, [ok]);
    assert.deepEqual(opened, []);
  }
});

function fakeNotification({ supported = true } = {}) {
  const instances = [];
  class FakeNotification {
    static isSupported() {
      return supported;
    }

    constructor(options) {
      this.options = options;
      this.handlers = {};
      this.shown = false;
      instances.push(this);
    }

    on(event, handler) {
      (this.handlers[event] ??= []).push(handler);
    }

    show() {
      this.shown = true;
    }

    click() {
      for (const handler of this.handlers.click ?? []) handler();
    }
  }
  FakeNotification.instances = instances;
  return FakeNotification;
}

test("an automatic update appears as a native notification", async () => {
  const Notification = fakeNotification();
  const dialog = fakeDialog(0);
  const opened = [];

  await showAvailableNotice({
    result: available(),
    language: "zh",
    Notification,
    dialog,
    openExternal: async (url) => opened.push(url),
  });

  assert.equal(Notification.instances.length, 1);
  const notification = Notification.instances[0];
  assert.match(notification.options.title, /0\.2\.0/);
  assert.match(notification.options.body, /Faster startup and fewer crashes\./);
  assert.equal(notification.shown, true);
  assert.equal(dialog.calls.length, 0);

  notification.click();
  assert.deepEqual(opened, [DOWNLOAD_URL]);
});

test("without native notifications the update falls back to a dialog", async () => {
  const Notification = fakeNotification({ supported: false });
  const dialog = fakeDialog(0);
  const opened = [];

  await showAvailableNotice({
    result: available(),
    language: "zh",
    Notification,
    dialog,
    openExternal: async (url) => opened.push(url),
  });

  assert.equal(Notification.instances.length, 0);
  assert.equal(dialog.calls.length, 1);
  assert.deepEqual(dialog.calls[0].buttons, ["打开下载页", "稍后"]);
  assert.deepEqual(opened, [DOWNLOAD_URL]);
});

test("a shell with no Notification at all falls back to the dialog", async () => {
  const dialog = fakeDialog(1);
  const opened = [];

  await showAvailableNotice({
    result: available(),
    language: "en",
    Notification: undefined,
    dialog,
    openExternal: async (url) => opened.push(url),
  });

  assert.equal(dialog.calls.length, 1);
  assert.deepEqual(dialog.calls[0].buttons, ["Open download page", "Later"]);
  assert.deepEqual(opened, []);
});

test("the notification body keeps the notes to a couple of lines", async () => {
  const Notification = fakeNotification();
  const notes = "First line.\nSecond line.\nA third line nobody reads in a toast.";

  await showAvailableNotice({
    result: available({ notes }),
    language: "en",
    Notification,
    dialog: fakeDialog(0),
    openExternal: async () => {},
  });

  const body = Notification.instances[0].options.body;
  assert.match(body, /First line\./);
  assert.match(body, /Second line\./);
  assert.doesNotMatch(body, /three|third/i);
});

test("a short notification still warns about installing", async () => {
  const Notification = fakeNotification();

  await showAvailableNotice({
    result: available(),
    language: "zh",
    Notification,
    dialog: fakeDialog(0),
    openExternal: async () => {},
  });

  assert.match(Notification.instances[0].options.body, /Windows 可能提示未知发布者/);
});

test("the notification turns release markup into plain text", async () => {
  const Notification = fakeNotification();
  const notes = "## v0.2.0\n\n**Faster** [startup](https://example.com/startup).";

  await showAvailableNotice({
    result: available({ notes }),
    language: "en",
    Notification,
    dialog: fakeDialog(0),
    openExternal: async () => {},
  });

  const body = Notification.instances[0].options.body;
  assert.doesNotMatch(body, /##/);
  assert.doesNotMatch(body, /\*\*|\[startup\]/);
  assert.match(body, /Faster startup\./);
  assert.match(body, /unknown publisher/);
});

test("a long changelog is shortened but still warns about installing", async () => {
  const Notification = fakeNotification();

  await showAvailableNotice({
    result: available({ notes: "x".repeat(4000) }),
    language: "zh",
    Notification,
    dialog: fakeDialog(0),
    openExternal: async () => {},
  });

  const body = Notification.instances[0].options.body;
  assert.ok(body.length <= 240, `notification body was ${body.length} characters`);
  assert.match(body, /…/);
  assert.match(body, /Windows 可能提示未知发布者/);
});

test("About shows the version that is running", async () => {
  for (const [language, title] of [
    ["zh", "关于 NoteAgent"],
    ["en", "About NoteAgent"],
  ]) {
    const dialog = fakeDialog(0);

    await showAbout({ appVersion: "0.1.0", language, dialog });

    const options = dialog.calls[0];
    assert.equal(options.title, title);
    assert.match(options.message, /NoteAgent v0\.1\.0/);
  }
});
