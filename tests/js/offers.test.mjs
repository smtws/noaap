import assert from "node:assert/strict";
import { test } from "node:test";

import { offerLine, offersState } from "../../src/noaap/webui/logic.mjs";

const DRAGONTOWN = [
  { id: "70972233-aaaa", title: "Dragontown", date: "", country: "XW", tracks: 12,
    media: [12], matched: 8, needed: 10 },
  { id: "7c99fae7-bbbb", title: "Dragontown", date: "2001", country: "DE", tracks: 12,
    media: [12], matched: 8, needed: 10 },
  { id: "bfc10ae2-cccc", title: "Dragontown", date: "2021-09-17", country: "US", tracks: 12,
    media: [], matched: null, needed: null },
];

test("a weighed release reads as its shape and its fit", () => {
  assert.equal(offerLine(DRAGONTOWN[1]),
               "Dragontown (2001, DE) · 12 track(s) · 8 of 10 titles fitted");
});

test("one that was never opened says so rather than claiming a fit", () => {
  assert.equal(offerLine(DRAGONTOWN[2]),
               "Dragontown (2021-09-17, US) · 12 track(s) · not opened");
});

test("a release with no date is not pretended to have one", () => {
  assert.match(offerLine(DRAGONTOWN[0]), /^Dragontown \(XW\)/);
  assert.match(offerLine({}), /^untitled \(no date\)/);
});

test("a multi-disc release says its media, not one number", () => {
  assert.match(offerLine({ title: "Requiembryo", media: [13, 15], tracks: 28 }),
               /· 13\/15 track\(s\) ·/);
});

test("nothing weighed is nothing to show", () => {
  assert.equal(offersState({}), null);
  assert.equal(offersState({ offered: [] }), null);
});

test("the note says how many were weighed and what to do", () => {
  const s = offersState({ offered: DRAGONTOWN });
  assert.match(s.note, /weighed 3 release\(s\) and none fitted/);
  assert.match(s.note, /Pin the right one/);
  assert.equal(s.rows.length, 3);
  assert.equal(s.rows[1].id, "7c99fae7-bbbb");
});

test("a pinned release that did not fit is said differently", () => {
  assert.match(offersState({ offered: DRAGONTOWN, pinned: true }).note,
               /could not fit the release you pinned/);
});

test("the one already pinned is marked, so it is not offered as new", () => {
  const s = offersState({ offered: DRAGONTOWN, mbid: "7c99fae7-bbbb" });
  assert.deepEqual(s.rows.map((r) => r.current), [false, true, false]);
});
