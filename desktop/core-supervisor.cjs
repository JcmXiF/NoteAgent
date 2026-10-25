"use strict";

// When a core that died is started again, and when the app stops trying.
//
// A crash is usually something one restart does not fix, and a loop of restarts
// fills the log with the same traceback while the window keeps going back to the
// start screen. So starts are counted, and a core that stayed up long enough is
// treated as having worked: its budget is given back, because the crash that
// follows is a new event rather than the same one repeating.
//
// Nothing here starts anything. How to start, what to show and when to give up
// are the shell's decisions; this module only answers "again, after how long",
// which is the part worth testing without a process.

const MAX_RESTARTS = 3;

//: A core that ran this long was working, even if it died later.
const STABLE_MS = 60_000;

//: Waits between attempts. Growing, so a transient crash is back in a second and
//: a core that keeps failing is not on screen as a flicker.
const BACKOFF_MS = [1_000, 3_000, 8_000];

/**
 * How long to wait before starting again, for the given attempt (1-based).
 *
 * An unknown attempt gets the shortest wait: being too eager once is better than
 * leaving a window with nothing behind it.
 */
function restartDelay(attempt) {
  if (!Number.isFinite(attempt) || attempt < 1) return BACKOFF_MS[0];
  const index = Math.min(Math.floor(attempt) - 1, BACKOFF_MS.length - 1);
  return BACKOFF_MS[index];
}

/**
 * The budget left after a core run ended.
 *
 * A run shorter than `stableMs` keeps the count, which is what makes repeated
 * quick crashes add up to "stop". A longer run resets it.
 */
function restartBudget({ attempts, ranForMs, stableMs = STABLE_MS }) {
  const used = Number.isFinite(attempts) && attempts > 0 ? Math.floor(attempts) : 0;
  if (Number.isFinite(ranForMs) && ranForMs >= stableMs) return 0;
  return used;
}

/**
 * What the shell should do after the core exited.
 *
 * - `ignore`: the app is quitting or restarting it on purpose.
 * - `restart`: try again after `delayMs`, as attempt `attempt`.
 * - `stop`: the budget is spent; ask the reader to start the app again.
 */
function nextStep({
  attempts,
  ranForMs,
  stopping = false,
  maxRestarts = MAX_RESTARTS,
  stableMs = STABLE_MS,
}) {
  if (stopping) return { kind: "ignore" };
  const used = restartBudget({ attempts, ranForMs, stableMs });
  if (used >= maxRestarts) return { kind: "stop", attempts: used };
  const attempt = used + 1;
  return { kind: "restart", attempt, delayMs: restartDelay(attempt) };
}

module.exports = {
  BACKOFF_MS,
  MAX_RESTARTS,
  STABLE_MS,
  nextStep,
  restartBudget,
  restartDelay,
};
