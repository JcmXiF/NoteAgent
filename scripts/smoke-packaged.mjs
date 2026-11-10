// Smoke the core that ships in the installer.
//
// This starts the packaged interpreter, not the Electron shell. The core is the
// part that serves the interface and answers the API; the shell only launches
// it, and a smoke that needs a desktop session would prove less about what
// actually travels in the installer.
//
// Four things are checked, in the order a person meets them:
//
//   1. the packaged core starts and writes the address it really took;
//   2. a project that upgrades from the old chat files has its conversations
//      before either list route is asked (the lifespan migrates them);
//   3. `/api/v1/health` reports the app's version and `/` serves the built
//      interface;
//   4. one chat + wait flow runs against the packaged source with a scripted
//      model, so no provider, key or network is involved.
//
// A failed run keeps everything under build/smoke-packaged (the project, the
// session events, the core's output), which is what CI uploads.
//
// Usage: node scripts/smoke-packaged.mjs [--core <dir>] [--keep]

import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import fs from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { readDesktopVersion } from "./version.mjs";

const require = createRequire(import.meta.url);
const {
  healthUrl,
  recordPath,
  waitForHealth,
  waitForServerRecord,
} = require("../desktop/kernel.cjs");

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
const keep = args.includes("--keep");
const coreIndex = args.indexOf("--core");
const coreRoot =
  coreIndex >= 0 && args[coreIndex + 1]
    ? path.resolve(args[coreIndex + 1])
    : path.join(repoRoot, "build", "runtime", "core");

const PYTHON = path.join(coreRoot, "python", "python.exe");
const CHAT_DRIVER = path.join(repoRoot, "scripts", "smoke_packaged_chat.py");
const VERSION = readDesktopVersion(path.join(repoRoot, "desktop", "package.json"));

function fail(message) {
  throw new Error(message);
}

function request(url, timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        body += chunk;
      });
      res.on("end", () => resolve({ statusCode: res.statusCode || 0, body }));
    });
    req.on("error", reject);
    req.on("timeout", () => {
      req.destroy(new Error(`timed out waiting for ${url}`));
    });
  });
}

async function json(url) {
  const response = await request(url);
  if (response.statusCode !== 200) {
    fail(`${url} answered ${response.statusCode}: ${response.body.slice(0, 400)}`);
  }
  try {
    return JSON.parse(response.body);
  } catch (err) {
    fail(`${url} did not answer JSON: ${err.message}`);
  }
}

function prepareProject(root) {
  fs.mkdirSync(path.join(root, "raw"), { recursive: true });
  fs.mkdirSync(path.join(root, "wiki"), { recursive: true });
  fs.mkdirSync(path.join(root, ".noteagent"), { recursive: true });
  return root;
}

function seedOldChat(project) {
  const chats = path.join(project, ".noteagent", "chats");
  fs.mkdirSync(chats, { recursive: true });
  fs.writeFileSync(
    path.join(chats, "c-1.json"),
    JSON.stringify(
      {
        version: 1,
        id: "c-1",
        title: "Upgrade smoke",
        updatedAt: "2026-11-09T10:00:02Z",
        messages: [
          {
            id: "m1",
            role: "user",
            content: "旧问题",
            createdAt: "2026-11-09T10:00:01Z",
          },
          {
            id: "m2",
            role: "assistant",
            content: "旧回答",
            createdAt: "2026-11-09T10:00:02Z",
          },
        ],
      },
      null,
      2,
    ),
    "utf8",
  );
}

function environment({ config, home, project, logs }) {
  return {
    ...process.env,
    NOTEAGENT_CONFIG_DIR: config,
    NOTEAGENT_HOME: home,
    NOTEAGENT_PROJECT: project,
    NOTEAGENT_SUPERVISED: "1",
    NOTEAGENT_LOG_DIR: logs,
    // The packaged runtime describes its own import path; an inherited value
    // must not be able to change it.
    PYTHONPATH: "",
    PYTHONIOENCODING: "utf-8",
  };
}

async function stopCore(child) {
  if (!child || child.exitCode !== null) return;
  // The listener goes on before the signal, so an exit that lands between the
  // check above and the kill is not missed.
  const exited = once(child, "exit");
  child.kill();
  await Promise.race([
    exited,
    // Do not hold the process open for the full wait after a quick exit.
    new Promise((resolve) => setTimeout(resolve, 5000).unref()),
  ]);
}

async function main() {
  if (!fs.existsSync(PYTHON)) {
    fail(
      `no packaged runtime at ${PYTHON}. Build it first: node scripts/package.mjs --dir`,
    );
  }
  if (!fs.existsSync(path.join(coreRoot, "src", "noteagent", "web", "index.html"))) {
    fail(`no built interface inside ${coreRoot}; package the app first`);
  }

  const temp = path.join(repoRoot, "build", "smoke-packaged");
  fs.rmSync(temp, { recursive: true, force: true });
  fs.mkdirSync(temp, { recursive: true });
  const config = path.join(temp, "config");
  const dataHome = path.join(temp, "home");
  const project = prepareProject(path.join(temp, "project"));
  const logs = path.join(temp, "logs");
  fs.mkdirSync(config, { recursive: true });
  fs.mkdirSync(dataHome, { recursive: true });
  fs.mkdirSync(logs, { recursive: true });
  seedOldChat(project);

  let child = null;
  let output = "";
  let passed = false;
  try {
    child = spawn(
      PYTHON,
      ["-m", "noteagent.api", "--port", "0", "--fallback-port"],
      {
        cwd: config,
        env: environment({ config, home: dataHome, project, logs }),
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
      },
    );
    child.stdout?.on("data", (chunk) => {
      output += chunk;
    });
    child.stderr?.on("data", (chunk) => {
      output += chunk;
    });
    // A stale record can share a reused pid; the core writes its own in a moment.
    fs.rmSync(recordPath(child.pid), { force: true });

    const settled = await Promise.race([
      waitForServerRecord(child.pid, { timeoutMs: 30000 }).then((record) => ({
        record,
      })),
      once(child, "exit").then(([code]) => ({ code })),
    ]);
    if ("code" in settled) {
      fail(`the core exited before it wrote an address (code ${settled.code})`);
    }
    if (!settled.record) fail("the core did not write its address in time");
    const base = settled.record.url;
    if (!(await waitForHealth(healthUrl(base), { timeoutMs: 30000 }))) {
      fail(`the core did not answer ${healthUrl(base)}`);
    }
    console.log(`core ${base}`);

    const migrated = path.join(project, ".noteagent", "sessions", "c-1.jsonl");
    if (!fs.existsSync(migrated)) {
      fail("the old chat was not migrated when the core started");
    }
    const listed = await json(`${base}/api/v1/sessions`);
    if (!(listed.sessions || []).some((item) => item.id === "c-1")) {
      fail(`the migrated session is not listed: ${JSON.stringify(listed)}`);
    }
    console.log("upgrade migration ok");

    const health = await json(`${base}/api/v1/health`);
    if (health.status !== "ok") {
      fail(`health is not ok: ${JSON.stringify(health)}`);
    }
    if (health.version !== VERSION) {
      fail(`the core reports ${health.version}, the app is ${VERSION}`);
    }
    console.log(`health ok ${health.version}`);

    const page = await request(`${base}/`);
    if (page.statusCode !== 200) {
      fail(`the interface answered ${page.statusCode}: ${page.body.slice(0, 400)}`);
    }
    if (!page.body.includes('<div id="root">')) {
      fail("the interface page does not look like the built UI");
    }
    console.log("interface ok");

    const chatHome = path.join(temp, "chat-home");
    const chatProject = path.join(temp, "chat-project");
    const chatConfig = path.join(temp, "chat-config");
    fs.mkdirSync(chatConfig, { recursive: true });
    const chat = spawnSync(
      PYTHON,
      [CHAT_DRIVER, "--home", chatHome, "--project", chatProject],
      {
        cwd: repoRoot,
        env: environment({
          config: chatConfig,
          home: chatHome,
          project: chatProject,
          logs: path.join(temp, "chat-logs"),
        }),
        encoding: "utf8",
        timeout: 120000,
      },
    );
    try {
      fs.writeFileSync(
        path.join(temp, "chat.log"),
        `${chat.stdout || ""}${chat.stderr || ""}`,
        "utf8",
      );
    } catch (err) {
      // The failure below is the one worth reporting; a log that cannot be
      // written must not replace it.
      console.error(`could not write chat.log: ${err.message}`);
    }
    if (chat.error) fail(`the chat smoke did not run: ${chat.error.message}`);
    if (chat.status !== 0) {
      fail(`the chat smoke failed\n${chat.stdout || ""}\n${chat.stderr || ""}`);
    }
    // The driver ends with its own marker; this script prints the one above.
    const chatLines = (chat.stdout || "")
      .split("\n")
      .filter((line) => line.trim() !== "" && line.trim() !== "SMOKE OK");
    if (chatLines.length > 0) console.log(chatLines.join("\n"));

    passed = true;
  } finally {
    await stopCore(child);
    if (child?.pid) fs.rmSync(recordPath(child.pid), { force: true });
    if (passed && !keep) {
      fs.rmSync(temp, { recursive: true, force: true });
    } else {
      try {
        fs.writeFileSync(path.join(temp, "core.log"), output, "utf8");
      } catch (err) {
        console.error(`could not write core.log: ${err.message}`);
      }
      console.error(`smoke files kept at ${temp}`);
      if (output.trim()) console.error(output.trim());
    }
  }
  console.log("SMOKE OK");
}

try {
  await main();
} catch (err) {
  console.error(err.message);
  process.exit(1);
}
