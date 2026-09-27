// Putting a provider's proposal into the editor: the walk down the lines, and what it says about
// itself. The provider is on the other side of a boundary (DESIGN.md §9.36) — this is the page's
// half, and it is pure.
import { test } from "node:test";
import assert from "node:assert/strict";

import { alignNotice, applyStamps } from "../../src/ytalbum/webui/logic.mjs";

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

test("with everything placed the notice does not invent a complaint", () => {
  const result = applyStamps("one\ntwo", timed(12.3, 24.8));
  assert.doesNotMatch(alignNotice(timed(12.3, 24.8), result), /could not be placed/);
  assert.equal(alignNotice(null, null), null);
});
