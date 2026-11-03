"use strict";

// Whether a newer release exists, decided here rather than in the window.
//
// Every answer the GitHub API can give has to become one of three results: a
// version to offer, no version to offer, or a failure with a short reason. The
// shell supplies `fetch` and its log, and Electron is not imported, so the
// comparison and every failure path can be exercised without a window.

const RELEASES_LATEST =
  "https://api.github.com/repos/JcmXiF/NoteAgent/releases/latest";
const TIMEOUT_MS = 10000;

// Three numbers and nothing else. This release line has no beta channel, and
// GitHub's `releases/latest` never returns a draft or a prerelease, so a tag
// that needs more than this is either a mistake or a different kind of release.
const VERSION = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;

function parseVersion(text) {
  if (typeof text !== "string") return null;
  const match = VERSION.exec(text.startsWith("v") ? text.slice(1) : text);
  if (!match) return null;
  return [Number(match[1]), Number(match[2]), Number(match[3])];
}

function isNewer(latest, current) {
  for (let index = 0; index < 3; index += 1) {
    if (latest[index] !== current[index]) return latest[index] > current[index];
  }
  return false;
}

function none(currentVersion) {
  return { kind: "none", currentVersion };
}

/**
 * Ask GitHub for the newest release and compare it with this app.
 *
 * The result is one of:
 *   { kind: "available", version, currentVersion, notes, url }
 *   { kind: "none", currentVersion }
 *   { kind: "error", reason }
 * A reason never reaches the window as text; the notifier decides what to say.
 */
async function checkForUpdates({
  fetch,
  appVersion,
  logger = () => {},
  timeoutMs = TIMEOUT_MS,
}) {
  const current = parseVersion(appVersion);
  if (!current) {
    logger(`update check failed reason=bad-current-version version=${appVersion}`);
    return { kind: "error", reason: "bad-current-version" };
  }

  // A check that hangs must end by itself: nobody is watching a menu click, and
  // a request left open would hold a slot in the rate limit for no answer. The
  // timeout stays armed until the body has been read: a server can send its
  // headers and stall, and clearing the timer at the headers would let that
  // response hold the check open forever.
  const controller = new AbortController();
  let timer = null;
  let timedOut = false;
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
      resolve({ ended: "timeout" });
    }, timeoutMs);
  });

  try {
    let answer;
    try {
      const settled = await Promise.race([
        fetch(RELEASES_LATEST, {
          headers: {
            "User-Agent": `NoteAgent/${appVersion}`,
            Accept: "application/vnd.github+json",
          },
          signal: controller.signal,
        }).then((value) => ({ answer: value })),
        timeout,
      ]);
      if (settled.ended === "timeout") {
        logger(`update check failed reason=timeout after=${timeoutMs}ms`);
        return { kind: "error", reason: "timeout" };
      }
      answer = settled.answer;
    } catch (err) {
      // An abort makes the real fetch reject too, so the flag, not the error
      // type, decides whether this was a timeout or the network.
      if (timedOut) {
        logger(`update check failed reason=timeout after=${timeoutMs}ms`);
        return { kind: "error", reason: "timeout" };
      }
      logger(`update check failed reason=network ${err && err.message ? err.message : err}`);
      return { kind: "error", reason: "network" };
    }

    // The repository exists but has published nothing yet. That is a normal
    // answer, not a failure: there is simply no version to compare against.
    if (answer && answer.status === 404) return none(appVersion);

    // Unauthenticated GitHub asks are rate limited by address. There is no token
    // to add and no retry to wait for, so the caller only needs to know it was
    // refused rather than wrong.
    if (answer && (answer.status === 403 || answer.status === 429)) {
      logger(`update check failed reason=rate-limited status=${answer.status}`);
      return { kind: "error", reason: "rate-limited" };
    }

    const status = answer && Number.isInteger(answer.status) ? answer.status : 0;
    const ok = answer && typeof answer.ok === "boolean" ? answer.ok : status >= 200 && status < 300;
    if (!answer || !ok) {
      logger(`update check failed reason=bad-response status=${status}`);
      return { kind: "error", reason: "bad-response" };
    }

    let release;
    try {
      const settled = await Promise.race([
        Promise.resolve()
          .then(() => answer.json())
          .then((value) => ({ release: value })),
        timeout,
      ]);
      if (settled.ended === "timeout") {
        logger(`update check failed reason=timeout after=${timeoutMs}ms`);
        return { kind: "error", reason: "timeout" };
      }
      release = settled.release;
    } catch (err) {
      // The abort also rejects the body read, so the flag decides again.
      if (timedOut) {
        logger(`update check failed reason=timeout after=${timeoutMs}ms`);
        return { kind: "error", reason: "timeout" };
      }
      logger(`update check failed reason=bad-response ${err && err.message ? err.message : err}`);
      return { kind: "error", reason: "bad-response" };
    }

    const tag = release && release.tag_name;
    const latest =
      typeof tag === "string" && tag.startsWith("v") ? parseVersion(tag) : null;
    if (!latest) {
      logger(`update check failed reason=bad-tag tag=${tag}`);
      return { kind: "error", reason: "bad-tag" };
    }
    const url = release.html_url;
    if (typeof url !== "string" || !/^https?:\/\//i.test(url)) {
      logger(`update check failed reason=bad-response url=${url}`);
      return { kind: "error", reason: "bad-response" };
    }

    if (!isNewer(latest, current)) return none(appVersion);

    return {
      kind: "available",
      version: tag.slice(1),
      currentVersion: appVersion,
      notes: typeof release.body === "string" ? release.body : "",
      url,
    };
  } finally {
    clearTimeout(timer);
  }
}

module.exports = { checkForUpdates, parseVersion };
