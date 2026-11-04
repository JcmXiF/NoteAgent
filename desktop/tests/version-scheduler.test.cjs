"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { createVersionScheduler } = require("../version-scheduler.cjs");

const SIX_HOURS = 6 * 60 * 60 * 1000;

// The scheduler only ever asks for a couple of timers, so a map and a clock are
// enough. Advancing runs due callbacks in order, the way a real clock would.
function fakeTimers() {
  let now = 0;
  let nextId = 1;
  const timers = new Map();
  return {
    setTimeout(callback, delay) {
      const id = nextId;
      nextId += 1;
      timers.set(id, { at: now + delay, callback });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    advance(ms) {
      const target = now + ms;
      for (;;) {
        let due = null;
        for (const [id, timer] of timers) {
          if (timer.at <= target && (!due || timer.at < due.timer.at)) {
            due = { id, timer };
          }
        }
        if (!due) break;
        timers.delete(due.id);
        now = due.timer.at;
        due.timer.callback();
      }
      now = target;
    },
  };
}

// Callbacks are async, so their continuations run after the timer returns.
function settle() {
  return new Promise((resolve) => setImmediate(resolve));
}

function collector() {
  const calls = [];
  return {
    calls,
    check: async () => {
      calls.push("check");
      return { kind: "none", currentVersion: "0.1.0" };
    },
  };
}

test("a packaged app checks half a minute after start, then every six hours", async () => {
  const timers = fakeTimers();
  const { calls, check } = collector();
  const scheduler = createVersionScheduler({
    isPackaged: true,
    timers,
    check,
    onAvailable: () => {},
    onError: () => {},
  });

  scheduler.start();
  assert.deepEqual(calls, []);

  timers.advance(29_999);
  await settle();
  assert.deepEqual(calls, []);

  timers.advance(1);
  await settle();
  assert.deepEqual(calls, ["check"]);

  timers.advance(SIX_HOURS);
  await settle();
  assert.deepEqual(calls, ["check", "check"]);

  timers.advance(SIX_HOURS);
  await settle();
  assert.deepEqual(calls, ["check", "check", "check"]);
});

test("a checkout never checks on its own, but a manual check still runs", async () => {
  const timers = fakeTimers();
  const { calls, check } = collector();
  const scheduler = createVersionScheduler({
    isPackaged: false,
    timers,
    check,
    onAvailable: () => {},
    onError: () => {},
  });

  scheduler.start();
  timers.advance(SIX_HOURS * 3);
  await settle();
  assert.deepEqual(calls, []);

  const result = await scheduler.checkNow();
  assert.equal(result.kind, "none");
  assert.deepEqual(calls, ["check"]);
});

test("an automatic check reports only the answer it got", async () => {
  for (const [result, available, failed] of [
    [{ kind: "available", version: "0.2.0" }, 1, 0],
    [{ kind: "error", reason: "network" }, 0, 1],
    [{ kind: "none", currentVersion: "0.1.0" }, 0, 0],
  ]) {
    const timers = fakeTimers();
    const seen = { available: [], error: [] };
    const scheduler = createVersionScheduler({
      isPackaged: true,
      timers,
      check: async () => result,
      onAvailable: (value) => seen.available.push(value),
      onError: (value) => seen.error.push(value),
    });

    scheduler.start();
    timers.advance(30_000);
    await settle();

    assert.equal(seen.available.length, available);
    assert.equal(seen.error.length, failed);
  }
});

test("a check that throws is reported as a failed automatic check", async () => {
  const timers = fakeTimers();
  const seen = [];
  const scheduler = createVersionScheduler({
    isPackaged: true,
    timers,
    check: async () => {
      throw new Error("boom");
    },
    onAvailable: () => {},
    onError: (result) => seen.push(result),
  });

  scheduler.start();
  timers.advance(30_000);
  await settle();

  assert.equal(seen.length, 1);
  assert.equal(seen[0].kind, "error");
  assert.equal(seen[0].reason, "unexpected");
  assert.equal(seen[0].message, "boom");
});

test("stopping clears the schedule", async () => {
  const timers = fakeTimers();
  const { calls, check } = collector();
  const scheduler = createVersionScheduler({
    isPackaged: true,
    timers,
    check,
    onAvailable: () => {},
    onError: () => {},
  });

  scheduler.start();
  scheduler.stop();
  timers.advance(SIX_HOURS * 2);
  await settle();

  assert.deepEqual(calls, []);
});

function slowCheck() {
  const calls = [];
  let finish = null;
  return {
    calls,
    check: () => {
      calls.push("check");
      return new Promise((resolve) => {
        finish = resolve;
      });
    },
    finish: (result) => finish(result),
  };
}

test("a timer that fires during a check does not start a second request", async () => {
  const timers = fakeTimers();
  const slow = slowCheck();
  const scheduler = createVersionScheduler({
    isPackaged: true,
    timers,
    check: slow.check,
    onAvailable: () => {},
    onError: () => {},
  });

  scheduler.start();
  timers.advance(30_000);
  await settle();
  assert.deepEqual(slow.calls, ["check"]);

  // The long interval comes due while the first answer has not arrived. It is
  // skipped, and the schedule carries on rather than stacking requests.
  timers.advance(SIX_HOURS);
  await settle();
  assert.deepEqual(slow.calls, ["check"]);

  slow.finish({ kind: "none" });
  await settle();

  timers.advance(SIX_HOURS);
  await settle();
  assert.deepEqual(slow.calls, ["check", "check"]);
});

test("a manual check during an automatic check shares its answer", async () => {
  const timers = fakeTimers();
  const slow = slowCheck();
  const scheduler = createVersionScheduler({
    isPackaged: true,
    timers,
    check: slow.check,
    onAvailable: () => {},
    onError: () => {},
  });

  scheduler.start();
  timers.advance(30_000);
  await settle();

  const manual = scheduler.checkNow();
  await settle();
  assert.deepEqual(slow.calls, ["check"]);

  slow.finish({ kind: "none", currentVersion: "0.1.0" });
  assert.deepEqual(await manual, { kind: "none", currentVersion: "0.1.0" });
  assert.deepEqual(slow.calls, ["check"]);

  // Once the first answer is in, a manual check is allowed again.
  const later = scheduler.checkNow();
  await settle();
  assert.deepEqual(slow.calls, ["check", "check"]);
  slow.finish({ kind: "none", currentVersion: "0.1.0" });
  assert.deepEqual(await later, { kind: "none", currentVersion: "0.1.0" });
});

test("two manual checks share one request and both receive the answer", async () => {
  const timers = fakeTimers();
  const slow = slowCheck();
  const seen = [];
  const scheduler = createVersionScheduler({
    isPackaged: false,
    timers,
    check: slow.check,
    onAvailable: (result) => seen.push(result),
    onError: (result) => seen.push(result),
  });

  const first = scheduler.checkNow();
  const second = scheduler.checkNow();
  await settle();
  assert.deepEqual(slow.calls, ["check"]);

  slow.finish({ kind: "available", version: "0.2.0" });
  assert.deepEqual(await first, { kind: "available", version: "0.2.0" });
  assert.deepEqual(await second, { kind: "available", version: "0.2.0" });
  // A manual answer belongs to the caller, not to the automatic notifier.
  assert.deepEqual(seen, []);
});

test("an automatic check that comes due during a manual check is skipped", async () => {
  const timers = fakeTimers();
  const slow = slowCheck();
  const scheduler = createVersionScheduler({
    isPackaged: true,
    timers,
    check: slow.check,
    onAvailable: () => {},
    onError: () => {},
  });

  scheduler.start();
  const manual = scheduler.checkNow();
  await settle();
  assert.deepEqual(slow.calls, ["check"]);

  timers.advance(30_000);
  await settle();
  assert.deepEqual(slow.calls, ["check"]);

  slow.finish({ kind: "none" });
  await manual;
  await settle();

  timers.advance(SIX_HOURS);
  await settle();
  assert.deepEqual(slow.calls, ["check", "check"]);
  slow.finish({ kind: "none" });
});

test("a report that fails does not stop later checks", async () => {
  const timers = fakeTimers();
  let checks = 0;
  const scheduler = createVersionScheduler({
    isPackaged: true,
    timers,
    check: async () => {
      checks += 1;
      return { kind: "available", version: "0.2.0" };
    },
    onAvailable: () => {
      throw new Error("the notification could not be shown");
    },
    onError: () => {},
  });

  scheduler.start();
  timers.advance(30_000);
  await settle();

  timers.advance(SIX_HOURS);
  await settle();
  assert.equal(checks, 2);
});

test("starting twice schedules one check, not two", async () => {
  const timers = fakeTimers();
  const { calls, check } = collector();
  const scheduler = createVersionScheduler({
    isPackaged: true,
    timers,
    check,
    onAvailable: () => {},
    onError: () => {},
  });

  scheduler.start();
  scheduler.start();
  timers.advance(30_000);
  await settle();

  assert.deepEqual(calls, ["check"]);
});

