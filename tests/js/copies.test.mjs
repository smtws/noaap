// Two copies of one song, and the choice the pass could not make (DESIGN.md §9, slice 55).
//
// The page's half of it: which copies are still waiting, and what one line about a copy says.
// `awaitingChoice` is the twin of `PlanTrack.undecided_copies` — both answers empty it, and
// nothing else does, which is what lets the library page put a number on it.
import { test } from "node:test";
import assert from "node:assert/strict";

import { awaitingChoice, candidateLine, copyLabels, refLabel, sourceChange } from "../../src/noaap/webui/logic.mjs";

const track = (over = {}) => ({
  video_id: "aaaaaaaaaaa", artist: "Kupfergold", title: "Und 'n Tripper",
  source_override: null, refused_candidates: [],
  candidates: [
    { ref: "aaaaaaaaaaa", provider: "youtube", added_by: "source", why: "the playlist's own video",
      codec: "opus", bitrate: 128000, cutoff_khz: 20, full_band: false, length: 200, bytes: 3_200_000 },
    { ref: "/home/someone/Music/legacy/Kupfergold/01 - Und 'n Tripper.flac", provider: "folder",
      added_by: "pass", undecided: true, codec: "flac", bitrate: 900000, cutoff_khz: 20,
      full_band: false, length: 200, bytes: 25_000_000,
      why: "the incoming file is lossless and the one here is not, but they hold the same audio" },
  ],
  ...over,
});

const OFFER = "/home/someone/Music/legacy/Kupfergold/01 - Und 'n Tripper.flac";

test("a copy the pass could not rank is waiting for a person", () => {
  assert.deepEqual(awaitingChoice(track()).map((c) => c.ref), [OFFER]);
});

test("taking it ends the question, and so does refusing it", () => {
  assert.deepEqual(awaitingChoice(track({ source_override: OFFER })), []);
  assert.deepEqual(awaitingChoice(track({ refused_candidates: [OFFER] })), []);
});

test("an ordinary alternative source is not a question anyone asked", () => {
  const chosen = track({ candidates: [track().candidates[0],
    { ref: "bbbbbbbbbbb", added_by: "user", why: "chosen instead of the playlist's" }] });
  assert.deepEqual(awaitingChoice(chosen), []);
  assert.deepEqual(awaitingChoice({ video_id: "a", candidates: [] }), []);
});

test("the line about a waiting copy says nothing could choose, and then the numbers", () => {
  const [here, offer] = track().candidates;

  const line = candidateLine(offer, false, false);
  assert.match(line, /^nothing could choose: the incoming file is lossless/);
  assert.match(line, /flac 900 kbps/);
  assert.match(line, /to 20 kHz/);
  assert.match(line, /25\.0 MB/);

  // and the one in use is described the same way, so the two are read side by side
  assert.match(candidateLine(here, true, false), /^in use · opus 128 kbps · to 20 kHz/);
});

test("in use and refused are said before any verdict is", () => {
  const offer = track().candidates[1];
  assert.match(candidateLine(offer, true, false), /^in use/);
  assert.match(candidateLine(offer, false, true), /^refused/);
});

test("the offer is named by its last two parts, never by someone's home directory", () => {
  assert.equal(refLabel(OFFER), "Kupfergold/01 - Und 'n Tripper.flac");
  assert.ok(!candidateLine(track().candidates[1], false, false).includes("/home/"));
});

test("taking a listed copy says it is another copy, not another recording", () => {
  const t = track();
  const copy = t.candidates[1];

  assert.ok(sourceChange(t, OFFER, copy).lines.includes("The track is fetched again from that copy."));
  // typing a video in means what it always meant
  assert.ok(sourceChange(t, "bbbbbbbbbbb").lines
    .includes("The track is downloaded again — it is a different recording."));
});

// -- telling two copies of the same shape apart (§9, slice 77, R-271) -------------------------------
//
// The finding, from a real library: a track whose two copies live in two libraries of the same
// layout had the **same** label — the last two parts of both paths are the album folder and the file
// name — so the panel showed one line twice and only the numbers beside them differed.

const ONE = "/home/somebody/Music/first/Various Artists/An Album/09 - A Song.mp3";
const TWO = "/home/somebody/Music/second/Various Artists/An Album/09 - A Song.mp3";

test("two copies with the same last two parts get labels that differ", () => {
  const labels = copyLabels([ONE, TWO]);

  assert.notEqual(labels.get(ONE), labels.get(TWO));
  assert.ok(labels.get(ONE).endsWith("An Album/09 - A Song.mp3"));
  assert.ok(labels.get(ONE).startsWith("first/"), labels.get(ONE));
  assert.ok(labels.get(TWO).startsWith("second/"), labels.get(TWO));
});

test("and a label grows no further than it has to", () => {
  const other = "/elsewhere/Another Album/09 - A Song.mp3";
  const labels = copyLabels([ONE, other]);

  assert.equal(labels.get(ONE), refLabel(ONE), "these two differ already");
  assert.equal(labels.get(other), "Another Album/09 - A Song.mp3");
});

test("a ref that is not a path is left alone", () => {
  const labels = copyLabels(["patreon:video:100004", "dQw4w9WgXcQ", ONE]);

  assert.equal(labels.get("patreon:video:100004"), "patreon:video:100004");
  assert.equal(labels.get("dQw4w9WgXcQ"), "dQw4w9WgXcQ");
});

test("two refs that are genuinely the same string stay one label", () => {
  const labels = copyLabels([ONE, ONE]);

  assert.equal(labels.size, 1);
  assert.equal(labels.get(ONE), refLabel(ONE), "nothing to tell apart, so nothing grows");
});
