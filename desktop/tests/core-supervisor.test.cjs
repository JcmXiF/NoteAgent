"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  BACKOFF_MS,
  MAX_RESTARTS,
  STABLE_MS,
  nextStep,
  restartBudget,
  restartDelay,
} = require("../core-supervisor.cjs");

test("the first restart waits the shortest time, and later ones wait longer", () => {
  assert.equal(restartDelay(1), BACKOFF_MS[0]);
  assert.equal(restartDelay(2), BACKOFF_MS[1]);
  assert.equal(restartDelay(3), BACKOFF_MS[2]);
});

test("a wait never grows past the last one", () => {
  assert.equal(restartDelay(9), BACKOFF_MS[BACKOFF_MS.length - 1]);
});

test("an unknown attempt is treated as the first", () => {
  assert.equal(restartDelay(0), BACKOFF_MS[0]);
  assert.equal(restartDelay(-2), BACKOFF_MS[0]);
  assert.equal(restartDelay(Number.NaN), BACKOFF_MS[0]);
});

test("a quick crash keeps the budget, a long run gives it back", () => {
  assert.equal(restartBudget({ attempts: 2, ranForMs: 1_000 }), 2);
  assert.equal(restartBudget({ attempts: 2, ranForMs: STABLE_MS }), 0);
  assert.equal(restartBudget({ attempts: 2, ranForMs: STABLE_MS * 3 }), 0);
});

test("the budget cannot go below zero on its own", () => {
  assert.equal(restartBudget({ attempts: -4, ranForMs: 10 }), 0);
  assert.equal(restartBudget({ attempts: Number.NaN, ranForMs: 10 }), 0);
});

test("quitting on purpose is never a crash", () => {
  const step = nextStep({ attempts: 0, ranForMs: 10, stopping: true });
  assert.deepEqual(step, { kind: "ignore" });
});

test("the first crash is restarted right away", () => {
  const step = nextStep({ attempts: 0, ranForMs: 2_000 });
  assert.deepEqual(step, { kind: "restart", attempt: 1, delayMs: BACKOFF_MS[0] });
});

test("a second quick crash is still retried, with a longer wait", () => {
  const step = nextStep({ attempts: 1, ranForMs: 2_000 });
  assert.deepEqual(step, { kind: "restart", attempt: 2, delayMs: BACKOFF_MS[1] });
});

test("the last attempt is allowed, and the next one is not", () => {
  const last = nextStep({ attempts: MAX_RESTARTS - 1, ranForMs: 2_000 });
  assert.equal(last.kind, "restart");
  assert.equal(last.attempt, MAX_RESTARTS);

  const spent = nextStep({ attempts: MAX_RESTARTS, ranForMs: 2_000 });
  assert.deepEqual(spent, { kind: "stop", attempts: MAX_RESTARTS });
});

test("a core that ran for a while starts its count over", () => {
  const step = nextStep({ attempts: MAX_RESTARTS, ranForMs: STABLE_MS + 1 });
  assert.deepEqual(step, { kind: "restart", attempt: 1, delayMs: BACKOFF_MS[0] });
});

test("a different budget can be used without changing the default", () => {
  const step = nextStep({ attempts: 1, ranForMs: 10, maxRestarts: 1 });
  assert.deepEqual(step, { kind: "stop", attempts: 1 });
  assert.equal(MAX_RESTARTS, 3);
});
