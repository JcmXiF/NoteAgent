"use strict";

// The two pages the shell shows around the interface: one while it starts, one
// when it could not.
//
// They are local documents rather than the interface, because at both moments the
// interface is exactly what is missing. The wording is product wording: no file
// names, no commands, no stages. The one exception is the log location on the
// failure screen, which is there because it is the only way a person can find out
// what happened.

const TEXT = {
  zh: {
    starting: "正在启动",
    startingBody: "正在启动本地引擎，请稍候。",
    failed: "启动失败",
    retry: "再试一次",
    logLabel: "日志",
    configLabel: "配置",
    reasons: {
      "core-missing": "本地引擎没有随应用一起安装。重新安装应用通常可以解决。",
      "core-exited": "本地引擎启动后立即停止。",
      "core-timeout": "本地引擎没有响应。",
      "core-crashed": "本地引擎连续多次停止。请重试；若仍未恢复，请重新启动应用。",
      "interface-timeout": "界面没有响应。",
      unknown: "启动过程中出现问题。",
    },
  },
  en: {
    starting: "Starting",
    startingBody: "Starting the local engine. This takes a moment.",
    failed: "Startup failed",
    retry: "Try again",
    logLabel: "Log",
    configLabel: "Settings",
    reasons: {
      "core-missing": "The local engine is not installed with this app. Reinstalling usually fixes it.",
      "core-exited": "The local engine stopped right after it started.",
      "core-timeout": "The local engine is not responding.",
      "core-crashed":
        "The local engine stopped several times in a row. Try again; if that does not help, restart the app.",
      "interface-timeout": "The interface is not responding.",
      unknown: "The app could not be started.",
    },
  },
};

const SHARED_STYLE = `
  html, body { height: 100%; }
  body { margin: 0; display: flex; align-items: center; justify-content: center;
    font-family: "Segoe UI", "Microsoft YaHei", sans-serif; color: #15202b;
    background: #d8e2ea; }
  main { width: min(46rem, calc(100vw - 6rem)); }
  h1 { font-size: 1.35rem; margin: 0 0 0.6rem; font-weight: 600; }
  p { line-height: 1.6; margin: 0.35rem 0; }
  .detail { color: #41525f; font-size: 0.95rem; }
  button { margin-top: 1.2rem; padding: 0.5rem 1.2rem; font: inherit;
    border: 1px solid #6b7f8d; border-radius: 4px; background: #f3f7fa;
    color: inherit; cursor: pointer; }
  button:hover { background: #ffffff; }
  pre { margin-top: 1rem; padding: 0.8rem; max-height: 15rem; overflow: auto;
    background: #eef3f6; border: 1px solid #c3d0d9; border-radius: 4px;
    font-family: Consolas, monospace; font-size: 0.8rem; line-height: 1.45;
    white-space: pre-wrap; word-break: break-word; }
  .path { font-family: Consolas, monospace; font-size: 0.85rem; }
`;

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function asDocument(title, language, body) {
  return `<!doctype html>
<html lang="${language === "zh" ? "zh-CN" : "en"}">
<head>
<meta charset="utf-8" />
<title>${escapeHtml(title)}</title>
<style>${SHARED_STYLE}</style>
</head>
<body>
<main>
${body}
</main>
</body>
</html>`;
}

function asDataUrl(html) {
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

function bootScreen(language = "en") {
  const text = TEXT[language] ?? TEXT.en;
  return asDataUrl(
    asDocument(
      text.starting,
      language,
      `<h1>${escapeHtml(text.starting)}</h1>
<p class="detail">${escapeHtml(text.startingBody)}</p>`,
    ),
  );
}

function failureScreen({
  language = "en",
  reason = "unknown",
  logPath = "",
  logTail = [],
  configPath = "",
  configMissing = false,
} = {}) {
  const text = TEXT[language] ?? TEXT.en;
  const sentence = text.reasons[reason] || text.reasons.unknown;
  const tail = Array.isArray(logTail) && logTail.length
    ? `<pre>${escapeHtml(logTail.join("\n"))}</pre>`
    : "";
  const log = logPath
    ? `<p class="detail">${escapeHtml(text.logLabel)}: ` +
      `<span class="path">${escapeHtml(logPath)}</span></p>`
    : "";
  const config = configMissing && configPath
    ? `<p class="detail">${escapeHtml(text.configLabel)}: ` +
      `<span class="path">${escapeHtml(configPath)}</span></p>`
    : "";
  return asDataUrl(
    asDocument(
      text.failed,
      language,
      `<h1>${escapeHtml(text.failed)}</h1>
<p class="detail">${escapeHtml(sentence)}</p>
${log}
${config}
${tail}
<button type="button" id="retry">${escapeHtml(text.retry)}</button>
<script>
document.getElementById("retry").addEventListener("click", function () {
  var bridge = window.noteagentScreen;
  if (bridge) bridge.retry();
});
</script>`,
    ),
  );
}

module.exports = {
  TEXT,
  bootScreen,
  escapeHtml,
  failureScreen,
};
