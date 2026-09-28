// Where a track's audio comes from: the parser the page refuses a bad paste with, and the words
// it asks the user to confirm before a switch throws the old file's work away (DESIGN.md §9, slice 34).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { candidateLine, effectiveId, oneVideo, refLabel, sourceChange, timingNotice } from "../../src/noaap/webui/logic.mjs";

const table = JSON.parse(readFileSync(new URL("../shared/video_ids.json", import.meta.url))).cases;

const track = (over = {}) => ({
  video_id: "aaaaaaaaaaa", artist: "Kupfergold", title: "Und 'n Tripper",
  trim_start: null, trim_end: null, lyrics: "plain", source_override: null, ...over,
});

test("oneVideo agrees with the shared table (youtube.one_video's twin)", () => {
  assert.ok(table.length >= 20);
  for (const c of table) assert.equal(oneVideo(c.text), c.id, c.why);
});

test("the effective id is the playlist's video until the user names another", () => {
  assert.equal(effectiveId(track()), "aaaaaaaaaaa");
  assert.equal(effectiveId(track({ source_override: "bbbbbbbbbbb" })), "bbbbbbbbbbb");
});

test("no change is asked for when the track already takes its audio from there", () => {
  assert.equal(sourceChange(track(), "aaaaaaaaaaa"), null);
  assert.equal(sourceChange(track({ source_override: "bbbbbbbbbbb" }), "bbbbbbbbbbb"), null);
  assert.equal(sourceChange(track(), null), null); // already the playlist's own
});

test("the confirm names the marks it is about to clear", () => {
  const change = sourceChange(track({ trim_start: 12, trim_end: 196.5 }), "bbbbbbbbbbb");
  assert.equal(change.to, "bbbbbbbbbbb");
  assert.equal(change.marks, "0:12–3:16.5");
  assert.equal(change.back, false);
  const text = change.lines.join("\n");
  assert.match(text, /The trim 0:12–3:16\.5 belongs to the current file and will be cleared\./);
  assert.match(text, /downloaded again/);
  assert.match(text, /Und 'n Tripper/);
});

test("a track with no marks is not told about marks", () => {
  const text = sourceChange(track(), "bbbbbbbbbbb").lines.join("\n");
  assert.doesNotMatch(text, /trim/);
  assert.doesNotMatch(text, /timings/); // plain words have none to be wrong
});

test("timed words are named too, because their timings will be for the old file", () => {
  const text = sourceChange(track({ lyrics: "synced" }), "bbbbbbbbbbb").lines.join("\n");
  assert.match(text, /Your lyrics are kept, but their timings were written for the current file\./);
});

test("going back is a change of its own, and says so", () => {
  const change = sourceChange(track({ source_override: "bbbbbbbbbbb", trim_start: 1.6 }), null);
  assert.equal(change.to, "aaaaaaaaaaa");
  assert.equal(change.back, true);
  assert.match(change.lines.join("\n"), /Back to the source's own aaaaaaaaaaa\./);
  assert.equal(change.marks, "0:01.6–end");
});

test("the panel says nothing when the words were written for the file that is there", () => {
  assert.equal(timingNotice({}), null);
  assert.equal(timingNotice({ timings: null }), null);
});

test("and names the two lengths only when they really differ", () => {
  assert.equal(timingNotice({ timings: { source: "aaaaaaaaaaa", was: 207, now: 184 } }),
    "these timings were written for a different file, 3:27 → 3:04");
  // another upload of the same song, measured again: these two differ by rounding alone
  assert.equal(timingNotice({ timings: { source: "aaaaaaaaaaa", was: 230.0135, now: 229.841 } }),
    "these timings were written for a different file");
  // and where nobody measured either file, it is still worth saying, without numbers
  assert.equal(timingNotice({ timings: { source: "aaaaaaaaaaa", was: null, now: null } }),
    "these timings were written for a different file");
});

test("a ref that is a path is shown by its last two parts", () => {
  // a folder's ref is the file's absolute location; in a list that is someone's home directory
  assert.equal(refLabel("/home/you/Music/legacy/Van Canto/01 - Back in the Lead.mp3"),
               "Van Canto/01 - Back in the Lead.mp3");
  assert.equal(refLabel("dQw4w9WgXcQ"), "dQw4w9WgXcQ", "a ref with no separator is left alone");
  assert.equal(refLabel(""), "");
  assert.equal(refLabel(undefined), undefined);
});

test("a candidate says what it is, how good it is and what it costs", () => {
  const line = candidateLine({ codec: "flac", bitrate: 912000, cutoff_khz: 22, full_band: true,
                               length: 201.4, bytes: 41_300_000, why: "holds more audio" }, false, false);
  assert.match(line, /holds more audio/);
  assert.match(line, /flac 912 kbps/);
  assert.match(line, /to 22 kHz \(all it can hold\)/);
  assert.match(line, /41\.3 MB/);
});

test("the one in use says so, and a refused one says that instead", () => {
  const c = { codec: "opus", bitrate: 128000, why: "the playlist's own video" };
  assert.match(candidateLine(c, true, false), /^in use/);
  assert.match(candidateLine(c, false, true), /^refused/);
});

test("a candidate with nothing measured still renders", () => {
  assert.equal(candidateLine({ ref: "x", added_by: "source" }, false, false), "source");
});
