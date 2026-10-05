import assert from "node:assert/strict";
import { test } from "node:test";

import { POLL_CEILING_MS, pollFailureIsOffline, pollPlan } from "../../src/noaap/webui/logic.mjs";

test("a tick that finds one request in flight does not open another", () => {
  const plan = pollPlan({ polling: true, busy: true });
  assert.equal(plan.ask, false);
  assert.equal(plan.next, 1000, "it comes back soon, it just does not ask now");
});

test("with nothing in flight it asks, and the delay follows what is happening", () => {
  assert.deepEqual(pollPlan({}), { ask: true, next: 8000 });
  assert.deepEqual(pollPlan({ busy: true }), { ask: true, next: 700 });
  assert.deepEqual(pollPlan({ waiting: true }), { ask: true, next: 700 });
  assert.deepEqual(pollPlan({ offline: true }), { ask: true, next: 3000 });
  assert.deepEqual(pollPlan({ offline: true, busy: true }), { ask: true, next: 3000 },
    "offline first: a dead server is not polled every 700 ms");
});

test("a busy library polled every 700ms never stacks requests", () => {
  // what the user's page did: 107 s per answer, a tick every 700 ms
  let inFlight = 0;
  let opened = 0;
  for (let t = 0; t < 107000; t += 700) {
    const plan = pollPlan({ polling: inFlight > 0, busy: true });
    if (plan.ask) { inFlight += 1; opened += 1; }
  }
  assert.equal(opened, 1, "one request for the whole 107 seconds");
  assert.equal(inFlight, 1);
});

test("the ceiling is far above the staleness bound the server states", () => {
  const staleAfter = 60;            // what /api/state reports as `stale_after`
  assert.ok(POLL_CEILING_MS > staleAfter * 1000 * 1.5, POLL_CEILING_MS);
  assert.ok(POLL_CEILING_MS >= 120000);
});

test("a poll that fails has waited past the ceiling, so it is a dead server", () => {
  assert.equal(pollFailureIsOffline(), true);
});
