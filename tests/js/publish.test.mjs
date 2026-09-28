// Giving the words back: what the page says before anything leaves (DESIGN.md §9, slice 42). The server
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
  assert.equal(publishState(no).show, false);       // no button…
  assert.match(publishState(no).why, /lrclib's own words/);   // …but the panel says why
  assert.match(publishState(no).title, /lrclib's own words/);

  assert.equal(publishState({}).show, false);
});

// The reason was once carried only as the `title` of a button that `show: false` stopped anyone
// from rendering, so it could never be read. The panel shows it as a line of its own now, the way a
// refused MusicBrainz seed always has — "no, because these are lrclib's own words" is a different
// answer from no button at all. `why` is what the render layer draws, so it is part of the contract.
// The reasons below are every string `publishable()` in lyrics.py can return and that reaches this
// branch — copied from it, not invented, so a reader can trust them as the real messages.
test("a refusal is readable, and silence is reserved for having nothing to say", () => {
  for (const why of ["there is no file beside these words yet",
                     "this track is marked instrumental",
                     "only timed lyrics are worth giving back \u2014 these have no timestamps",
                     "these are lrclib's own words, not yours",
                     "these words are a draft by deepgram \u2014 write them yourself first",
                     "lrclib already has exactly these words"]) {
    const state = publishState({ publish: { can: false, why } });
    assert.equal(state.show, false, `${why}: still no button`);
    assert.equal(state.why, why, `${why}: shown to the reader verbatim`);
  }

  // nothing to explain: no words at all, so there is no line and no empty element
  assert.equal(publishState({ publish: { can: false, why: "", published: "" } }).why, "");
  assert.equal(publishState({}).why, "");
  assert.equal(publishState(null).why, "");

  // and the two offered states say nothing extra — the label is the message there
  assert.equal(publishState(ready).why, "");
  assert.equal(publishState({ publish: { published: "2026-09-27T12:00:00+00:00", can: false } }).why, "");

  // `publishable()`'s seventh reason, "already published", never reaches the line above: the plan
  // records `at` and `sha` in the same write (service.py), so whenever the sha matches there is also
  // a date, and the date wins — the reader is told it was published, not refused.
  const again = { publish: { published: "2026-09-27T12:00:00+00:00", can: false, why: "already published" } };
  assert.equal(publishState(again).show, true);
  assert.match(publishState(again).label, /published to lrclib/);
  assert.equal(publishState(again).why, "");
});
