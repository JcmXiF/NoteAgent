"use strict";

// When the shell looks for a new version, kept away from Electron.
//
// The schedule has two numbers: one check shortly after the app comes up, then
// the same check at a long interval. A checkout never runs it, because a
// developer's build has no release to compare against and would be told about
// versions it cannot install over.
//
// Only the timer is injected. The check itself and what to do with its answer are
// handed in, so a fake clock can exercise the whole schedule without a window and
// without waiting six hours.

const STARTUP_DELAY_MS = 30 * 1000;
const INTERVAL_MS = 6 * 60 * 60 * 1000;

function createVersionScheduler({
  isPackaged,
  check,
  onAvailable,
  onError,
  timers = { setTimeout, clearTimeout },
  startupDelayMs = STARTUP_DELAY_MS,
  intervalMs = INTERVAL_MS,
}) {
  let started = false;
  let timer = null;
  // A check is one request, so there is one slot. A timer that fires while the
  // last check is still waiting does not add a second one: the answer would be
  // about the same release and would arrive twice.
  let inFlight = null;

  function startCheck() {
    if (inFlight) return null;
    const run = (async () => {
      try {
        return await check();
      } catch (err) {
        // The checker turns its own failures into results. Reaching here means
        // the check itself broke, which is still a failed check to report; the
        // message goes along because the log is the only place it can be read.
        return {
          kind: "error",
          reason: "unexpected",
          message: err && err.message ? err.message : String(err),
        };
      }
    })();
    inFlight = run;
    void run.then(() => {
      if (inFlight === run) inFlight = null;
    });
    return run;
  }

  async function runAutomatically() {
    const run = startCheck();
    if (!run) return;
    const result = await run;
    try {
      if (result.kind === "available") {
        await onAvailable(result);
      } else if (result.kind === "error") {
        await onError(result);
      }
    } catch {
      // A notification that cannot be shown is not a reason to abandon the
      // schedule; the next check is already set and may well succeed.
    }
  }

  function schedule(delayMs) {
    timer = timers.setTimeout(() => {
      timer = null;
      void runAutomatically();
      if (started) schedule(intervalMs);
    }, delayMs);
  }

  return {
    start() {
      if (started || !isPackaged) return;
      started = true;
      schedule(startupDelayMs);
    },
    stop() {
      started = false;
      if (timer !== null) {
        timers.clearTimeout(timer);
        timer = null;
      }
    },
    // A manual check takes the same slot as an automatic one. It is available in
    // a checkout too, which is the only way to try the path during development.
    async checkNow() {
      const run = startCheck();
      if (!run) return null;
      return await run;
    },
  };
}

module.exports = { createVersionScheduler };
