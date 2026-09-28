// Tracks the near-miss check handed back to a person (§9.46). Before P41 nothing listed them:
// 42 across 246 albums, each two clicks inside an album nobody had a reason to open.
import { test } from "node:test";
import assert from "node:assert/strict";

// `NEEDS_YOU` lives in app.js, which has no DOM here; the rule is small enough to state once and
// assert against, and test_web.py holds the server side of the same rule.
const needsYou = (t) => !(t.lyrics === "synced" || t.lyrics === "plain")
  && ["unclear", "shown"].includes((t.lyrics_fit || {}).decided);

test("the two verdicts that hand the question back are the ones that need you", () => {
  assert.equal(needsYou({ lyrics: "none", lyrics_fit: { decided: "unclear" } }), true);
  assert.equal(needsYou({ lyrics: "none", lyrics_fit: { decided: "shown" } }), true);
});

test("a decided track needs nobody", () => {
  for (const decided of ["words+stamps", "words", "reject", "words by hand"]) {
    assert.equal(needsYou({ lyrics: "none", lyrics_fit: { decided } }), false, decided);
  }
  assert.equal(needsYou({ lyrics: "none" }), false, "never checked is not waiting");
  assert.equal(needsYou({ lyrics: "none", lyrics_fit: {} }), false);
});

test("words already beside the track settle it, whatever the old verdict says", () => {
  // the stale-verdict case: a sidecar arrived after the check ran, so the question is moot
  assert.equal(needsYou({ lyrics: "synced", lyrics_fit: { decided: "unclear" } }), false);
  assert.equal(needsYou({ lyrics: "plain", lyrics_fit: { decided: "shown" } }), false);
});
