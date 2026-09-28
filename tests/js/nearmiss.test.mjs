// What the panel says about an entry that is nearly this recording (DESIGN.md §9, slice 46). The server
// decides; these sentences only report what it decided, including when it decided nothing.
import { test } from "node:test";
import assert from "node:assert/strict";

import { nearMiss } from "../../src/noaap/webui/logic.mjs";

test("with nothing checked yet, it offers the check where one is possible", () => {
  assert.match(nearMiss({ can_check: true }).say, /a few seconds off this file/);
  assert.equal(nearMiss({ can_check: true }).action, "check");
  assert.equal(nearMiss({ can_check: false }), null);      // no aligner: no promise
  assert.equal(nearMiss(null), null);
});

test("a fit says so, with the number behind it", () => {
  const got = nearMiss({ fit: { decided: "words+stamps", ours: "218.1", theirs: "213.7", span: "0.968" } });
  assert.match(got.say, /4 s from this file/);
  assert.match(got.say, /cover 97% of the singing/);
  assert.equal(got.action, null);
});

test("the song's words on another cut keep the words and say whose clock it is", () => {
  const got = nearMiss({ fit: { decided: "words", ours: "304.8", theirs: "288.8", why: "another cut" } });
  assert.match(got.say, /These are the song's words/);
  assert.match(got.say, /another cut of it/);
  assert.match(got.say, /timings are this file's own, placed by the aligner/);
});

test("and a clip says that instead, because it is a fact about the file", () => {
  const got = nearMiss({ fit: { decided: "words", ours: "83.1", theirs: "222.0", why: "a clip" } });
  assert.match(got.say, /this file is a clip of it/);
});

test("a rejection names the reason and promises not to ask again", () => {
  const got = nearMiss({ fit: { decided: "reject", ours: "200", theirs: "204", unplaced: "0.71" } });
  assert.match(got.say, /not this song/);
  assert.match(got.say, /could not place 71% of its words/);
  assert.match(got.say, /will not be offered again/);
});

test("what nobody could decide is handed over with both numbers", () => {
  const got = nearMiss({ fit: { decided: "unclear", ours: "250", theirs: "245", span: "0.82", unplaced: "0" } });
  assert.match(got.say, /placed 100% of its lines across 82% of the singing/);
  assert.match(got.say, /neither a fit nor a miss/);
  assert.equal(got.action, "plain");
});

test("too far apart to be worth an alignment: shown, with both lengths", () => {
  const got = nearMiss({ fit: { decided: "shown", ours: "83.1", theirs: "222.0", why: "a clip" } });
  assert.match(got.say, /for a 3:42 recording/);
  assert.match(got.say, /this file is 1:23.1/);
  assert.match(got.say, /probably a clip of the song/);
  assert.equal(got.action, "plain");
});

test("a track that already has words is never offered more", () => {
  // found on the scratch library: a sidecar the plan did not know about made the words the user's,
  // and a stale verdict in the plan was still offering to take lrclib's
  const withWords = { text: "[00:01.0] words already here", can_check: true,
    fit: { decided: "shown", ours: "219.1", theirs: "95.0", why: "another cut" } };
  assert.equal(nearMiss(withWords), null);
  assert.equal(nearMiss({ text: "words", can_check: true }), null);
  // but where the words came *from* a verdict, the panel still says which
  const taken = { text: "[00:01.0] a line", fit: { decided: "words+stamps", ours: "218.1", theirs: "213.7", span: "0.97" } };
  assert.match(nearMiss(taken).say, /words and timings fit it/);
});

test("words taken by hand say so", () => {
  const got = nearMiss({ text: "a line", fit: { decided: "words by hand", ours: "1", theirs: "2" } });
  assert.match(got.say, /taken without their timings on your say-so/);
  assert.equal(got.action, null);
});
