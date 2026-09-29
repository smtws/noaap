// Stamping lyrics to the file's clock: the arithmetic behind tap, nudge and shift, and the
// conversion that makes a trimmed track's numbers mean the same thing (DESIGN.md §9, slice 35).
import { test } from "node:test";
import assert from "node:assert/strict";

import { lineAt, lineStart, nudged, shifted, stampOf, stampText, tapped, tenth, toFileClock, toPlayerClock, trimGuard, trimOffset, withStamp }
  from "../../src/noaap/webui/logic.mjs";

test("an untrimmed track has one clock", () => {
  const playing = { trimmed: null, start: null, savedStart: null };
  assert.equal(trimOffset(playing), 0);
  assert.equal(toFileClock(84.34, trimOffset(playing)), 84.3);
  assert.equal(toPlayerClock(84.3, trimOffset(playing)), 84.3);
});

test("a trimmed track's player clock is the original's, and the stamp belongs to the file", () => {
  const playing = { trimmed: "90.00-320.00", start: 90, savedStart: 90 };
  assert.equal(trimOffset(playing), 90);
  assert.equal(toFileClock(91.66, 90), 1.7);          // the tap case, rounded to a tenth
  assert.equal(toPlayerClock(1.7, 90), 91.7);         // and back, to play from there
  assert.equal(toFileClock(12, 90), 0);               // before the cut: the file starts at 0
});

test("the offset is the mark on disk, not one being placed", () => {
  // the file is cut to 90 s; the user is dragging the start handle to 100 and has not saved
  const playing = { trimmed: "90.00-320.00", start: 100, savedStart: 90 };
  assert.equal(trimOffset(playing), 90);
  // and a track with marks typed but never applied is not cut at all
  assert.equal(trimOffset({ trimmed: null, start: 12, savedStart: 12 }), 0);
});

test("a stamp is exactly what a hand-typed one looks like", () => {
  assert.equal(stampText(0), "[00:00.0]");
  assert.equal(stampText(84.34), "[01:24.3]");
  assert.equal(stampText(84.36), "[01:24.4]");
  assert.equal(stampText(59.97), "[01:00.0]");   // the rounding carries into the minute
  assert.equal(stampText(-3), "[00:00.0]");      // never before the start of the file
  assert.equal(stampText(3599.95), "[60:00.0]"); // and long tracks keep two digits of minutes
});

test("a stamp is read back the way both LRCLIB and the page write it", () => {
  assert.equal(stampOf("[01:24.3] Words"), 84.3);
  assert.equal(stampOf("[00:03.85] Words"), 3.85);   // LRCLIB's hundredths
  assert.equal(stampOf("[01:24:30] Words"), 84.3);   // the colon variant some files use
  assert.equal(stampOf("Words"), null);
  assert.equal(stampOf(""), null);
});

test("stamping a line keeps its words, and replaces a stamp already there", () => {
  assert.equal(withStamp("Words", 84.34), "[01:24.3] Words");
  assert.equal(withStamp("[00:12.3] Words", 84.34), "[01:24.3] Words");
  assert.equal(withStamp("[00:12.3]Words", 84.34), "[01:24.3] Words");  // a missing space is fine
  assert.equal(withStamp("", 12.3), "[00:12.3]");                       // a blank line can hold silence
  assert.equal(withStamp("[00:12.3] Words", null), "Words");            // and the stamp can come off
});

test("the cursor decides the line, and the tap moves to the next one", () => {
  const text = "one\ntwo\nthree";
  assert.equal(lineAt(text, 0), 0);
  assert.equal(lineAt(text, 5), 1);
  assert.equal(lineAt(text, text.length), 2);
  assert.equal(lineStart(text, 1), 4);

  const first = tapped(text, 1, 12.34);
  assert.equal(first.text, "[00:12.3] one\ntwo\nthree");
  assert.equal(first.line, 0);
  assert.equal(first.at, 12.3);
  assert.equal(lineAt(first.text, first.caret), 1);   // ready for the next tap
  assert.equal(first.last, false);

  const second = tapped(first.text, first.caret, 24.8);
  assert.equal(second.text, "[00:12.3] one\n[00:24.8] two\nthree");
  assert.equal(lineAt(second.text, second.caret), 2);
});

test("the last line takes its stamp and the cursor stays there", () => {
  const got = tapped("[00:12.3] one\ntwo", 14, 24.8);
  assert.equal(got.text, "[00:12.3] one\n[00:24.8] two");
  assert.equal(got.last, true);
  assert.equal(lineAt(got.text, got.caret), 1);
});

test("a tap on a stamped line rewrites it rather than adding a second one", () => {
  const got = tapped("[00:12.3] one", 3, 30);
  assert.equal(got.text, "[00:30.0] one");
});

test("a nudge moves the stamp and leaves everything else alone", () => {
  assert.deepEqual(nudged("[00:12.3] one", 0.1), { line: "[00:12.4] one", at: 12.4 });
  assert.deepEqual(nudged("[00:12.3] one", -0.5), { line: "[00:11.8] one", at: 11.8 });
  assert.deepEqual(nudged("[00:00.3] one", -0.5), { line: "[00:00.0] one", at: 0 });  // not past the start
  assert.equal(nudged("one", 0.1), null);            // nothing to nudge, and none is invented
  assert.equal(nudged("", -0.5), null);
});

test("a nudge stays on the tenth, whatever it started from", () => {
  assert.deepEqual(nudged("[00:03.85] one", 0.1), { line: "[00:04.0] one", at: 4 });
  assert.equal(tenth(3.85 + 0.1), 4);
});

test("shifting moves every stamped line and no other", () => {
  const text = "[00:12.3] one\n\nnot timed\n[00:24.8] two";
  const got = shifted(text, -2.4);
  assert.equal(got.text, "[00:09.9] one\n\nnot timed\n[00:22.4] two");
  assert.equal(got.moved, 2);

  const back = shifted(got.text, 2.4);
  assert.equal(back.text, text);                      // and it is reversible
  assert.equal(shifted("no stamps here", 1).moved, 0);
});

test("shifting cannot push a line before the start of the file", () => {
  const got = shifted("[00:01.0] one\n[00:30.0] two", -5);
  assert.equal(got.text, "[00:00.0] one\n[00:25.0] two");
});

// -- the trim window, and the difference between playing into it and jumping (§9, slice 77) --------
//
// The user: clicking a timed line stops the sound for some seconds, then it recovers. Measured on
// their own track (trim 5.0–247.4, the player holding the untrimmed original): a position past the
// end was treated as having *played* into the end, so the player started the **next track** — which
// from the outside is the sound stopping and other music arriving.

test("playing into the trim end moves on, as it always did", () => {
  assert.deepEqual(trimGuard({ current: 248, start: 5, end: 247.4, previous: 247.2 }), { next: true });
});

test("but jumping past it stops there, where the audio ends", () => {
  const said = trimGuard({ current: 250, start: 5, end: 247.4, previous: 40 });

  assert.equal(said.next, undefined, "no song change from a jump");
  assert.equal(said.pause, true);
  assert.ok(Math.abs(said.seekTo - 247.4) < 0.1, `landed at ${said.seekTo}`);
});

test("the head is still skipped, and the middle is left alone", () => {
  assert.deepEqual(trimGuard({ current: 2, start: 5, end: 247.4, previous: 1.9 }), { seekTo: 5 });
  assert.deepEqual(trimGuard({ current: 100, start: 5, end: 247.4, previous: 99.8 }), {});
  assert.deepEqual(trimGuard({ current: 2, start: 5, end: 247.4, previous: 1.9, dragging: true }), {},
    "while somebody is dragging the mark, nothing fights them");
});

test("an unsaved trim always stops rather than carrying itself into the next song", () => {
  const said = trimGuard({ current: 248, start: 5, end: 247.4, previous: 247.2, unsaved: true });

  assert.equal(said.pause, true);
  assert.equal(said.next, undefined);
});
