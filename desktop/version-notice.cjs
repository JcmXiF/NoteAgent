"use strict";

// What the update dialogs say.
//
// A manual check always ends in a dialog, so the outcomes have one place that
// turns them into words. The shell hands in the dialog and the browser; nothing
// here imports Electron, which is what lets the wording and the button meanings
// be tested directly.
//
// "Open download page" is the only button that does anything beyond closing.
// The app never downloads, runs, or installs an update itself.

const NOTES_LIMIT = 500;

const TEXT = {
  zh: {
    checkTitle: "检查更新",
    available: (version) => `发现新版本 v${version}`,
    openDownload: "打开下载页",
    later: "稍后",
    warning: "Windows 可能提示未知发布者；请先退出 NoteAgent 再安装。",
    latest: (version) => `已是最新版本 v${version}`,
    failed: "无法检查更新（网络或 API 错误），请稍后再试。",
    ok: "确定",
    aboutTitle: "关于 NoteAgent",
    about: (version) => `NoteAgent v${version}`,
  },
  en: {
    checkTitle: "Check for updates",
    available: (version) => `NoteAgent v${version} is available`,
    openDownload: "Open download page",
    later: "Later",
    warning:
      "Windows may warn about an unknown publisher; quit NoteAgent before installing.",
    latest: (version) => `Already the latest version v${version}`,
    failed: "Couldn't check for updates (network or API error). Try again later.",
    ok: "OK",
    aboutTitle: "About NoteAgent",
    about: (version) => `NoteAgent v${version}`,
  },
};

function textFor(language) {
  return TEXT[language] ?? TEXT.en;
}

// Release notes can be any length. The dialog is a prompt, not a changelog, and
// the whole body stays one click away on the release page.
function notesSummary(notes) {
  const text = String(notes || "").trim();
  if (text.length <= NOTES_LIMIT) return text;
  return `${text.slice(0, NOTES_LIMIT).trimEnd()}…`;
}

function plainDialog(title, message, ok) {
  return {
    type: "info",
    title,
    message,
    buttons: [ok],
    defaultId: 0,
    cancelId: 0,
    noLink: true,
  };
}

async function showManualResult({ result, language, dialog, openExternal }) {
  const text = textFor(language);

  if (result.kind === "none") {
    await dialog.showMessageBox(
      plainDialog(text.checkTitle, text.latest(result.currentVersion), text.ok),
    );
    return;
  }
  if (result.kind !== "available") {
    await dialog.showMessageBox(plainDialog(text.checkTitle, text.failed, text.ok));
    return;
  }

  const summary = notesSummary(result.notes);
  const answer = await dialog.showMessageBox({
    type: "info",
    title: text.checkTitle,
    message: text.available(result.version),
    detail: summary ? `${summary}\n\n${text.warning}` : text.warning,
    buttons: [text.openDownload, text.later],
    defaultId: 0,
    cancelId: 1,
    noLink: true,
  });

  if (answer && answer.response === 0) {
    try {
      await openExternal(result.url);
    } catch {
      // The browser is outside this app's control; a dialog already told the
      // user what happened, and a failure to open one cannot improve on it.
    }
  }
}

// About answers the first question in every bug report: which version is
// running. The version is handed in when the entry is chosen, not captured when
// the menu is built.
async function showAbout({ appVersion, language, dialog }) {
  const text = textFor(language);
  await dialog.showMessageBox(
    plainDialog(text.aboutTitle, text.about(appVersion), text.ok),
  );
}

module.exports = { showAbout, showManualResult };
