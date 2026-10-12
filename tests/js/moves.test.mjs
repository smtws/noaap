// A track moves past tracks, not past what is open under one (§9, slice 189; q13).
//
// The lyrics editor and the source picker are rows carrying their track's `data-id`. Alt+↓ on a
// track with its lyrics open took that row as the track to pass, read `null.value` from it and
// threw half way: nothing moved, and an Alt+↑ after it left two tracks numbered 2.
import { test } from "node:test";
import assert from "node:assert/strict";

import { nextTrack, withPanels } from "../../src/noaap/webui/logic.mjs";

/** A rendered row as these functions see it: `dataset`, `classList`, and fields only on a track. */
function row(id, panel = null) {
  return {
    dataset: { id },
    classList: panel ? [panel] : [],
    querySelector: (selector) => (panel ? null : { value: `${selector} of ${id}` }),
    name: panel ? `${panel} of ${id}` : id,
  };
}

const names = (rows) => rows.map((r) => r.name);

test("the next track is past the lyrics open under this one", () => {
  const rows = [row("v1"), row("v1", "lyrics"), row("v2"), row("v3")];
  assert.equal(nextTrack(rows, rows[0], +1).name, "v2");
});

test("the previous track is past what is open under it", () => {
  const rows = [row("v1"), row("v1", "lyrics"), row("v1", "source"), row("v2")];
  assert.equal(nextTrack(rows, rows[3], -1).name, "v1");
});

test("no track beyond the first or the last", () => {
  const rows = [row("v1"), row("v2"), row("v2", "lyrics")];
  assert.equal(nextTrack(rows, rows[0], -1), null);
  assert.equal(nextTrack(rows, rows[1], +1), null, "an open row below the last track is not a track");
});

test("with nothing open it is simply the neighbour", () => {
  const rows = [row("v1"), row("v2"), row("v3")];
  assert.equal(nextTrack(rows, rows[1], -1).name, "v1");
  assert.equal(nextTrack(rows, rows[1], +1).name, "v3");
});

test("what is open under a track goes with it when the tracks are reordered", () => {
  const rows = [row("v1"), row("v1", "lyrics"), row("v2"), row("v3"), row("v3", "source")];
  const [v1, , v2, v3] = rows;
  assert.deepEqual(names(withPanels(rows, [v2, v1, v3])),
                   ["v2", "v1", "lyrics of v1", "v3", "source of v3"]);
});

test("two open rows under one track keep their order", () => {
  const rows = [row("v1"), row("v1", "source"), row("v1", "lyrics"), row("v2")];
  assert.deepEqual(names(withPanels(rows, [rows[3], rows[0]])),
                   ["v2", "v1", "source of v1", "lyrics of v1"]);
});
