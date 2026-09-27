// Giving the words back: what the page says before anything leaves (DESIGN.md §9.42). The server
// decides whether it may be offered at all; this half is only how it is put to the user.
import { test } from "node:test";
import assert from "node:assert/strict";

import { publishConfirm, publishState } from "../../src/ytalbum/webui/logic.mjs";

const ready = {
  publish: { can: true, why: "", published: "", lines: 42, length: 219.1,
    album: "Fegefeuer", artist: "Feuerschwanz", title: "Berzerkermode" },
};

test("the confirm names everything that leaves, and that it cannot be undone", () => {
  const text = publishConfirm(ready);
  assert.match(text, /published to LRCLIB, for everyone/);
  assert.match(text, /Feuerschwanz — Berzerkermode/);
  assert.match(text, /album: Fegefeuer/);
  assert.match(text, /length: 219 s \(the file's own/);
  assert.match(text, /42 lines, with their timestamps, and the same words without them/);
  assert.match(text, /cannot be taken back/);
  assert.match(text, /Cancel: nothing leaves this machine/);
});

test("there is no confirm for something that may not be published", () => {
  assert.equal(publishConfirm({ publish: { can: false, why: "these are lrclib's own words, not yours" } }), null);
  assert.equal(publishConfirm({}), null);
  assert.equal(publishConfirm(null), null);
});

test("the button is offered, refused or already done", () => {
  assert.deepEqual(publishState(ready).can, true);
  assert.match(publishState(ready).label, /publish to lrclib/);

  const done = { publish: { published: "2026-09-27T12:00:00+00:00", can: false } };
  assert.equal(publishState(done).show, true);
  assert.equal(publishState(done).can, false);      // shown, and not pressable again
  assert.match(publishState(done).label, /published to lrclib/);
  assert.match(publishState(done).title, /2026-09-27/);

  const no = { publish: { can: false, why: "these are lrclib's own words, not yours" } };
  assert.equal(publishState(no).show, false);       // no button at all
  assert.match(publishState(no).title, /lrclib's own words/);

  assert.equal(publishState({}).show, false);
});
