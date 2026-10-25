"use strict";

// The guest browser: one WebContentsView per tab, over a session of its own.
//
// It is a separate session on purpose. Signing in to a site here leaves cookies
// that belong to this module and to nothing else, and the interface's own
// requests never see them. The view is also a separate surface, laid over the
// window rather than inside the page, which is why the interface has to say where
// its bounds are and the shell has to redraw them on every frame.

const { ipcMain, WebContentsView, screen } = require("electron");
const {
  cookiePresence,
  httpAccessReason,
  isMainDocumentNavigation,
  parseHttpUrl,
  publicGuestAuth,
} = require("./guest-auth.cjs");
const { textFor } = require("./guest-text.cjs");

/** The page a tab with no address shows: a note about what this module is for. */
function placeholderFor(words) {
  const html = `<!doctype html>
<html lang="${words.htmlLang}">
<head>
  <meta charset="utf-8" />
  <title>${words.newTab}</title>
  <style>
    body { margin: 0; padding: 48px 40px; font-family: "Segoe UI", sans-serif;
      color: #15202b; background: #d8e2ea; }
    h1 { font-size: 1.4rem; }
    p { line-height: 1.55; max-width: 36em; }
  </style>
</head>
<body>
  <h1>${words.placeholderHeading}</h1>
  <p>${words.placeholderLine1}</p>
  <p>${words.placeholderLine2}</p>
</body>
</html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

function isPlaceholderUrl(raw) {
  const text = String(raw || "");
  return text === "about:blank" || text.startsWith("data:text/html");
}

function createGuestShell(opts) {
  const { snapshot, sendToUi, guestSession, isTrustedSender, language } = opts;
  const words = textFor(language);
  const placeholderUrl = placeholderFor(words);

  let hostWindow = null;
  let pageBounds = { x: 0, y: 0, width: 0, height: 0, visible: false };
  let tabs = [];
  let activeId = 0;
  let nextId = 1;
  let frameRelayout = null;

  function activeTab() {
    return tabs.find((tab) => tab.id === activeId) || null;
  }

  function contentsOf(tab) {
    if (!tab || tab.view.webContents.isDestroyed()) return null;
    return tab.view.webContents;
  }

  function tabUrl(tab) {
    const contents = contentsOf(tab);
    return contents ? contents.getURL() : "";
  }

  function tabHasPage(tab = activeTab()) {
    const url = tabUrl(tab);
    return Boolean(url) && !isPlaceholderUrl(url);
  }

  function tabByWebContentsId(id) {
    return (
      tabs.find((tab) => {
        const contents = contentsOf(tab);
        return contents ? contents.id === id : false;
      }) || null
    );
  }

  async function refreshCookieSummary(tab) {
    if (!tab) return;
    const url = tabHasPage(tab) ? tabUrl(tab) : "";
    if (!url) {
      tab.hasCookies = false;
      tab.cookieCount = 0;
      return;
    }
    try {
      const cookies = await guestSession().cookies.get({ url });
      const summary = cookiePresence(cookies);
      tab.hasCookies = summary.hasCookies;
      tab.cookieCount = summary.cookieCount;
    } catch {
      tab.hasCookies = false;
      tab.cookieCount = 0;
    }
  }

  function hookGuestSession() {
    const sess = guestSession();
    if (sess.__noteagentGuestAuth) return;
    sess.__noteagentGuestAuth = true;
    sess.webRequest.onCompleted(
      { urls: ["http://*/*", "https://*/*"] },
      (details) => {
        if (details.resourceType !== "mainFrame") return;
        const tab = tabByWebContentsId(details.webContentsId);
        if (!tab) return;
        if (typeof details.statusCode === "number") {
          tab.httpStatus = details.statusCode;
        }
        void refreshCookieSummary(tab).then(() => {
          pushChromeState();
        });
      },
    );
    sess.cookies.on("changed", () => {
      const tab = activeTab();
      if (!tab || !tabHasPage(tab)) return;
      void refreshCookieSummary(tab).then(() => {
        pushChromeState();
      });
    });
  }

  function serializeState() {
    const current = activeTab();
    const contents = contentsOf(current);
    const address = tabHasPage(current) ? tabUrl(current) : "";
    const auth = publicGuestAuth(current || {});
    return {
      tabs: tabs.map((tab) => {
        const url = tabHasPage(tab) ? tabUrl(tab) : "";
        const tabAuth = publicGuestAuth(tab);
        return {
          id: tab.id,
          title: tab.title || (url ? url : words.newTab),
          url,
          loading: Boolean(tab.loading),
          active: tab.id === activeId,
          httpStatus: tabAuth.httpStatus,
          hasCookies: tabAuth.hasCookies,
          cookieCount: tabAuth.cookieCount,
        };
      }),
      activeId,
      address,
      canGoBack: contents ? contents.navigationHistory.canGoBack() : false,
      canGoForward: contents ? contents.navigationHistory.canGoForward() : false,
      httpStatus: auth.httpStatus,
      hasCookies: auth.hasCookies,
      cookieCount: auth.cookieCount,
    };
  }

  function emitActiveUrl() {
    const current = activeTab();
    const url = tabHasPage(current) ? tabUrl(current) : "";
    sendToUi("desktop:guest-url", url);
  }

  function pushChromeState() {
    const state = serializeState();
    sendToUi("desktop:guest-state", state);
  }

  function layout() {
    const shown = Boolean(
      pageBounds.visible && pageBounds.width > 2 && pageBounds.height > 2,
    );
    const x = Math.round(pageBounds.x);
    const y = Math.round(pageBounds.y);
    const width = Math.round(pageBounds.width);
    const height = Math.round(pageBounds.height);
    for (const tab of tabs) {
      if (shown) {
        tab.view.setBounds({ x, y, width, height });
      }
      tab.view.setVisible(shown && tab.id === activeId);
    }
  }

  function fillUiView() {
    if (!hostWindow || hostWindow.isDestroyed()) return;
    const [width, height] = hostWindow.getContentSize();
    if (!width || !height) return;
    const guests = new Set(tabs.map((tab) => tab.view));
    const children = hostWindow.contentView?.children ?? [];
    for (const child of children) {
      if (guests.has(child) || typeof child.setBounds !== "function") continue;
      const current = typeof child.getBounds === "function" ? child.getBounds() : null;
      if (
        current &&
        current.x === 0 &&
        current.y === 0 &&
        current.width === width &&
        current.height === height
      ) {
        continue;
      }
      child.setBounds({ x: 0, y: 0, width, height });
    }
  }

  function relayoutWindow() {
    fillUiView();
    sendToUi("desktop:frame");
    layout();
  }

  function destroyTab(tab) {
    try {
      if (!tab.view.webContents.isDestroyed()) {
        tab.view.webContents.close();
      }
    } catch {
      /* already gone */
    }
    if (hostWindow && !hostWindow.isDestroyed()) {
      hostWindow.contentView.removeChildView(tab.view);
    }
  }

  function switchTab(id) {
    const tab = tabs.find((item) => item.id === id);
    if (!tab) return;
    activeId = id;
    layout();
    const contents = contentsOf(tab);
    if (pageBounds.visible && contents) contents.focus();
    emitActiveUrl();
    pushChromeState();
    void refreshCookieSummary(tab).then(() => {
      pushChromeState();
    });
  }

  function coerceHttpUrl(raw) {
    const text = String(raw || "").trim();
    const candidate = /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(text)
      ? text
      : `https://${text}`;
    try {
      return parseHttpUrl(candidate);
    } catch (err) {
      throw new Error(err.code === "not-http" ? words.notHttp : words.notUrl);
    }
  }

  function guardGuestContents(contents) {
    if (!contents || contents.__noteagentGuarded) return;
    contents.__noteagentGuarded = true;
    contents.setWindowOpenHandler((details) => {
      try {
        const href = parseHttpUrl(details.url);
        addTab(href, { activate: details.disposition !== "background-tab" });
        sendToUi("desktop:show-browse");
      } catch {
        /* deny */
      }
      return { action: "deny" };
    });
    contents.on("will-navigate", (event, url) => {
      if (isPlaceholderUrl(url)) return;
      try {
        parseHttpUrl(url);
      } catch {
        event.preventDefault();
      }
    });
  }

  function attachTabEvents(tab) {
    const contents = tab.view.webContents;
    guardGuestContents(contents);
    const refresh = () => {
      if (contents.isDestroyed()) return;
      tab.title = contents.getTitle() || tab.title;
      if (tab.id === activeId) emitActiveUrl();
      pushChromeState();
    };
    contents.on("page-title-updated", (_event, title) => {
      tab.title = title;
      refresh();
    });
    contents.on("did-start-navigation", (...args) => {
      if (!isMainDocumentNavigation(args)) return;
      tab.httpStatus = null;
      tab.hasCookies = false;
      tab.cookieCount = 0;
      if (tab.id === activeId) pushChromeState();
    });
    contents.on("did-navigate", (_event, _url, httpResponseCode) => {
      if (typeof httpResponseCode === "number" && httpResponseCode > 0) {
        tab.httpStatus = httpResponseCode;
      }
      refresh();
    });
    contents.on("did-navigate-in-page", refresh);
    contents.on("did-start-loading", () => {
      tab.loading = true;
      pushChromeState();
    });
    contents.on("did-stop-loading", () => {
      tab.loading = false;
      void refreshCookieSummary(tab).then(refresh);
    });
  }

  function addTab(rawUrl, options = {}) {
    const activate = options.activate !== false;
    if (!hostWindow || hostWindow.isDestroyed()) {
      throw new Error(words.noWindow);
    }
    const tab = {
      id: nextId,
      title: words.newTab,
      loading: false,
      httpStatus: null,
      hasCookies: false,
      cookieCount: 0,
      view: new WebContentsView({
        webPreferences: {
          session: guestSession(),
          sandbox: true,
          contextIsolation: true,
          nodeIntegration: false,
          webSecurity: true,
        },
      }),
    };
    nextId += 1;
    tabs.push(tab);
    hostWindow.contentView.addChildView(tab.view);
    attachTabEvents(tab);
    const href = rawUrl ? coerceHttpUrl(rawUrl) : placeholderUrl;
    void tab.view.webContents.loadURL(href);
    if (activate) activeId = tab.id;
    fillUiView();
    layout();
    if (activate) emitActiveUrl();
    pushChromeState();
    return tab;
  }

  function navigateActive(rawUrl) {
    const tab = activeTab();
    if (!tab) {
      addTab(rawUrl);
      return;
    }
    const href = coerceHttpUrl(rawUrl);
    void tab.view.webContents.loadURL(href);
  }

  function closeTab(id) {
    const index = tabs.findIndex((tab) => tab.id === id);
    if (index < 0) return;
    const [removed] = tabs.splice(index, 1);
    destroyTab(removed);
    if (tabs.length === 0) {
      addTab("", { activate: true });
      return;
    }
    if (activeId === id) {
      const next = tabs[Math.max(0, index - 1)];
      switchTab(next.id);
    } else {
      layout();
      pushChromeState();
    }
  }

  async function captureActive({ notifyUi }) {
    const tab = activeTab();
    const contents = contentsOf(tab);
    if (!contents) throw new Error(words.noPage);
    const url = tabUrl(tab);
    try {
      parseHttpUrl(url);
    } catch {
      throw new Error(words.notUrl);
    }
    await refreshCookieSummary(tab);
    pushChromeState();
    const reason = httpAccessReason(tab.httpStatus);
    if (reason === "http_unauthorized") throw new Error(words.httpUnauthorized);
    if (reason === "http_forbidden") throw new Error(words.httpForbidden);
    const payload = await contents.executeJavaScript(snapshot, true);
    if (!payload || typeof payload.html !== "string" || !payload.html.trim()) {
      throw new Error(words.noHtml);
    }
    const result = { url: String(payload.url || url), html: payload.html };
    if (notifyUi) sendToUi("desktop:captured", result);
    return result;
  }

  function attachHost(win) {
    hostWindow = win;
    frameRelayout = () => relayoutWindow();
    win.on("resize", frameRelayout);
    win.on("resized", frameRelayout);
    win.on("maximize", frameRelayout);
    win.on("unmaximize", frameRelayout);
    win.on("enter-full-screen", frameRelayout);
    win.on("leave-full-screen", frameRelayout);
    win.on("show", frameRelayout);
    win.webContents.on("did-finish-load", frameRelayout);
    screen.on("display-metrics-changed", frameRelayout);
    if (tabs.length === 0) addTab("", { activate: true });
    relayoutWindow();
  }

  function setPageBounds(bounds) {
    pageBounds = {
      x: Number(bounds.x) || 0,
      y: Number(bounds.y) || 0,
      width: Number(bounds.width) || 0,
      height: Number(bounds.height) || 0,
      visible: Boolean(bounds.visible),
    };
    layout();
  }

  function openUrl(rawUrl) {
    const href = coerceHttpUrl(rawUrl);
    const existing = tabs.find((tab) => tabHasPage(tab) && tabUrl(tab) === href);
    if (existing) {
      switchTab(existing.id);
    } else {
      const current = activeTab();
      const contents = contentsOf(current);
      if (contents && !tabHasPage(current)) {
        void contents.loadURL(href);
      } else {
        addTab(href);
      }
    }
    sendToUi("desktop:show-browse");
    pushChromeState();
  }

  function dispose() {
    if (frameRelayout) {
      screen.removeListener("display-metrics-changed", frameRelayout);
      frameRelayout = null;
    }
    for (const tab of tabs) destroyTab(tab);
    tabs = [];
    activeId = 0;
    hostWindow = null;
    pageBounds = { x: 0, y: 0, width: 0, height: 0, visible: false };
    sendToUi("desktop:guest-url", "");
    pushChromeState();
  }

  function onActive(action) {
    const contents = contentsOf(activeTab());
    if (contents) action(contents);
  }

  const guestHandlers = [
    ["chrome:get-state", () => serializeState()],
    ["chrome:new-tab", (_event, url) => addTab(url || "")],
    ["chrome:close-tab", (_event, id) => closeTab(Number(id))],
    ["chrome:switch-tab", (_event, id) => switchTab(Number(id))],
    ["chrome:navigate", (_event, url) => navigateActive(url)],
    ["chrome:back", () => onActive((contents) => contents.navigationHistory.back())],
    [
      "chrome:forward",
      () => onActive((contents) => contents.navigationHistory.forward()),
    ],
    ["chrome:reload", () => onActive((contents) => contents.reload())],
    ["chrome:capture", () => captureActive({ notifyUi: true })],
    ["desktop:open-guest", (_event, url) => openUrl(url)],
    ["desktop:capture-guest", () => captureActive({ notifyUi: true })],
    ["desktop:set-guest-bounds", (_event, bounds) => setPageBounds(bounds || {})],
  ];
  hookGuestSession();
  for (const [channel, handler] of guestHandlers) {
    ipcMain.removeHandler(channel);
    ipcMain.handle(channel, (event, ...args) => {
      if (!isTrustedSender(event)) throw new Error(words.refused);
      return handler(event, ...args);
    });
  }

  return {
    attachHost,
    setPageBounds,
    openUrl,
    captureActive,
    dispose,
    guardGuestContents,
  };
}

module.exports = { createGuestShell };
