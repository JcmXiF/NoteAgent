"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const {
  healthUrl,
  readServerRecord,
  recordPath,
  urlFor,
  waitForHealth,
  waitForServerRecord,
} = require("../kernel.cjs");

function tempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "noteagent-kernel-test-"));
}

function writeRecord(dir, pid, payload) {
  fs.writeFileSync(path.join(dir, `${pid}.json`), JSON.stringify(payload), "utf8");
}

function listen(handler) {
  return new Promise((resolve) => {
    const server = http.createServer(handler);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({
        server,
        url: `http://127.0.0.1:${port}`,
        close: () => new Promise((done) => server.close(done)),
      });
    });
  });
}

test("a record says which port the core actually took", () => {
  const dir = tempDir();
  writeRecord(dir, 4242, { pid: 4242, host: "127.0.0.1", port: 8791, requested_port: 8765 });

  const record = readServerRecord(4242, dir);

  assert.deepEqual(record, { pid: 4242, host: "127.0.0.1", port: 8791, url: "http://127.0.0.1:8791" });
});

test("a record that is not there yet is not an error", () => {
  assert.equal(readServerRecord(999999, tempDir()), null);
});

test("a half-written record is not mistaken for a running core", () => {
  const dir = tempDir();
  fs.writeFileSync(path.join(dir, "77.json"), '{"port": 879', "utf8");

  assert.equal(readServerRecord(77, dir), null);
});

test("a record without a usable address is ignored", () => {
  const dir = tempDir();
  writeRecord(dir, 78, { pid: 78, host: "127.0.0.1" });
  writeRecord(dir, 79, { pid: 79, host: "", port: 8791 });
  writeRecord(dir, 80, { pid: 80, host: "127.0.0.1", port: 70000 });

  assert.equal(readServerRecord(78, dir), null);
  assert.equal(readServerRecord(79, dir), null);
  assert.equal(readServerRecord(80, dir), null);
});

test("records live under one directory named after the process", () => {
  assert.equal(recordPath(4242, "/tmp/records"), path.join("/tmp/records", "4242.json"));
});

test("an IPv6 address is bracketed in the url", () => {
  assert.equal(urlFor("::1", 8765), "http://[::1]:8765");
  assert.equal(urlFor("127.0.0.1", 8765), "http://127.0.0.1:8765");
});

test("health is asked for at the core's own endpoint", () => {
  assert.equal(healthUrl("http://127.0.0.1:8791"), "http://127.0.0.1:8791/api/health");
  assert.equal(healthUrl("http://127.0.0.1:8791/"), "http://127.0.0.1:8791/api/health");
});

test("a core that answers for itself is ready", async () => {
  const core = await listen((_req, res) => {
    res.writeHead(200, { "content-type": "application/json" });
    res.end('{"status":"ok"}');
  });
  try {
    assert.equal(await waitForHealth(core.url, { timeoutMs: 2000 }), true);
  } finally {
    await core.close();
  }
});

test("any other answer is not readiness", async () => {
  // Readiness is the core answering for itself; a 500 is still not readiness.
  const core = await listen((_req, res) => {
    res.writeHead(500);
    res.end("not yet");
  });
  try {
    assert.equal(await waitForHealth(core.url, { timeoutMs: 200 }), false);
  } finally {
    await core.close();
  }
});

test("nothing listening means not ready, and the wait gives up", async () => {
  const port = await freePort();
  assert.equal(await waitForHealth(`http://127.0.0.1:${port}`, { timeoutMs: 200 }), false);
});

test("waiting waits: a core that comes up late is still found", async () => {
  const port = await freePort();
  const server = http.createServer((_req, res) => {
    res.writeHead(200);
    res.end("{}");
  });
  const timer = setTimeout(() => server.listen(port, "127.0.0.1"), 150);
  try {
    const ready = await waitForHealth(`http://127.0.0.1:${port}`, {
      timeoutMs: 2000,
      intervalMs: 20,
    });
    assert.equal(ready, true);
  } finally {
    clearTimeout(timer);
    await new Promise((done) => server.close(() => done()));
  }
});

test("a record that appears late is found by pid", async () => {
  const dir = tempDir();
  const seen = [];
  const read = (pid) => {
    seen.push(pid);
    if (seen.length < 3) return null;
    return readServerRecord(4243, dir);
  };
  writeRecord(dir, 4243, { pid: 4243, host: "127.0.0.1", port: 8792 });

  const record = await waitForServerRecord(4243, {
    dir,
    intervalMs: 5,
    timeoutMs: 1000,
    read,
  });

  assert.equal(record.port, 8792);
  assert.equal(seen.length, 3);
});

test("a record that never appears gives up instead of hanging", async () => {
  let t = 0;
  const record = await waitForServerRecord(4244, {
    intervalMs: 1,
    timeoutMs: 100,
    read: () => null,
    now: () => (t += 60),
  });

  assert.equal(record, null);
});

function freePort() {
  return new Promise((resolve) => {
    const server = http.createServer();
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}
