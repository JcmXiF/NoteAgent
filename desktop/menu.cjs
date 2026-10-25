"use strict";

// The window menu, in the same language the system uses.
//
// The menu is built by the shell, so it cannot read the interface's preference;
// what it can do is follow the operating system, which is also what the
// interface falls back to when nobody has picked a language. Every entry names
// something a person can point at — never a build stage, a file layout, or a
// command.
//
// A folder entry exists for each home, because the settings screen edits the
// configuration file without opening it: the menu is how someone gets to the
// files at all — the notes, that `.env`, and the logs.

const LABELS = {
  zh: {
    notebook: "笔记",
    reloadInterface: "重新加载界面",
    file: "文件",
    openNotes: "打开笔记文件夹",
    openSettings: "打开配置文件夹",
    openLogs: "打开日志文件夹",
    browse: "浏览",
    openBrowse: "打开浏览模块",
    view: "查看",
    reload: "重新加载",
    forceReload: "强制重新加载",
    devTools: "开发者工具",
    fullscreen: "全屏",
  },
  en: {
    notebook: "Notebook",
    reloadInterface: "Reload interface",
    file: "File",
    openNotes: "Open notes folder",
    openSettings: "Open settings folder",
    openLogs: "Open logs folder",
    browse: "Browse",
    openBrowse: "Open browse module",
    view: "View",
    reload: "Reload",
    forceReload: "Force reload",
    devTools: "Developer tools",
    fullscreen: "Full screen",
  },
};

function labels(language) {
  return LABELS[language] ?? LABELS.en;
}

// Walk the plain template and put the click handlers and roles in. Kept separate
// from `Menu.buildFromTemplate` so the shape can be tested without Electron.
function menuTemplate(language, actions) {
  const text = labels(language);
  return [
    {
      label: text.notebook,
      submenu: [
        { label: text.reloadInterface, click: actions.reloadInterface },
      ],
    },
    {
      label: text.file,
      submenu: [
        { label: text.openNotes, click: actions.openNotes },
        { label: text.openSettings, click: actions.openSettings },
        { label: text.openLogs, click: actions.openLogs },
      ],
    },
    {
      label: text.browse,
      submenu: [{ label: text.openBrowse, click: actions.openBrowse }],
    },
    {
      label: text.view,
      submenu: [
        { role: "reload", label: text.reload },
        { role: "forceReload", label: text.forceReload },
        { role: "toggleDevTools", label: text.devTools },
        { type: "separator" },
        { role: "togglefullscreen", label: text.fullscreen },
      ],
    },
  ];
}

module.exports = { LABELS, labels, menuTemplate };
