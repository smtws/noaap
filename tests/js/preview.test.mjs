// The editor's own clock: while the words are being edited, the textarea is what playback follows
// (DESIGN.md §9, slice 39). Both halves are pure — the page's half is a list drawn from these.
import { test } from "node:test";
import assert from "node:assert/strict";

import { nowLine, timedLines, trimOffset } from "../../src/ytalbum/webui/logic.mjs";

test("a stamped line yields its words and its moment, an unstamped one only words", () => {
  const got = timedLines("[00:12.3] one\ntwo\n[01:40.0] three");
  assert.deepEqual(got, [
    { words: "one", at: 12.3 },
    { words: "two", at: null },
    { words: "three", at: 100 },
  ]);
});

test("a blank line and a stamp without words both survive the reading", () => {
  const got = timedLines("[00:05.0] one\n\n[03:05.66]");
  assert.deepEqual(got.map((l) => l.words), ["one", "", ""]);
  assert.deepEqual(got.map((l) => l.at), [5, null, 185.66]);
});

test("the line being sung is the last stamped one at or before the moment", () => {
  const lines = timedLines("[00:10.0] one\n[00:20.0] two\n[00:30.0] three");
  assert.equal(nowLine(lines, 0), -1);          // before the first stamp nothing is marked
  assert.equal(nowLine(lines, 9.9), -1);
  assert.equal(nowLine(lines, 10), 0);          // exactly on it counts
  assert.equal(nowLine(lines, 19.9), 0);
  assert.equal(nowLine(lines, 25), 1);
  assert.equal(nowLine(lines, 3600), 2);
});

test("unstamped lines are never marked, and never break the walk", () => {
  const lines = timedLines("[00:10.0] one\nno stamp here\n\n[00:30.0] three");
  assert.equal(nowLine(lines, 12), 0);
  assert.equal(nowLine(lines, 31), 3);
  assert.equal(nowLine(timedLines("nothing\nis stamped"), 99), -1);
});

test("out of order, the file's own order wins", () => {
  // a shift or a kept stamp can leave a sidecar out of order; the reader wants the line the file
  // says comes next, which is the walk the page has always used
  const lines = timedLines("[00:10.0] one\n[00:40.0] two\n[00:20.0] three");
  assert.equal(nowLine(lines, 25), 2);
  assert.equal(nowLine(lines, 45), 2);
});

test("the editor's clock is the file's, so the trim is taken off the player's time", () => {
  // a track cut to start at 1:30 plays from its untouched original: player 114.9 s is file 24.9 s
  const playing = { trimmed: true, savedStart: 90 };
  const lines = timedLines("[00:20.0] one\n[00:30.0] two");
  const at = 114.9 - trimOffset(playing);
  assert.equal(nowLine(lines, at), 0);
  assert.equal(nowLine(lines, 121 - trimOffset(playing)), 1);
  // and an untrimmed track needs no conversion at all
  assert.equal(nowLine(lines, 25 - trimOffset({ trimmed: false, savedStart: 90 })), 0);
});

test("an edit changes what is marked, at once", () => {
  // the stale-after-edit case: the same moment, a textarea the user has shifted by hand
  const before = timedLines("[00:10.0] one\n[00:20.0] two");
  const after = timedLines("[00:40.0] one\n[00:50.0] two");
  assert.equal(nowLine(before, 25), 1);
  assert.equal(nowLine(after, 25), -1);
});
