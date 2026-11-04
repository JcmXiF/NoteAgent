"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { checkForUpdates } = require("../version-checker.cjs");

const RELEASE_URL = "https://api.github.com/repos/JcmXiF/NoteAgent/releases/latest";
const DOWNLOAD_URL = "https://github.com/JcmXiF/NoteAgent/releases/tag/v0.2.0";

function response(data, status = 200, { json } = {}) {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: json ?? (async () => data),
  };
}

function release(overrides = {}) {
  return {
    tag_name: "v0.2.0",
    body: "Faster startup and fewer crashes.",
    html_url: DOWNLOAD_URL,
    ...overrides,
  };
}

test("a newer release is offered with its notes and download page", async () => {
  const calls = [];
  const fetch = async (url, options) => {
    calls.push({ url, options });
    return response(release());
  };

  const result = await checkForUpdates({ fetch, appVersion: "0.1.0" });

  assert.equal(result.kind, "available");
  assert.equal(result.version, "0.2.0");
  assert.equal(result.currentVersion, "0.1.0");
  assert.equal(result.notes, "Faster startup and fewer crashes.");
  assert.equal(result.url, DOWNLOAD_URL);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, RELEASE_URL);
  assert.equal(calls[0].options.headers["User-Agent"], "NoteAgent/0.1.0");
  assert.equal(calls[0].options.headers.Accept, "application/vnd.github+json");
});

test("the same version is not an update", async () => {
  const fetch = async () => response(release({ tag_name: "v0.1.0" }));

  const result = await checkForUpdates({ fetch, appVersion: "0.1.0" });

  assert.equal(result.kind, "none");
  assert.equal(result.currentVersion, "0.1.0");
});

test("an older release is not an update", async () => {
  const fetch = async () => response(release({ tag_name: "v0.0.9" }));

  const result = await checkForUpdates({ fetch, appVersion: "0.1.0" });

  assert.equal(result.kind, "none");
});

test("versions compare as numbers, not as text", async () => {
  const fetch = async () => response(release({ tag_name: "v0.10.0" }));

  const result = await checkForUpdates({ fetch, appVersion: "0.9.0" });

  assert.equal(result.kind, "available");
  assert.equal(result.version, "0.10.0");
});

test("a repository without a release has nothing to offer", async () => {
  const fetch = async () =>
    response({ message: "Not Found" }, 404, {
      json: async () => {
        throw new Error("404 has no release document");
      },
    });

  const result = await checkForUpdates({ fetch, appVersion: "0.1.0" });

  assert.equal(result.kind, "none");
  assert.equal(result.currentVersion, "0.1.0");
});

function logCollector() {
  const lines = [];
  return { lines, logger: (line) => lines.push(line) };
}

test("GitHub's rate limit is reported as a failure and logged", async () => {
  for (const status of [403, 429]) {
    const log = logCollector();
    const fetch = async () => response({ message: "API rate limit exceeded" }, status);

    const result = await checkForUpdates({
      fetch,
      appVersion: "0.1.0",
      logger: log.logger,
    });

    assert.equal(result.kind, "error");
    assert.equal(result.reason, "rate-limited");
    assert.ok(log.lines.some((line) => line.includes("rate-limited")));
  }
});

test("an unexpected API status is a failure and is logged", async () => {
  const log = logCollector();
  const fetch = async () => response({ message: "server error" }, 500);

  const result = await checkForUpdates({
    fetch,
    appVersion: "0.1.0",
    logger: log.logger,
  });

  assert.equal(result.kind, "error");
  assert.equal(result.reason, "bad-response");
  assert.ok(log.lines.some((line) => line.includes("bad-response")));
});

test("a body that is not JSON is a failure and is logged", async () => {
  const log = logCollector();
  const fetch = async () =>
    response(null, 200, {
      json: async () => {
        throw new SyntaxError("Unexpected token <");
      },
    });

  const result = await checkForUpdates({
    fetch,
    appVersion: "0.1.0",
    logger: log.logger,
  });

  assert.equal(result.kind, "error");
  assert.equal(result.reason, "bad-response");
  assert.ok(log.lines.some((line) => line.includes("bad-response")));
});

test("a network error is a failure and is logged", async () => {
  const log = logCollector();
  const fetch = async () => {
    throw new Error("getaddrinfo ENOTFOUND api.github.com");
  };

  const result = await checkForUpdates({
    fetch,
    appVersion: "0.1.0",
    logger: log.logger,
  });

  assert.equal(result.kind, "error");
  assert.equal(result.reason, "network");
  assert.ok(log.lines.some((line) => line.includes("network")));
});

test("an unusable running version is a failure and is logged", async () => {
  const log = logCollector();
  const fetch = async () => {
    throw new Error("the request should not be made");
  };

  const result = await checkForUpdates({
    fetch,
    appVersion: "development",
    logger: log.logger,
  });

  assert.equal(result.kind, "error");
  assert.equal(result.reason, "bad-current-version");
  assert.ok(log.lines.some((line) => line.includes("bad-current-version")));
});

test("a tag that is not v<semver> is a failure and is logged", async () => {
  for (const tag_name of ["0.2.0", "v0.2", "v0.2.0.1", "release-0.2.0", "v01.2.3", undefined]) {
    const log = logCollector();
    const fetch = async () => response(release({ tag_name }));

    const result = await checkForUpdates({
      fetch,
      appVersion: "0.1.0",
      logger: log.logger,
    });

    assert.equal(result.kind, "error", `tag ${String(tag_name)} was accepted`);
    assert.equal(result.reason, "bad-tag");
    assert.ok(log.lines.some((line) => line.includes("bad-tag")));
  }
});

test("a release without a usable page is a failure and is logged", async () => {
  for (const html_url of [undefined, "", "not a url", "ftp://github.com/NoteAgent"]) {
    const log = logCollector();
    const fetch = async () => response(release({ html_url }));

    const result = await checkForUpdates({
      fetch,
      appVersion: "0.1.0",
      logger: log.logger,
    });

    assert.equal(result.kind, "error", `page ${String(html_url)} was accepted`);
    assert.equal(result.reason, "bad-response");
    assert.ok(log.lines.some((line) => line.includes("bad-response")));
  }
});

test("a request that never answers gives up and is logged", async () => {
  const log = logCollector();
  let signal;
  const fetch = (_url, options) => {
    signal = options.signal;
    return new Promise(() => {});
  };

  const result = await checkForUpdates({
    fetch,
    appVersion: "0.1.0",
    logger: log.logger,
    timeoutMs: 10,
  });

  assert.equal(result.kind, "error");
  assert.equal(result.reason, "timeout");
  assert.ok(log.lines.some((line) => line.includes("timeout")));
  assert.equal(signal.aborted, true);
});

test("the check waits ten seconds before giving up", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const fetch = () => new Promise(() => {});

  const pending = checkForUpdates({ fetch, appVersion: "0.1.0" });
  t.mock.timers.tick(9999);
  t.mock.timers.tick(1);

  const result = await pending;
  assert.equal(result.kind, "error");
  assert.equal(result.reason, "timeout");
});

test("a response whose body stalls after the headers still times out", async () => {
  const log = logCollector();
  let signal;
  const fetch = (_url, options) => {
    signal = options.signal;
    return Promise.resolve({
      status: 200,
      ok: true,
      json: () => new Promise(() => {}),
    });
  };

  const result = await checkForUpdates({
    fetch,
    appVersion: "0.1.0",
    logger: log.logger,
    timeoutMs: 10,
  });

  assert.equal(result.kind, "error");
  assert.equal(result.reason, "timeout");
  assert.ok(log.lines.some((line) => line.includes("timeout")));
  assert.equal(signal.aborted, true);
});
