// Putting a provider's proposal into the editor: the walk down the lines, and what it says about
// itself. The provider is on the other side of a boundary (DESIGN.md §9.36) — this is the page's
// half, and it is pure.
import { test } from "node:test";
import assert from "node:assert/strict";

import { alignNotice, applyStamps, draftNotice, draftText } from "../../src/ytalbum/webui/logic.mjs";

const timed = (...starts) => ({
  provider: "local", model: "wav2vec2", version: "2.11.0",
  lines: starts.map((start, i) => ({ text: `line ${i}`, start, end: null })),
});

test("every line gets the stamp that came back for it", () => {
  const got = applyStamps("one\ntwo\nthree", timed(12.34, 24.8, 100));
  assert.equal(got.text, "[00:12.3] one\n[00:24.8] two\n[01:40.0] three");
  assert.deepEqual(got.unplaced, []);
  assert.equal(got.placed, 3);
});

test("blank lines keep their place and are not counted", () => {
  const got = applyStamps("one\n\n\ntwo", timed(12.3, 24.8));
  assert.equal(got.text, "[00:12.3] one\n\n\n[00:24.8] two");
  assert.equal(got.total, 2);
});

test("a line the provider would not place keeps its words and loses its stamp", () => {
  const got = applyStamps("[00:05.0] one\ntwo\nthree", timed(12.3, null, 30));
  assert.equal(got.text, "[00:12.3] one\ntwo\n[00:30.0] three");
  assert.deepEqual(got.unplaced, [1]);
  assert.equal(got.placed, 2);
});

test("stamps already in the text are replaced, never stacked", () => {
  const got = applyStamps("[00:05.0] one\n[01:02.34] two", timed(12.3, 24.8));
  assert.equal(got.text, "[00:12.3] one\n[00:24.8] two");
});

test("a short answer leaves the rest unplaced rather than shifting the words up", () => {
  const got = applyStamps("one\ntwo\nthree", timed(12.3));
  assert.equal(got.text, "[00:12.3] one\ntwo\nthree");
  assert.deepEqual(got.unplaced, [1, 2]);
});

test("the notice names the provider and never calls the clock the user's", () => {
  const result = applyStamps("one\ntwo", timed(12.3, null));
  const text = alignNotice(timed(12.3, null), result);
  assert.match(text, /timed by local\/wav2vec2/);
  assert.match(text, /a machine's proposal/);
  assert.match(text, /nothing is saved yet/);
  assert.match(text, /1 line could not be placed/);
  assert.doesNotMatch(text, /yours/);
});

test("a stamped line with no words is left alone, and counted", () => {
  // LRCLIB entries often end with a bare [03:05.66] marking the outro, and an empty stamped line is
  // a legitimate "silence starts here". It is never sent to the provider, so it is never rewritten.
  const got = applyStamps("one\n[03:05.66]\n\ntwo", timed(12.3, 24.8));
  assert.equal(got.text, "[00:12.3] one\n[03:05.66]\n\n[00:24.8] two");
  assert.equal(got.kept, 1);
  assert.equal(got.placed, 2);
  assert.equal(got.total, 2);            // it is not one of the lines that could be placed
});

test("the notice says so, because a kept stamp can end up out of order", () => {
  const two = applyStamps("one\n[03:05.66]\ntwo\n[04:00.0]", timed(12.3, 24.8));
  assert.match(alignNotice(timed(12.3, 24.8), two),
    /2 stamped lines without words were left as they were; check they are still in order\./);
  const one = applyStamps("one\ntwo\n[03:05.66]", timed(12.3, 24.8));
  assert.match(alignNotice(timed(12.3, 24.8), one),
    /1 stamped line without words was left as it was; check it is still in order\./);
  const none = applyStamps("one\n\ntwo", timed(12.3, 24.8));
  assert.doesNotMatch(alignNotice(timed(12.3, 24.8), none), /without words/);
});

test("with everything placed the notice does not invent a complaint", () => {
  const result = applyStamps("one\ntwo", timed(12.3, 24.8));
  assert.doesNotMatch(alignNotice(timed(12.3, 24.8), result), /could not be placed/);
  assert.equal(alignNotice(null, null), null);
});

// -- a draft, which is a different thing and says so ------------------------------------------

test("a draft becomes editor text, with a stamp only where the provider gave one", () => {
  const drafted = { provider: "deepgram", model: "nova-3", lines: [
    { text: "One two three.", start: 0.5, end: 1.8 },
    { text: "Four five.", start: null, end: null },
  ] };
  assert.equal(draftText(drafted), "[00:00.5] One two three.\nFour five.");
  assert.equal(draftText(null), "");
});

test("the draft notice never claims more than a guess", () => {
  const drafted = { provider: "deepgram", model: "nova-3", lines: [
    { text: "one", start: 1 }, { text: "two", start: null }] };
  const text = draftNotice(drafted);
  assert.match(text, /drafted by deepgram\/nova-3/);
  assert.match(text, /a machine's guess/);
  assert.match(text, /half a song for some tracks/);
  assert.match(text, /1 of 2 lines came with a time/);
  assert.doesNotMatch(text, /yours/);
  assert.equal(draftNotice(null), null);
});

// -- a second opinion (§9.38) -----------------------------------------------------------------

const checked = (p, lines) => ({ provider: "local", model: "wav2vec2 + large-v3", parameters: p, lines });

test("when two methods agree the notice says so", () => {
  const timed = checked({ verified_against: "large-v3", compared: "2", disagreed: "0" },
    [{ text: "one", start: 1 }, { text: "two", start: 2 }]);
  const text = alignNotice(timed, applyStamps("one\ntwo", timed));
  assert.match(text, /A second method agreed with every line it could compare\./);
});

test("and when they disagree about a few lines, why those have no stamp", () => {
  const timed = checked({ verified_against: "large-v3", compared: "3", disagreed: "1" },
    [{ text: "one", start: 1 }, { text: "two", start: null }, { text: "three", start: 3 }]);
  const got = applyStamps("one\ntwo\nthree", timed);
  assert.deepEqual(got.unplaced, [1]);
  assert.match(alignNotice(timed, got), /disagreed on 1 of 3 lines, which is why those have no stamp/);
});

test("a whole-track disagreement keeps every stamp and says how total it was", () => {
  // Measured, not assumed (catalog Y): on all five real tracks where this fired, the primary was the
  // accurate method and the second had lost the song. Placing nothing threw away good work.
  const timed = checked({ one_method: "a second method disagreed about the whole track",
    compared: "20", disagreed: "16", lost: "14", lost_beyond: "5" },
    [{ text: "one", start: 1 }, { text: "two", start: 2 }]);
  const got = applyStamps("one\ntwo", timed);
  assert.equal(got.text, "[00:01.0] one\n[00:02.0] two");
  const text = alignNotice(timed, got);
  assert.match(text, /disagreed about the whole track — 14 of 20 lines more than 5 seconds apart/);
  assert.match(text, /one method's word: play the first line before you save it/);
  assert.doesNotMatch(text, /which is why those have no stamp/);   // the per-line rule is off here
});

test("when the check could not run at all, the notice says so rather than implying agreement", () => {
  const timed = checked({ unchecked: "the graphics card has no room left" },
    [{ text: "one", start: 1 }, { text: "two", start: 2 }]);
  const text = alignNotice(timed, applyStamps("one\ntwo", timed));
  assert.match(text, /could not be checked against a second method \(the graphics card has no room left\)/);
  assert.match(text, /one method's word/);
  assert.doesNotMatch(text, /agreed/);
});

test("when the evidence names the method that lost the song, the notice says which and why", () => {
  const timed = checked({ lost_method: "large-v3", kept_method: "wav2vec2",
    lost_why: "its stamps cover only 45% of the part of the track where somebody sings",
    compared: "46", disagreed: "45", lost: "45", lost_beyond: "5" },
    [{ text: "one", start: 1 }, { text: "two", start: 2 }]);
  const got = applyStamps("one\ntwo", timed);
  assert.equal(got.text, "[00:01.0] one\n[00:02.0] two");      // the kept method's stamps, entire
  const text = alignNotice(timed, got);
  assert.match(text, /large-v3 is the one that lost it/);
  assert.match(text, /cover only 45% of the part of the track where somebody sings/);
  assert.match(text, /These stamps are wav2vec2's/);
});

test("and when it cannot say which, it says that too", () => {
  const timed = checked({ one_method: "a second method disagreed about the whole track",
    compared: "20", disagreed: "16", lost: "14", lost_beyond: "5" },
    [{ text: "one", start: 1 }, { text: "two", start: 2 }]);
  const text = alignNotice(timed, applyStamps("one\ntwo", timed));
  assert.match(text, /nothing here can say which of them is right/);
  assert.doesNotMatch(text, /lost it:/);
});
