// Work that yields, so a typed letter can be painted (DESIGN §9, slice 155; R-527).
//
// The user's rule, verbatim: *"an input that doesnt show what the user types immediately is
// perceived as laggy, functional reaction may take time, display input has to be an instant."*
// Measured on their 1,548 albums: folding the library's words for the filter was one 243 ms task
// and rebuilding the grid when the filter cleared another 96 ms — and for as long as the main
// thread is in one of those, no letter can appear. So both run through `inSlices`.
import { test } from "node:test";
import assert from "node:assert/strict";

import { inSlices } from "../../src/noaap/webui/logic.mjs";

/** A clock that only moves when work says it did, so a slice's size is a fact and not a race. */
function fakeClock() {
  const c = { at: 0, pauses: [] };
  c.now = () => c.at;
  c.pause = async () => { c.pauses.push(c.at); };
  return c;
}

test("the thread goes back to the browser once a slice is used up", async () => {
  const c = fakeClock();
  const done = [];

  const whole = await inSlices([1, 2, 3, 4, 5, 6], (x) => { done.push(x); c.at += 3; },
                               { ms: 8, now: c.now, pause: c.pause });

  assert.equal(whole, true);
  assert.deepEqual(done, [1, 2, 3, 4, 5, 6], "everything was done");
  // three items fill the 8 ms budget (the third is the one that overruns it), then the thread goes
  // back; the remaining three fit the next slice, and nothing pauses after the last item
  assert.deepEqual(c.pauses, [9], "once, between the two slices");
});

test("no slice runs longer than its budget plus the one item it is in", async () => {
  const c = fakeClock();
  const starts = [0];
  await inSlices(Array.from({ length: 50 }, (_, i) => i), () => { c.at += 2; },
                 { ms: 8, now: c.now, pause: async () => { starts.push(c.at); } });

  const slices = starts.slice(1).map((end, i) => end - starts[i]);
  for (const long of slices) assert.ok(long <= 8 + 2, `a slice took ${long} ms`);
});

test("one item at a time is still progress when the budget is nothing", async () => {
  const c = fakeClock();
  const done = [];

  const whole = await inSlices([1, 2, 3], (x) => { done.push(x); c.at += 100; },
                               { ms: 0, now: c.now, pause: c.pause });

  assert.equal(whole, true, "a budget smaller than one item must not stall");
  assert.deepEqual(done, [1, 2, 3]);
  assert.equal(c.pauses.length, 2, "and it hands the thread back between every one of them");
});

test("a newer keystroke abandons the run where it stands", async () => {
  const c = fakeClock();
  const done = [];
  let newer = false;

  const whole = await inSlices(Array.from({ length: 100 }, (_, i) => i),
                               (x) => { done.push(x); c.at += 3; },
                               { ms: 8, now: c.now, pause: c.pause,
                                 wanted: () => { newer = done.length >= 6; return !newer; } });

  assert.equal(whole, false, "it says it did not finish");
  assert.ok(done.length < 100, `it stopped at ${done.length} of 100`);
  assert.ok(done.length >= 3, "and not before a single slice was done");
});

test("an answer is only claimed when the work really finished", async () => {
  // what the caller depends on: `gridShows` and `libFilter` are set on true and never on false
  const c = fakeClock();
  const never = await inSlices([1, 2, 3], () => { c.at += 3; },
                               { ms: 1, now: c.now, pause: c.pause, wanted: () => false });
  assert.equal(never, false);

  const always = await inSlices([1, 2, 3], () => { c.at += 3; },
                               { ms: 1, now: c.now, pause: c.pause });
  assert.equal(always, true);
});

test("nothing to do is finished, and asks for no pause", async () => {
  const c = fakeClock();
  assert.equal(await inSlices([], () => assert.fail("nothing to work on"),
                              { now: c.now, pause: c.pause }), true);
  assert.deepEqual(c.pauses, []);
});

test("the last slice does not pause after itself", async () => {
  const c = fakeClock();
  await inSlices([1, 2], (x) => { c.at += 99; }, { ms: 8, now: c.now, pause: c.pause });
  assert.equal(c.pauses.length, 1, "between the two, and not after the second");
});
