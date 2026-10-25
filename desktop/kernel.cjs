"use strict";

// How the shell finds a running core, and how it waits for one to be ready.
//
// The core writes down the port it really took, because it is the only process
// that can know: it binds the socket itself, and it may have had to take a free
// port when the preferred one was busy. The shell reads that record and nothing
// else, so it cannot be talked into loading an address that is not the core's.
//
// Records are one file per core process under the temp dir, named after the
// process id (see the Python side, `api/serve_control.py`). Two cores running
// at once therefore never shadow each other.

const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const RECORDS_DIR = path.join(os.tmpdir(), "noteagent-serve");
const HEALTH_PATH = "/api/health";

function recordPath(pid, dir = RECORDS_DIR) {
  return path.join(dir, `${pid}.json`);
}

function urlFor(host, port) {
  const name = String(host).includes(":") ? `[${host}]` : host;
  return `http://${name}:${port}`;
}

function readServerRecord(pid, dir = RECORDS_DIR) {
  let payload;
  try {
    payload = JSON.parse(fs.readFileSync(recordPath(pid, dir), "utf8"));
  } catch {
    return null;
  }
  if (!payload || typeof payload !== "object") return null;
  const port = Number(payload.port);
  const host = typeof payload.host === "string" ? payload.host : "";
  if (!host || !Number.isInteger(port) || port <= 0 || port > 65535) return null;
  return {
    pid: Number.isInteger(Number(payload.pid)) ? Number(payload.pid) : pid,
    host,
    port,
    url: urlFor(host, port),
  };
}

function healthUrl(baseUrl) {
  return `${String(baseUrl).replace(/\/+$/, "")}${HEALTH_PATH}`;
}

function probeHealth(url, timeoutMs = 800) {
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
        done(res.statusCode === 200);
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

// Resolves true as soon as the core answers for itself, false if it never does.
// A core that is starting is not a failure, which is why this waits rather than
// probing once: what it waits for is the core's own readiness, not the presence
// of something on a port.
async function waitForHealth(url, { timeoutMs = 30000, intervalMs = 300 } = {}) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (await probeHealth(url)) return true;
    if (Date.now() >= deadline) return false;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

// The record exists only after the core has bound its port, so its presence is
// the earliest moment the address is worth asking about.
async function waitForServerRecord(
  pid,
  {
    dir = RECORDS_DIR,
    timeoutMs = 30000,
    intervalMs = 100,
    read = readServerRecord,
    now = Date.now,
  } = {},
) {
  const deadline = now() + timeoutMs;
  for (;;) {
    const record = read(pid, dir);
    if (record) return record;
    if (now() >= deadline) return null;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

module.exports = {
  healthUrl,
  readServerRecord,
  recordPath,
  urlFor,
  waitForHealth,
  waitForServerRecord,
};
