// Choosing which release an album is (DESIGN §9, slice 157; R-519 item 3).
//
// The panel listed the candidates as a row of ids with "this one" buttons, which is not how a
// person decides between nine pressings of one single — and pinning left them to find the Identify
// button again. The dialog asks the question, preselects the best fit, and the look runs on the
// answer. These cases cover what it offers and in what order; the dialog itself is a `<dialog>`
// with a radio group, so the focus trap, Escape, Tab, the arrow keys and "3 of 9" are the
// browser's own.
import { test } from "node:test";
import assert from "node:assert/strict";

import { identifyState, offerFacts, offersToChoose } from "../../src/noaap/webui/logic.mjs";

// what MusicBrainz' release search really answers, trimmed to the fields a candidate keeps
const VIOLATOR = [
  { id: "never-opened", title: "Violator", date: "1990-03-19", country: "DE", tracks: 9,
    media: [9], matched: null, needed: null, format: "CD", label: "Mute" },
  { id: "fitted-seven", title: "Violator", date: "2006", country: "XW", tracks: 14,
    media: [9, 5], matched: 7, needed: 8, format: "CD + DVD-Video", label: "Mute",
    note: "collector’s edition" },
  { id: "fitted-eight", title: "Violator", date: "1990", country: "US", tracks: 9,
    media: [9], matched: 8, needed: 9, format: '7" Vinyl', label: "Sire" },
];

test("the nearest fit is offered first and preselected", () => {
  const said = offersToChoose({ offered: VIOLATOR });

  assert.deepEqual(said.rows.map((r) => r.id), ["fitted-eight", "fitted-seven", "never-opened"]);
  assert.equal(said.choose, "fitted-eight");
});

test("a release already pinned is the one preselected, whatever fitted best", () => {
  const said = offersToChoose({ offered: VIOLATOR, mbid: "never-opened" });

  assert.equal(said.choose, "never-opened", "the answer the user already gave");
  assert.ok(said.rows.find((r) => r.id === "never-opened").current);
});

test("the ones nobody opened keep the order the search answered in", () => {
  const two = [{ id: "second", title: "A", matched: null }, { id: "first", title: "A", matched: null }];
  assert.deepEqual(offersToChoose({ offered: two }).rows.map((r) => r.id), ["second", "first"]);
});

test("nothing to choose between is no dialog at all", () => {
  assert.equal(offersToChoose({ offered: [] }), null);
  assert.equal(offersToChoose({}), null);
});

test("a candidate says the few things that tell editions apart", () => {
  const said = offerFacts(VIOLATOR[1]);

  assert.equal(said.title, "Violator");
  assert.equal(said.note, "collector’s edition", "MusicBrainz' own word for which edition this is");
  assert.deepEqual(said.facts, ["2006", "XW", "9/5 track(s)", "CD + DVD-Video", "Mute"]);
  assert.equal(said.fit, "7 of 8 titles fitted");
  assert.equal(said.weighed, true);
});

test("a candidate nobody opened says so rather than pretending to a fit", () => {
  const said = offerFacts(VIOLATOR[0]);
  assert.equal(said.fit, "not opened");
  assert.equal(said.weighed, false);
  assert.deepEqual(said.facts, ["1990", "DE", "9 track(s)", "CD", "Mute"]);
});

test("the year is the year, not the whole date", () => {
  assert.equal(offerFacts({ id: "x", date: "1990-03-19" }).facts[0], "1990");
});

test("a candidate with nothing but an id is still listed", () => {
  const said = offerFacts({ id: "bare" });
  assert.equal(said.title, "untitled");
  assert.deepEqual(said.facts, ["? track(s)"]);
  assert.equal(said.note, "");
});

test("per-medium counts are shown where there are media, the total otherwise", () => {
  assert.equal(offerFacts({ id: "a", media: [9, 5] }).facts.at(-1), "9/5 track(s)");
  assert.equal(offerFacts({ id: "b", tracks: 12 }).facts.at(-1), "12 track(s)");
});

// -- what the panel says when nothing fitted (§9, slice 158; R-530) ------------------------------

test("a check that weighed releases and fitted none asks which one it is", () => {
  // The reviewer's case on `Depeche Mode/Violator`: twenty editions weighed, three opened, the best
  // fitting 7 of the 8 titles it needed — and the panel answered "MusicBrainz has no release that
  // matches this album", with no way to choose. The change list is empty (nothing would be
  // written) and nothing fitted, so those two alone cannot tell "none of them" from "there is
  // nothing"; the releases it weighed are the third thing.
  const said = identifyState({ check: { lines: [], matched: false, version: "v1",
                                        offered: VIOLATOR }, version: "v1" });

  assert.equal(said.canChoose, true, "the dialog must be reachable");
  assert.match(said.note, /weighed 3 release\(s\)/);
  assert.match(said.note, /Which one is this album\?/);
  assert.doesNotMatch(said.note, /no release/);
  assert.equal(said.canApply, false, "there is still nothing to write");
});

test("nothing weighed and nothing fitted is still nothing", () => {
  const said = identifyState({ check: { lines: [], matched: false, version: "v1", offered: [] },
                               version: "v1" });
  assert.equal(said.canChoose, false);
  assert.match(said.note, /no release that matches/);
});

test("an album MusicBrainz agrees with is not asked about", () => {
  const said = identifyState({ check: { lines: [], matched: true, version: "v1", offered: [] },
                               version: "v1" });
  assert.equal(said.canChoose, false);
  assert.match(said.note, /nothing to change/);
});

test("a check with changes can still offer the choice", () => {
  // it fitted one release well enough to list changes, and others were weighed: both are true
  const said = identifyState({ check: { lines: ["mbid: nothing → x"], matched: true,
                                        version: "v1", offered: VIOLATOR }, version: "v1" });
  assert.equal(said.canApply, true);
  assert.equal(said.canChoose, true);
});

test("every answer says whether the choice is there, so no caller reads undefined", () => {
  for (const check of [null, { lines: [], matched: false, version: "old", offered: VIOLATOR }]) {
    for (const running of [true, false]) {
      const said = identifyState({ check, running, version: "v1" });
      assert.equal(typeof said.canChoose, "boolean", JSON.stringify({ check, running }));
    }
  }
});
