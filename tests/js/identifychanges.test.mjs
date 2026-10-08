// What an identify check counts as a change (DESIGN §9, slice 154; R-519 item 1).
//
// The user: *Identify said "matched" and wrote nothing visible — no release id in the field, no
// cover.* Reproduced on a library of my own: the write path was sound, and the panel was counting
// the pass's **progress** as changes. `identifyLines` kept every log line that was not a `===`
// header, so an album MusicBrainz agreed with still produced six lines — `reading …`,
// `left alone: …`, `MusicBrainz: looking for …`, `MusicBrainz: release matched`,
// `existing album …: 0 new, 0 no longer in the source` — and the panel announced "6 change(s)",
// offered Apply, and the apply wrote nothing. `identifyState`'s own "nothing to change" branch
// could never be reached.
//
// The three jobs below are real: a check on a fresh album (20 changes), the apply that followed,
// and a second check on the same album afterwards (none). Only the fields the page reads are kept,
// and the scratch library's path is shortened to `/music`.
import { test } from "node:test";
import assert from "node:assert/strict";

import { identifyLines, identifyState } from "../../src/noaap/webui/logic.mjs";

const CHECK_WITH_CHANGES = {
  "state": "done",
  "log": [
    "only: Alice Cooper/Dragontown (1 album)",
    "=== [1/1] Alice Cooper — Dragontown",
    "reading Alice Cooper/Dragontown …",
    "  left alone: 1 hidden",
    "  MusicBrainz: looking for the release “Dragontown”",
    "MusicBrainz: release matched",
    "existing album Alice Cooper/Dragontown: 0 new, 0 no longer in the source",
    "  year: nothing → 2001",
    "  mbid: nothing → 7c99fae7-73c0-45b0-8cdd-a81f9c9864c4",
    "  cover: from https://coverartarchive.org/release/7c99fae7-73c0-45b0-8cdd-a81f9c9864c4/front-500",
    "  01 Triggerman: a recording id",
    "  02 Deeper: a recording id",
    "  03 Dragontown: a recording id",
    "  04 title: Sex, Death and, Money → Sex, Death and Money",
    "  04 Sex, Death and Money: a recording id",
    "  05 Fantasy Man: a recording id",
    "  07 Disgraceland: a recording id",
    "  08 title: Sister Sarah → Sister Sara",
    "  08 Sister Sara: a recording id",
    "  09 title: Every Woman Has A Name → Every Woman Has a Name",
    "  09 Every Woman Has a Name: a recording id",
    "  10 title: Just Wanna Be God → I Just Wanna Be God",
    "  10 I Just Wanna Be God: a recording id",
    "  11 title: It's Much Too Late → It’s Much Too Late",
    "  11 It’s Much Too Late: a recording id",
    "  Somewhere: 1-06 → 1-12",
    "  I Am The Sentinel: 1-12 → 1-13"
  ],
  "result": [
    {
      "status": "reported",
      "message": "20 change(s)",
      "changes": [
        "year: nothing → 2001",
        "mbid: nothing → 7c99fae7-73c0-45b0-8cdd-a81f9c9864c4",
        "cover: from https://coverartarchive.org/release/7c99fae7-73c0-45b0-8cdd-a81f9c9864c4/front-500",
        "01 Triggerman: a recording id",
        "02 Deeper: a recording id",
        "03 Dragontown: a recording id",
        "04 title: Sex, Death and, Money → Sex, Death and Money",
        "04 Sex, Death and Money: a recording id",
        "05 Fantasy Man: a recording id",
        "07 Disgraceland: a recording id",
        "08 title: Sister Sarah → Sister Sara",
        "08 Sister Sara: a recording id",
        "09 title: Every Woman Has A Name → Every Woman Has a Name",
        "09 Every Woman Has a Name: a recording id",
        "10 title: Just Wanna Be God → I Just Wanna Be God",
        "10 I Just Wanna Be God: a recording id",
        "11 title: It's Much Too Late → It’s Much Too Late",
        "11 It’s Much Too Late: a recording id",
        "Somewhere: 1-06 → 1-12",
        "I Am The Sentinel: 1-12 → 1-13"
      ]
    }
  ]
};

const APPLY = {
  "state": "done",
  "log": [
    "only: Alice Cooper/Dragontown (1 album)",
    "=== [1/1] Alice Cooper — Dragontown",
    "reading Alice Cooper/Dragontown …",
    "  left alone: 1 hidden",
    "  MusicBrainz: looking for the release “Dragontown”",
    "MusicBrainz: release matched",
    "existing album Alice Cooper/Dragontown: 0 new, 0 no longer in the source",
    "  year: nothing → 2001",
    "  mbid: nothing → 7c99fae7-73c0-45b0-8cdd-a81f9c9864c4",
    "  cover: from https://coverartarchive.org/release/7c99fae7-73c0-45b0-8cdd-a81f9c9864c4/front-500",
    "  01 Triggerman: a recording id",
    "  02 Deeper: a recording id",
    "  03 Dragontown: a recording id",
    "  04 title: Sex, Death and, Money → Sex, Death and Money",
    "  04 Sex, Death and Money: a recording id",
    "  05 Fantasy Man: a recording id",
    "  07 Disgraceland: a recording id",
    "  08 title: Sister Sarah → Sister Sara",
    "  08 Sister Sara: a recording id",
    "  09 title: Every Woman Has A Name → Every Woman Has a Name",
    "  09 Every Woman Has a Name: a recording id",
    "  10 title: Just Wanna Be God → I Just Wanna Be God",
    "  10 I Just Wanna Be God: a recording id",
    "  11 title: It's Much Too Late → It’s Much Too Late",
    "  11 It’s Much Too Late: a recording id",
    "  Somewhere: 1-06 → 1-12",
    "  I Am The Sentinel: 1-12 → 1-13",
    "downloading 0 of 12 tracks into /music/Alice Cooper/Dragontown",
    "12/12 tracks done"
  ],
  "result": [
    {
      "status": "ok",
      "message": "",
      "changes": [
        "year: nothing → 2001",
        "mbid: nothing → 7c99fae7-73c0-45b0-8cdd-a81f9c9864c4",
        "cover: from https://coverartarchive.org/release/7c99fae7-73c0-45b0-8cdd-a81f9c9864c4/front-500",
        "01 Triggerman: a recording id",
        "02 Deeper: a recording id",
        "03 Dragontown: a recording id",
        "04 title: Sex, Death and, Money → Sex, Death and Money",
        "04 Sex, Death and Money: a recording id",
        "05 Fantasy Man: a recording id",
        "07 Disgraceland: a recording id",
        "08 title: Sister Sarah → Sister Sara",
        "08 Sister Sara: a recording id",
        "09 title: Every Woman Has A Name → Every Woman Has a Name",
        "09 Every Woman Has a Name: a recording id",
        "10 title: Just Wanna Be God → I Just Wanna Be God",
        "10 I Just Wanna Be God: a recording id",
        "11 title: It's Much Too Late → It’s Much Too Late",
        "11 It’s Much Too Late: a recording id",
        "Somewhere: 1-06 → 1-12",
        "I Am The Sentinel: 1-12 → 1-13"
      ]
    }
  ]
};

const CHECK_NOTHING = {
  "state": "done",
  "log": [
    "only: Alice Cooper/Dragontown (1 album)",
    "=== [1/1] Alice Cooper — Dragontown",
    "reading Alice Cooper/Dragontown …",
    "  left alone: 1 hidden, 1 image",
    "  MusicBrainz: looking for the release “Dragontown”",
    "MusicBrainz: release matched",
    "existing album Alice Cooper/Dragontown: 0 new, 0 no longer in the source"
  ],
  "result": [
    {
      "status": "reported",
      "message": "0 change(s)",
      "changes": []
    }
  ]
};

test("the changes are the server's list, not its log", () => {
  const lines = identifyLines(CHECK_WITH_CHANGES);
  assert.equal(lines.length, 20);
  assert.ok(lines.includes("mbid: nothing \u2192 7c99fae7-73c0-45b0-8cdd-a81f9c9864c4"),
    JSON.stringify(lines.slice(0, 3)));
  for (const noise of ["reading", "left alone", "MusicBrainz: looking", "MusicBrainz: release",
                       "existing album", "only:"]) {
    assert.ok(!lines.some((l) => l.startsWith(noise)), `“${noise}” is progress, not a change`);
  }
});

test("an album MusicBrainz agrees with has nothing to apply", () => {
  const lines = identifyLines(CHECK_NOTHING);
  assert.deepEqual(lines, [], "its log still has six progress lines; none of them is a change");

  const said = identifyState({ check: { lines, matched: true, version: "v1" }, version: "v1" });
  assert.equal(said.canApply, false, "there is nothing to write, so Apply is not offered");
  assert.match(said.note, /nothing to change/);
});

test("an album with changes offers to apply exactly them", () => {
  const lines = identifyLines(CHECK_WITH_CHANGES);
  const said = identifyState({ check: { lines, matched: true, version: "v1" }, version: "v1" });
  assert.equal(said.canApply, true);
  assert.equal(said.note, "Apply writes exactly what is listed: 20 change(s).");
});

test("the apply says what it wrote", () => {
  // the user pressed Apply and got `downloading 0 of 12 tracks` / `12/12 tracks done`
  assert.equal(APPLY.result[0].status, "ok");
  assert.equal(APPLY.result[0].changes.length, 20);
  assert.ok(APPLY.result[0].changes.some((l) => l.startsWith("cover: from ")));
  assert.ok(APPLY.result[0].changes.some((l) => l.startsWith("mbid: nothing \u2192 ")));
});

test("what the check offered is what the apply reports", () => {
  assert.deepEqual(APPLY.result[0].changes, CHECK_WITH_CHANGES.result[0].changes,
    "the same list, so the two can be read against each other");
});

test("a job from before the outcome carried its changes still shows something", () => {
  // a page left open across an upgrade: no `result`, so the log is all there is
  const old = { state: "done", log: CHECK_WITH_CHANGES.log };
  assert.ok(identifyLines(old).length > 0);
});

test("a job that failed before reporting anything offers no apply", () => {
  const said = identifyState({ check: { lines: identifyLines({ state: "failed", log: [], result: [] }),
                                        matched: false, version: "v1" }, version: "v1" });
  assert.equal(said.canApply, false);
  assert.match(said.note, /no release that matches/);
});
