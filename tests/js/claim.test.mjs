// Claiming a draft as your own (§9, slice 69).
//
// The finding, in the user's words: the panel says "these words are a draft by deepgram/nova-3 — write
// them yourself first" and offers no publish button, and the advice cannot be followed. The cause: the
// editor read `words_by` once and sent it back on every save, so nothing ever cleared the mark.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { CLAIM_LABEL, claimOffer, wordsAfterClaim } from "../../src/noaap/webui/logic.mjs";

test("the statement is offered where the words are a draft", () => {
  const offer = claimOffer({ words_by: "deepgram/nova-3" });
  assert.equal(offer.offer, true);
  assert.equal(offer.label, CLAIM_LABEL);
  assert.match(offer.title, /draft by deepgram\/nova-3/);
  assert.match(offer.title, /one changed character of a machine's guess is not authorship/);
});

test("and not offered for words that were never a draft", () => {
  for (const d of [{}, { words_by: "" }, { words_by: null }, null]) {
    assert.equal(claimOffer(d).offer, false);
  }
});

test("a save without the statement keeps the mark", () => {
  assert.equal(wordsAfterClaim("deepgram/nova-3", false), "deepgram/nova-3");
  assert.equal(wordsAfterClaim("deepgram/nova-3", undefined), "deepgram/nova-3",
               "no checkbox at all is not a claim");
});

test("a save with it clears the mark", () => {
  assert.equal(wordsAfterClaim("deepgram/nova-3", true), "");
});

test("and a track that never had a mark sends nothing either way", () => {
  assert.equal(wordsAfterClaim("", false), "");
  assert.equal(wordsAfterClaim(undefined, true), "");
});

test("the refusal in lyrics.py names this exact label", () => {
  // an instruction that names a control which reads differently is not an instruction
  const py = readFileSync(new URL("../../src/noaap/lyrics.py", import.meta.url), "utf8");
  assert.ok(py.includes(`CLAIM_LABEL = "${CLAIM_LABEL}"`),
            "lyrics.py must carry the same words as logic.mjs");
});
