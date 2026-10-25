"use strict";

// The desktop shell.
//
// Two modes, and the difference is where the interface comes from. In a checkout
// the developer runs the core and the frontend server, and the shell only waits
// for them and loads the frontend server's address. In a packaged app the shell
// starts the core itself, and the core serves the interface, so there is one
// address and one origin.
//
// There is no list of addresses to try and no retry loop. An address is decided
// once, and if it does not answer the window says so and offers to try again.
// Guessing between several addresses is how a stale build gets loaded without
// anyone noticing.
//
// The core is spawned as a child without `detached`, so it shares the app's life:
// quitting the app ends it too. That is the intent — each launch starts its own
// core, and one left running from a previous session would be a second server
// nobody attached to. A core that ends this way leaves its serve record behind,
// which the CLI prunes the next time it reads them.

const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { app, BrowserWindow, Menu, dialog, ipcMain, session, shell } = require("electron");
const { createGuestShell } = require("./guest-shell.cjs");
const { textFor } = require("./guest-text.cjs");
const { CoreMissingError, coreLaunch, modeFor } = require("./core-runtime.cjs");
const { MAX_RESTARTS, nextStep } = require("./core-supervisor.cjs");
const { healthUrl, waitForHealth, waitForServerRecord } = require("./kernel.cjs");
const { languageFromLocale } = require("./locale.cjs");
const { menuTemplate } = require("./menu.cjs");
const { bootScreen, failureScreen } = require("./screens.cjs");
const { makeIsUiOrigin } = require("./ui-origin.cjs");
const { openLogFd, rotateOnStart, tailLines, writeLine } = require("./logs.cjs");
const paths = require("./paths.cjs");
const { ensureSettingsFile } = require("./settings-file.cjs");

const MODE = modeFor({ isPackaged: app.isPackaged });

// Where the developer's two servers answer. A checkout never spawns the core, so
// these are the only addresses it can mean.
const DEV_KERNEL_URL = "http://127.0.0.1:8765";
const DEV_UI_URL = "http://127.0.0.1:5174";

const GUEST_PARTITION = "persist:noteagent-guest";
const SNAPSHOT = fs.readFileSync(path.join(__dirname, "snapshot.js"), "utf8");
const WINDOW_BG = "#d8e2ea";

let uiWindow = null;
let guestShell = null;
let coreChild = null;
// The address the running core answers on, kept so a reload can use the core that
// is already up instead of starting a second one.
let coreUrl = null;
// When the current core was started, and how many starts have been spent without
// one that stayed up. The pair is what decides whether another attempt is allowed.
let coreStartedAt = 0;
let coreAttempts = 0;
// Set before this shell ends the core on purpose, so the exit is not read as a crash.
let stopping = false;
let restartTimer = null;
// The last crash, kept across starts: the run that replaced it did not make the
// crash stop having happened.
let lastExitRecord = null;

// Each attempt to bring the interface up gets a generation. A newer attempt makes
// an older one stop reporting: without this, a slow failure could load its page
// over a retry that has already started, and a click during the last moment of a
// failure would be ignored because the previous attempt had not finished.
let startGeneration = 0;
// The address the window was last told to load. A load failure is only about the
// current attempt, so it is compared against this rather than against whichever
// attempt happens to be running.
let currentAddress = null;
// Replaced as soon as the interface's address is known, which in a packaged app
// is the core's own address and may not be the preferred port.
let isOwnInterface = makeIsUiOrigin([DEV_UI_URL]);

const LANGUAGE = languageFromLocale(app.getLocale());
const WORDS = textFor(LANGUAGE);
const CORE_LOG = paths.coreLogPath();
const SHELL_LOG = paths.shellLogPath();

class StartupFailure extends Error {
  constructor(reason) {
    super(reason);
    this.reason = reason;
  }
}

function log(line) {
  writeLine(SHELL_LOG, `${new Date().toISOString()} ${line}`);
}

function guestSession() {
  return session.fromPartition(GUEST_PARTITION);
}

function sendToUi(channel, payload) {
  if (uiWindow && !uiWindow.isDestroyed()) {
    uiWindow.webContents.send(channel, payload);
  }
}

function openInBrowse(raw) {
  if (!guestShell) return;
  try {
    guestShell.openUrl(raw);
  } catch {
    /* not an http(s) page */
  }
}

// One probe for "is this page being served", used for the frontend server in a
// checkout. Anything the server answers counts; only silence does not.
function probePage(url, timeoutMs = 800) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    try {
      const req = http.get(url, { timeout: timeoutMs }, (res) => {
        res.resume();
        done(Boolean(res.statusCode && res.statusCode < 400));
      });
      req.on("error", () => done(false));
      req.on("timeout", () => {
        req.destroy();
        done(false);
      });
    } catch {
      done(false);
    }
  });
}

async function waitForPage(url, { timeoutMs = 15000, intervalMs = 250 } = {}) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (await probePage(url)) return true;
    if (Date.now() >= deadline) return false;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

function childExited(child) {
  return new Promise((resolve) => {
    if (child.exitCode !== null) {
      resolve(child.exitCode);
      return;
    }
    child.once("exit", (code) => resolve(code));
    child.once("error", () => resolve(null));
  });
}

function spawnCore(launch) {
  fs.mkdirSync(path.dirname(CORE_LOG), { recursive: true });
  rotateOnStart(CORE_LOG);
  const fd = openLogFd(CORE_LOG);
  try {
    return spawn(launch.command, launch.args, {
      cwd: launch.cwd,
      env: launch.env,
      stdio: ["ignore", fd, fd],
      windowsHide: true,
      detached: false,
    });
  } finally {
    fs.closeSync(fd);
  }
}

/**
 * Bring up the interface and return its address, or throw with a reason.
 *
 * The reasons become sentences on the failure screen, so they are a small fixed
 * set rather than exception text: what happened is chosen here, where the
 * difference between "no runtime" and "no answer" is still visible.
 */
async function interfaceAddress() {
  if (MODE === "dev") {
    if (!(await waitForHealth(healthUrl(DEV_KERNEL_URL), { timeoutMs: 30000 }))) {
      throw new StartupFailure("core-timeout");
    }
    if (!(await waitForPage(DEV_UI_URL))) {
      throw new StartupFailure("interface-timeout");
    }
    return DEV_UI_URL;
  }

  // A reload asks the core that is already up. Starting another one would leave
  // the first listening and unattached, which is the state this shell exists to
  // avoid.
  if (coreChild && coreUrl && coreChild.exitCode === null) return coreUrl;
  return await launchCore();
}

function supervisorStatePath() {
  return path.join(paths.logsDir(), "supervisor.json");
}

/**
 * Write down how many starts the window has needed, for the diagnostics screen.
 *
 * The core cannot see its own restarts — each start is a new process — so the
 * shell is the only one that can say. Written through a rename: the core may read
 * this at any moment, and half a file would be reported as no file at all.
 */
function writeSupervisorState({ restarts, lastExit }) {
  const target = supervisorStatePath();
  const temporary = `${target}.tmp`;
  const payload = {
    restarts: Number.isInteger(restarts) && restarts > 0 ? restarts : 0,
    lastExit:
      lastExit && Number.isInteger(lastExit.code) && Number.isFinite(lastExit.at)
        ? { code: lastExit.code, at: lastExit.at }
        : null,
    // Seconds since the epoch, the same unit the core reports its own times in,
    // so a screen reading both does not have to know which one is which.
    updatedAt: Date.now() / 1000,
  };
  try {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(temporary, JSON.stringify(payload), "utf8");
    fs.renameSync(temporary, target);
  } catch (err) {
    log(`could not write the restart record: ${err.message}`);
  }
}

async function launchCore() {
  let launch;
  try {
    launch = coreLaunch({
      mode: MODE,
      resourcesPath: process.resourcesPath,
      configHome: paths.configHome(),
      dataHome: paths.dataHome(),
      logDir: paths.logsDir(),
    });
  } catch (err) {
    if (err instanceof CoreMissingError) throw new StartupFailure("core-missing");
    throw err;
  }

  const child = spawnCore(launch);
  coreChild = child;
  coreStartedAt = Date.now();
  log(`core started pid=${child.pid} starts=${coreAttempts}`);
  writeSupervisorState({ restarts: coreAttempts, lastExit: lastExitRecord });

  const settled = await Promise.race([
    waitForServerRecord(child.pid).then((record) => ({ record })),
    childExited(child).then((code) => ({ code })),
  ]);
  if ("code" in settled) {
    log(`core exited before it wrote an address code=${settled.code}`);
    if (coreChild === child) coreChild = null;
    throw new StartupFailure("core-exited");
  }
  if (!settled.record) throw new StartupFailure("core-timeout");
  if (!(await waitForHealth(healthUrl(settled.record.url), { timeoutMs: 30000 }))) {
    throw new StartupFailure("core-timeout");
  }

  coreUrl = settled.record.url;
  // From here on an exit is a crash rather than a start that did not finish.
  child.once("exit", (code) => {
    void coreExited(child, code);
  });
  if (child.exitCode !== null) void coreExited(child, child.exitCode);
  return coreUrl;
}

/**
 * A core that died on its own is started again, a few times, then not.
 *
 * Each attempt is a new process, so the count lives here. A core that stayed up a
 * minute is treated as having worked and its budget is given back: the crash that
 * follows is a new event, not the same one repeating every second.
 */
async function coreExited(child, code) {
  if (coreChild !== child) return;
  coreChild = null;
  coreUrl = null;

  const exitCode = typeof code === "number" ? code : -1;
  const ranForMs = Date.now() - coreStartedAt;
  const step = nextStep({
    attempts: coreAttempts,
    ranForMs,
    stopping,
  });
  if (step.kind === "ignore") return;

  log(
    `core exited code=${exitCode} ran=${Math.round(ranForMs / 1000)}s ` +
      `starts=${coreAttempts} decision=${step.kind}`,
  );

  if (step.kind === "stop") {
    coreAttempts = step.attempts;
    lastExitRecord = { code: exitCode, at: Date.now() / 1000 };
    writeSupervisorState({ restarts: coreAttempts, lastExit: lastExitRecord });
    log(`core stopped ${MAX_RESTARTS} times in a row; not starting it again`);
    // The page on screen belongs to a core that is gone. Saying so, with the log
    // under it, is more useful than leaving the last render in place.
    await showFailure("core-crashed", startGeneration);
    return;
  }

  coreAttempts = step.attempt;
  lastExitRecord = { code: exitCode, at: Date.now() / 1000 };
  writeSupervisorState({ restarts: coreAttempts, lastExit: lastExitRecord });
  log(`core restart in ${step.delayMs}ms attempt=${step.attempt}`);
  restartTimer = setTimeout(() => {
    restartTimer = null;
    if (stopping) return;
    void startInterface();
  }, step.delayMs);
}

async function showFailure(reason, generation) {
  if (!uiWindow || uiWindow.isDestroyed()) return;
  if (generation !== startGeneration) return;
  const configPath = paths.envFilePath();
  const configMissing = !fs.existsSync(configPath);
  await uiWindow.loadURL(
    failureScreen({
      language: LANGUAGE,
      reason,
      logPath: CORE_LOG,
      logTail: tailLines(CORE_LOG),
      configPath,
      configMissing,
    }),
  );
}

async function startInterface() {
  const win = uiWindow;
  if (!win || win.isDestroyed()) return;
  // A start is somebody asking again, so an exit from here on is a crash again.
  stopping = false;
  const generation = ++startGeneration;
  try {
    await win.loadURL(bootScreen(LANGUAGE));
    const url = await interfaceAddress();
    if (generation !== startGeneration || win.isDestroyed()) return;
    isOwnInterface = makeIsUiOrigin([url]);
    currentAddress = url;
    log(`interface at ${url}`);
    await win.loadURL(url);
  } catch (err) {
    if (generation !== startGeneration || win.isDestroyed()) return;
    const reason = err instanceof StartupFailure ? err.reason : "unknown";
    log(`startup failed reason=${reason} ${err && err.message ? err.message : ""}`);
    await showFailure(reason, generation);
  }
}

function stopCore() {
  stopping = true;
  if (restartTimer) {
    clearTimeout(restartTimer);
    restartTimer = null;
  }
  if (coreChild && coreChild.exitCode === null) {
    try {
      coreChild.kill();
    } catch {
      /* it may already be gone */
    }
  }
  coreChild = null;
  coreUrl = null;
  // A person asking for a reload is a fresh attempt: the crashes before it were
  // one bad run, and the next one starts with its whole budget.
  coreAttempts = 0;
}

function attachUiNavigationGuard(win) {
  win.webContents.setWindowOpenHandler((details) => {
    openInBrowse(details.url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (isOwnInterface(url) || url.startsWith("data:text/html")) return;
    event.preventDefault();
    openInBrowse(url);
  });
  win.webContents.on("did-fail-load", (_event, code, _desc, url, isMainFrame) => {
    if (!isMainFrame || !uiWindow || uiWindow.isDestroyed()) return;
    // -3 is an aborted load: the next address replaced this one on purpose.
    if (code === -3) return;
    // Only the address this attempt is on can fail. A late event from an address
    // that was replaced says nothing about the page on screen now.
    if (!currentAddress || url !== currentAddress) return;
    void showFailure("interface-timeout", startGeneration);
  });
  win.webContents.on("did-finish-load", () => {
    if (!uiWindow || uiWindow.isDestroyed()) return;
    void uiWindow.webContents.setVisualZoomLevelLimits(1, 1);
    uiWindow.webContents.setZoomFactor(1);
  });
}

function buildMenu() {
  const actions = {
    reloadInterface: () => {
      // A reload asks again from the start: the core may have gone away, or the
      // developer may have restarted it.
      stopCore();
      void startInterface();
    },
    openNotes: () => void shell.openPath(paths.dataHome()),
    openSettings: () => void shell.openPath(paths.configHome()),
    openLogs: () => void shell.openPath(paths.logsDir()),
    openBrowse: () => sendToUi("desktop:show-browse"),
  };
  return Menu.buildFromTemplate(menuTemplate(LANGUAGE, actions));
}

function createUiWindow() {
  uiWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 640,
    title: "NOTEAGENT",
    backgroundColor: WINDOW_BG,
    webPreferences: {
      preload: path.join(__dirname, "preload-ui.cjs"),
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      zoomFactor: 1,
    },
  });
  uiWindow.setMenu(buildMenu());
  attachUiNavigationGuard(uiWindow);
  uiWindow.on("closed", () => {
    uiWindow = null;
    if (guestShell) guestShell.dispose();
  });
  void startInterface();
}

function registerIpc() {
  ipcMain.removeHandler("shell:retry");
  ipcMain.handle("shell:retry", (event) => {
    if (!uiWindow || uiWindow.isDestroyed() || event.sender !== uiWindow.webContents) {
      throw new Error(WORDS.refused);
    }
    stopCore();
    void startInterface();
  });

  // Choosing a folder is the shell's job: a page cannot read a directory path
  // from a file input, and the operating system's own picker is the one place a
  // reader expects to be asked. The answer is a path or an empty string for
  // "nothing was chosen", which is not a failure.
  ipcMain.removeHandler("desktop:pick-folder");
  ipcMain.handle("desktop:pick-folder", async (event) => {
    if (!uiWindow || uiWindow.isDestroyed() || event.sender !== uiWindow.webContents) {
      throw new Error(WORDS.refused);
    }
    const answer = await dialog.showOpenDialog(uiWindow, {
      title: WORDS.pickFolder,
      buttonLabel: WORDS.pickFolderButton,
      properties: ["openDirectory", "createDirectory"],
    });
    if (answer.canceled || answer.filePaths.length === 0) return "";
    return answer.filePaths[0];
  });
}

function ensureHomes() {
  for (const dir of [paths.configHome(), paths.logsDir(), paths.dataHome()]) {
    try {
      fs.mkdirSync(dir, { recursive: true });
    } catch (err) {
      log(`could not create ${dir}: ${err.message}`);
    }
  }
  rotateOnStart(SHELL_LOG);
  // A checkout reads the repository's own settings file; only an installed app
  // needs one created for it.
  if (MODE === "prod") {
    ensureSettingsFile({
      target: paths.envFilePath(),
      template: path.join(__dirname, "env-template.txt"),
    });
  }
}

app.setName("NoteAgent");
if (process.platform === "win32") {
  app.disableHardwareAcceleration();
}

const hasLock = app.requestSingleInstanceLock();
if (!hasLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!uiWindow || uiWindow.isDestroyed()) return;
    if (uiWindow.isMinimized()) uiWindow.restore();
    uiWindow.show();
    uiWindow.focus();
  });

  app.whenReady().then(() => {
    ensureHomes();
    log(`mode=${MODE} language=${LANGUAGE}`);

    const partitioned = guestSession();
    partitioned.setPermissionRequestHandler((_wc, _permission, callback) => {
      callback(false);
    });
    guestShell = createGuestShell({
      snapshot: SNAPSHOT,
      sendToUi,
      guestSession,
      language: LANGUAGE,
      isTrustedSender: (event) =>
        Boolean(
          uiWindow && !uiWindow.isDestroyed() && event.sender === uiWindow.webContents,
        ),
    });
    app.on("web-contents-created", (_event, contents) => {
      if (contents.session === partitioned) {
        guestShell.guardGuestContents(contents);
      }
    });
    registerIpc();
    createUiWindow();
    guestShell.attachHost(uiWindow);
  });
}

app.on("window-all-closed", () => {
  app.quit();
});

// The core shares the app's life. Saying so before the window goes away means its
// exit is read as this app ending, not as a crash worth starting it again for.
app.on("before-quit", () => {
  stopCore();
});
