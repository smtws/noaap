// Offering an album to MusicBrainz, the page's half (DESIGN.md §9, slice 43). ytalbum submits nothing:
// these functions decide when to offer, and what is said before their form opens.
import { test } from "node:test";
import assert from "node:assert/strict";

import { canSeed, fixConfirm, lengthFix, seedConfirm } from "../../src/noaap/webui/logic.mjs";

const plan = (fields = {}) => ({
  album: "Fegefeuer", albumartist: "Feuerschwanz", year: 2023, kind: "official_album", mbid: null,
  tracks: [{ state: "done" }, { state: "done" }, { state: "pending" }], ...fields,
});

test("an unmatched album with files is offered", () => {
  assert.deepEqual(canSeed(plan()), { can: true, why: "" });
});

test("what MusicBrainz already has, or never wanted, is not offered", () => {
  assert.equal(canSeed(plan({ mbid: "abc" })).can, false);
  assert.match(canSeed(plan({ kind: "compilation" })).why, /not compilations/);
  assert.match(canSeed(plan({ kind: "artist_playlist" })).why, /playlist somebody made/);
  assert.match(canSeed(plan({ tracks: [{ state: "pending" }] })).why, /nothing has been downloaded/);
  assert.match(canSeed(plan({ album: "   " })).why, /a title and an artist/);
  assert.equal(canSeed(null).can, false);
});

test("the confirm says it opens a form and submits nothing", () => {
  const text = seedConfirm(plan());
  assert.match(text, /release editor is about to open, with 2 tracks already filled in/);
  assert.match(seedConfirm(plan({ tracks: [{ state: "done" }] })), /with 1 track already/);  // not "1 tracks"
  assert.match(text, /Feuerschwanz — Fegefeuer \(2023\)/);
  assert.match(text, /ytalbum submits nothing/);
  assert.match(text, /until you press their own submit button/);
  assert.match(text, /releases that were really released/);
});

test("a recording whose length disagrees is flagged with both numbers", () => {
  const track = { mbid: "rec-1", file_length: 252.4, mb_length: 211, title: "Song" };
  assert.deepEqual(lengthFix(track), { ours: 252.4, theirs: 211, apart: 41.4 });
  // the trim is the usual reason a file is shorter, so the threshold is generous
  assert.equal(lengthFix({ mbid: "rec-1", file_length: 252.4, mb_length: 250 }), null);
  assert.equal(lengthFix({ file_length: 252.4, mb_length: 211 }), null);     // not on MusicBrainz
  assert.equal(lengthFix({ mbid: "rec-1", mb_length: 211 }), null);          // no file yet
  assert.equal(lengthFix(null), null);
});

test("and the confirm for it says ytalbum cannot make the change", () => {
  const track = { mbid: "rec-1", file_length: 252.4, mb_length: 211, title: "Song" };
  const text = fixConfirm(track, lengthFix(track));
  assert.match(text, /their recording: 3:31/);
  assert.match(text, /your file:\s+4:12/);
  assert.match(text, /seeding format is for releases, not recordings/);
  assert.match(text, /only if you are sure/);
  assert.match(text, /Nothing is sent from here/);
});
