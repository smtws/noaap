// Two copies of one song, and the choice the pass could not make (DESIGN.md §9, slice 55).
//
// The page's half of it: which copies are still waiting, and what one line about a copy says.
// `awaitingChoice` is the twin of `PlanTrack.undecided_copies` — both answers empty it, and
// nothing else does, which is what lets the library page put a number on it.
import { test } from "node:test";
import assert from "node:assert/strict";

import { awaitingChoice, candidateLine, refLabel, sourceChange } from "../../src/noaap/webui/logic.mjs";

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
