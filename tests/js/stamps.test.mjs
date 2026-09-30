// Stamping lyrics to the file's clock: the arithmetic behind tap, nudge and shift, and the
// conversion that makes a trimmed track's numbers mean the same thing (DESIGN.md §9, slice 35).
import { test } from "node:test";
import assert from "node:assert/strict";

import { audioRequest, editorRows, lineAt, lineStart, nudged, scrollToLine, shifted, stampOf, stampText, syncEntry, tapped, tenth, toFileClock, toPlayerClock, trimGuard, trimOffset, withStamp }
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

test("the cursor decides the line, and the tap stays on it", () => {
  const text = "one\ntwo\nthree";
  assert.equal(lineAt(text, 0), 0);
  assert.equal(lineAt(text, 5), 1);
  assert.equal(lineAt(text, text.length), 2);
  assert.equal(lineStart(text, 1), 4);

  const first = tapped(text, 1, 12.34);
  assert.equal(first.text, "[00:12.3] one\ntwo\nthree");
  assert.equal(first.line, 0);
  assert.equal(first.at, 12.3);
  assert.equal(lineAt(first.text, first.caret), 0, "still on the line it stamped");
  assert.equal(first.caret, 11, "and where it was in the words: one character in");
  assert.equal(first.last, false);

  // a second press on the same line is how a stamp is corrected, and the caret does not budge
  const second = tapped(first.text, first.caret, 24.8);
  assert.equal(second.text, "[00:24.8] one\ntwo\nthree");
  assert.equal(second.caret, first.caret);
});

test("the last line takes its stamp and the cursor stays there", () => {
  const got = tapped("[00:12.3] one\ntwo", 14, 24.8);
  assert.equal(got.text, "[00:12.3] one\n[00:24.8] two");
  assert.equal(got.last, true);
  assert.equal(lineAt(got.text, got.caret), 1);
});

test("a tap on a stamped line rewrites it rather than adding a second one", () => {
  // on a line that is not the last: the last one is stamped once and then says so (§9, slice 96)
  const got = tapped("[00:12.3] one\ntwo", 3, 30);
  assert.equal(got.text, "[00:30.0] one\ntwo");
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

// -- the window belongs to the file the player loaded (§9, slice 86, slice 87) ------------------------

test("the window is previewed on the original, which is what a cut track is played from", () => {
  assert.deepEqual(trimGuard({ current: 0.5, start: 48.1, end: 259.8 }), { seekTo: 48.1 });
  assert.deepEqual(trimGuard({ current: 300, start: 48.1, end: 259.8, previous: 299.9 }), { next: true });
  assert.deepEqual(trimGuard({ current: 0.5, start: 48.1, end: 259.8, onTheOriginal: true }),
                   { seekTo: 48.1 });
});

test("a player holding the cut file applies nothing: the bytes already carry it", () => {
  // the seconds between a save and the page learning that the file changed — the user's double skip
  assert.deepEqual(trimGuard({ current: 0.5, start: 8.6, end: null, onTheOriginal: false }), {});
  assert.deepEqual(trimGuard({ current: 300, start: 48.1, end: 259.8, onTheOriginal: false }), {});
  assert.deepEqual(trimGuard({ current: 0.5, start: 8.6, end: 100, onTheOriginal: false,
                              unsaved: true }), {}, "not even while the marks are being moved");
});

// -- what the player asks for, and what a save teaches it (§9, slice 87) ------------------------------

test("an untrimmed track is asked for as it is, with its length as the token", () => {
  assert.deepEqual(audioRequest({ trimmed: null, file_length: 194.841 }),
                   { original: false, token: "194.841", query: "&c=194.841" });
});

test("a cut track is asked for as the original, with the cut as the token", () => {
  assert.deepEqual(audioRequest({ trimmed: "8.60-", file_length: 186.22 }),
                   { original: true, token: "8.60-", query: "&o=1&c=8.60-" });
});

test("the token changes when the file does, so a replaced file is never reused", () => {
  const before = audioRequest({ trimmed: null, file_length: 194.841 });
  const after = audioRequest({ trimmed: "8.60-", file_length: 186.22 });
  assert.notEqual(before.query, after.query);
});

test("a track with no length yet is asked for without a token", () => {
  assert.deepEqual(audioRequest({ trimmed: null, file_length: null }),
                   { original: false, token: "", query: "" });
});

test("after a save the entry learns the cut, and the marks come from the plan", () => {
  const entry = { start: 8.6, end: null, savedStart: 8.6, savedEnd: null, trimmed: null,
                  file_length: 194.841 };
  const fresh = { trimmed: "8.60-", trim_start: 8.6, trim_end: null, file_length: 186.22, duration: 200 };

  assert.deepEqual(syncEntry(entry, fresh),
                   { trimmed: "8.60-", file_length: 186.22, duration: 200, savedStart: 8.6,
                     savedEnd: null, start: 8.6, end: null, original_kept: true });
});

test("a page that never saw the save learns everything at once", () => {
  // the flow the user hit: the page's entry still says "not cut" while the file on disk is cut
  const entry = { start: null, end: null, savedStart: null, savedEnd: null, trimmed: null,
                  file_length: 194.841 };
  const fresh = { trimmed: "12.25-", trim_start: 12.25, trim_end: null, file_length: 182.58, duration: 200 };

  const patched = { ...entry, ...syncEntry(entry, fresh) };

  assert.equal(patched.trimmed, "12.25-");
  assert.equal(patched.start, 12.25);
  assert.equal(audioRequest(patched).original, true, "and the next play asks for the original");
});

test("marks the user has moved but not saved are kept", () => {
  const entry = { start: 30, end: 90, savedStart: 8.6, savedEnd: null, trimmed: "8.60-",
                  file_length: 186.22 };
  const fresh = { trimmed: "8.60-", trim_start: 8.6, trim_end: null, file_length: 186.22, duration: 200 };

  const patched = { ...entry, ...syncEntry(entry, fresh) };

  assert.equal(patched.start, 30, "their unsaved mark survives a refresh");
  assert.equal(patched.end, 90);
  assert.equal(patched.savedStart, 8.6, "and what is on disk is remembered as such");
});

test("a cleared trim takes the entry back to the plain file", () => {
  const entry = { start: null, end: null, savedStart: 8.6, savedEnd: null, trimmed: "8.60-",
                  file_length: 186.22 };
  const fresh = { trimmed: null, trim_start: null, trim_end: null, file_length: 194.841, duration: 200 };

  const patched = { ...entry, ...syncEntry(entry, fresh) };

  assert.equal(patched.trimmed, null);
  assert.equal(audioRequest(patched).original, false);
  assert.equal(audioRequest(patched).query, "&c=194.841");
});

// -- the file in hand decides everything (§9, slice 88) ----------------------------------------------

test("the original is only asked for when one is really kept", () => {
  const kept = { trimmed: "12.25-", file_length: 182.58, original_kept: true };
  const gone = { trimmed: "12.25-", file_length: 182.58, original_kept: false };

  assert.equal(audioRequest(kept).original, true);
  assert.equal(audioRequest(gone).original, false, "o=1 would answer with the cut file anyway");
  assert.equal(audioRequest(gone).query, "&c=12.25-");
});

test("an older server that does not say is taken at its word", () => {
  assert.equal(audioRequest({ trimmed: "12.25-", file_length: 182.58 }).original, true);
});

test("the trim is added to a stamp only while the original is loaded", () => {
  const onOriginal = { trimmed: "12.25-", savedStart: 12.25, playingOriginal: true };
  const onTheCut = { trimmed: "12.25-", savedStart: 12.25, playingOriginal: false };

  assert.equal(trimOffset(onOriginal), 12.25);
  assert.equal(trimOffset(onTheCut), 0, "the cut file's own clock already starts at the trim");
  assert.equal(trimOffset({ trimmed: null, savedStart: null }), 0);
});

test("a jump to before the trim start is not answered by moving it back", () => {
  // the user: "every jumppoint completely restarts the track"
  assert.deepEqual(trimGuard({ current: 2, start: 12.25, end: null, jumped: true }), {});
  assert.deepEqual(trimGuard({ current: 2, start: 12.25, end: null, previous: 100 }), {},
                   "a gap since the last tick is a jump too");
  // but playing into the head from the very beginning still skips it
  assert.deepEqual(trimGuard({ current: 2, start: 12.25, end: null, previous: 1.9 }),
                   { seekTo: 12.25 });
});

test("a finished save also says whether an original is kept", () => {
  const entry = { start: null, end: null, savedStart: null, savedEnd: null, trimmed: null,
                  file_length: 194.841, original_kept: false };
  const fresh = { trimmed: "6.50-", trim_start: 6.5, trim_end: null, file_length: 188.34,
                  duration: 200, original_kept: true };

  const patched = { ...entry, ...syncEntry(entry, fresh) };

  assert.equal(patched.original_kept, true);
  assert.equal(audioRequest(patched).original, true);
  assert.equal(trimOffset({ ...patched, playingOriginal: true }), 6.5);
});


// -- the line being worked on stays in sight (§9, slice 94) ----------------------------------------
//
// The user: "stamp this line loses the focus on the line, while the nudge buttons keep it. So stamping
// line after line from the keyboard breaks after the first." Focus was never the thing — measured on a
// 34-line lyric in an 8-row editor, twelve stamps walked the caret from line 1 to line 13 with
// `scrollTop` at 0 the whole way, the editor focused every time. A programmatic selection does not
// scroll a textarea, and the stamp is the only control that moves the caret to another line.

// nine lines fit in the box, and the lyric is longer than that
const BOX = { lineHeight: 20, clientHeight: 180, lines: 34 };
const WORDS = Array.from({ length: 34 }, (_, n) => `line ${n + 1}`).join("\n");

// where each control leaves the caret, as the five handlers do it: the caret belongs to the text the
// control just wrote, not to the one it read — that is what `showLine` is given
const afterStamp = (text, caret) => {
  const got = tapped(text, caret, 12.3);
  return { text: got.text, caret: got.caret };
};
const afterNudge = (text, caret) => {
  const i = lineAt(text, caret);
  const lines = text.split("\n");
  const moved = nudged(lines[i], -0.1) || { line: lines[i] };
  lines[i] = moved.line;
  const written = lines.join("\n");
  return { text: written, caret: lineStart(written, i) };
};

test("the stamp and the nudges all stay on the line they act on", () => {
  const stamped = afterStamp(WORDS, 0);
  assert.equal(lineAt(stamped.text, stamped.caret), 0, "the stamp stays on line 1");
  // and a stamped line can be nudged four ways without the caret leaving it
  const stampedLine = withStamp("line 9", 30);
  for (const delta of [-0.5, -0.1, 0.1, 0.5]) {
    assert.ok(nudged(stampedLine, delta), `${delta} moves a stamp`);
  }
  // a lyric whose ninth line already carries a stamp, which is what a nudge needs
  const withNinth = WORDS.split("\n").map((line, i) => (i === 8 ? withStamp(line, 30) : line)).join("\n");
  const nudgedThere = afterNudge(withNinth, lineStart(withNinth, 8));
  assert.equal(lineAt(nudgedThere.text, nudgedThere.caret), 8, "the nudge stays on line 9");
  assert.match(nudgedThere.text.split("\n")[8], /^\[00:29\.9\]/, "and it moved that line's stamp");
});

test("each of the five controls keeps its line in sight", () => {
  // none of them moves to another line any more, so what has to be brought back is a line the box
  // has been scrolled away from — the editor is at the bottom of a long lyric, the caret near the top
  const at = lineStart(WORDS, 8);                        // line 9
  const stamped = afterStamp(WORDS, at);
  const stampLine = lineAt(stamped.text, stamped.caret) + 1;
  assert.equal(stampLine, 9, "the stamp works on the line the caret is in");
  assert.equal(scrollToLine({ ...BOX, line: stampLine, scrollTop: 400 }), 140,
               "and the box comes back to it, with a line of context");
  assert.equal(scrollToLine({ ...BOX, line: stampLine, scrollTop: 0 }), null,
               "while a line already in view moves nothing");

  for (const delta of [-0.5, -0.1, 0.1, 0.5]) {
    const moved = afterNudge(WORDS, at);
    const line = lineAt(moved.text, moved.caret) + 1;
    assert.equal(line, 9, `a nudge (${delta}) stays on its line`);
    assert.equal(scrollToLine({ ...BOX, line, scrollTop: 0 }), null, "so nothing scrolls");
  }
});

test("a line already in view does not move the box", () => {
  for (const line of [1, 2, 5, 9]) {
    assert.equal(scrollToLine({ ...BOX, line, scrollTop: 0 }), null, `line ${line}`);
  }
});

test("a line above the view scrolls back up, and the top is the top", () => {
  assert.equal(scrollToLine({ ...BOX, line: 2, scrollTop: 400 }), 0);
  assert.equal(scrollToLine({ ...BOX, line: 12, scrollTop: 400 }), 200);
  assert.equal(scrollToLine({ ...BOX, line: 1, scrollTop: 40 }), 0);
});

test("the last line cannot scroll past the end of the text", () => {
  const highest = BOX.lines * BOX.lineHeight - BOX.clientHeight;   // 500
  assert.equal(scrollToLine({ ...BOX, line: 34, scrollTop: 0 }), highest);
  assert.equal(scrollToLine({ ...BOX, line: 34, scrollTop: highest }), null);
});

test("a lyric that fits needs no scrolling at all", () => {
  const small = { lineHeight: 20, clientHeight: 180, lines: 6 };
  for (const line of [1, 3, 6]) assert.equal(scrollToLine({ ...small, line, scrollTop: 0 }), null);
});

test("an editor that cannot be measured is left alone", () => {
  // before layout, or in a hidden panel: no line height and no height to compare it with
  assert.equal(scrollToLine({ line: 12, lineHeight: 0, clientHeight: 180, lines: 34 }), null);
  assert.equal(scrollToLine({ line: 12, lineHeight: 20, clientHeight: 0, lines: 34 }), null);
  assert.equal(scrollToLine(), null);
});

test("a box too small for context still shows the line", () => {
  const tiny = { lineHeight: 20, clientHeight: 40, lines: 34 };   // two lines
  assert.equal(scrollToLine({ ...tiny, line: 5, scrollTop: 0 }), 60);
});

// -- the box grows (§9, slice 96), and the caret stays where it stamped (§9, slice 97) -------------------------------

test("the editor's box grows with what is typed, up to a full lyric's height", () => {
  assert.equal(editorRows(""), 8, "an empty editor is the smallest box");
  assert.equal(editorRows("one\ntwo"), 8, "a couple of lines still fit in it");
  assert.equal(editorRows(Array.from({ length: 12 }, (_, n) => `line ${n}`).join("\n")), 14);
  assert.equal(editorRows(Array.from({ length: 40 }, (_, n) => `line ${n}`).join("\n")), 26,
               "and it stops at the height a full lyric gets");
});

test("the box never shrinks while the editor is open", () => {
  // a box that jumps back when a line is deleted moves the words somebody is reading
  assert.equal(editorRows("one", 20), 20);
  assert.equal(editorRows("", 26), 26);
  assert.equal(editorRows(Array.from({ length: 30 }, () => "x").join("\n"), 26), 26);
});

test("the caret stays on a first, a middle and a last line alike", () => {
  const text = "one\ntwo\nthree";
  for (const [i, caret] of [[0, 1], [1, 5], [2, 9]]) {
    const got = tapped(text, caret, 12.3);
    assert.equal(lineAt(got.text, got.caret), i, `line ${i + 1} keeps the caret`);
    assert.equal(got.line, i);
    // the stamp is written in front of the words, and the caret keeps its place among them
    const words = got.text.split("\n")[i].replace(/^\[\d+:\d+\.\d+\] ?/, "");
    const at = got.caret - lineStart(got.text, i) - (got.text.split("\n")[i].length - words.length);
    assert.equal(at, caret - lineStart(text, i), "the same place in the words as before");
  }
});

test("any line is stamped as often as you like, the last one included", () => {
  const once = tapped("one\ntwo\nthree", 0, 5);
  const twice = tapped(once.text, once.caret, 9);
  assert.equal(twice.text.split("\n")[0], "[00:09.0] one", "a line takes a new moment");
  assert.equal(twice.caret, once.caret, "without the caret moving");

  // the last line is not a special case: fine-tuning it is the same two presses
  const last = tapped("one\ntwo", 4, 12.3);
  assert.equal(last.text, "one\n[00:12.3] two");
  const again = tapped(last.text, last.caret, 15);
  assert.equal(again.text, "one\n[00:15.0] two", "and it is rewritten, not refused");
  assert.equal(again.caret, last.caret);

  // …nor is a one-line lyric, which is its own last line
  const only = tapped("just this", 0, 3.5);
  assert.equal(only.text, "[00:03.5] just this");
  assert.equal(tapped(only.text, only.caret, 8).text, "[00:08.0] just this");
});

test("an empty line takes a stamp like any other", () => {
  // the words end with a blank line more often than not
  const got = tapped("one\n", 4, 7);
  assert.equal(got.text, "one\n[00:07.0]", "a stamp with no words after it is just the stamp");
  assert.equal(got.caret, got.text.length, "and the caret is where the words would start");
});
