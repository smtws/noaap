// What a panel offers, which is where the ownership rules of §9, slice 21 and §9, slice 29 become visible.
import { test } from "node:test";
import assert from "node:assert/strict";

import { trackRows, lyricsPanelState, resetKind } from "../../src/noaap/webui/logic.mjs";

test("lrclib's words can be looked up again or rejected", () => {
  const s = lyricsPanelState({ text: "[00:01.00] a", status: "synced", lrclib_id: 11, owner: null });
  assert.equal(s.where, "with timestamps");
  assert.equal(s.ownership, "lrclib #11");
  assert.deepEqual(s.actions, ["Edit", "Look up again", "Not these words"]);
});

test("the user's own words are not lrclib's to replace", () => {
  const s = lyricsPanelState({ text: "mine", status: "plain", lrclib_id: 11, owner: "user" });
  assert.equal(s.ownership, "yours");
  assert.deepEqual(s.actions, ["Edit"]);   // the editor's Delete is the way back
});

test("a track with no words offers writing them, and a lookup", () => {
  const s = lyricsPanelState({ text: "", status: "none", lrclib_id: null, owner: null });
  assert.equal(s.where, "no words yet");
  assert.equal(s.ownership, null);
  assert.deepEqual(s.actions, ["Write lyrics", "Look up again"]);
});

test("after a clear the kept lrclib id is not shown as if it had words", () => {
  // the id stays on the track (it is how a sidecar is recognised as ours) but there are no words
  const s = lyricsPanelState({ text: "", status: "none", lrclib_id: 11, owner: null });
  assert.equal(s.ownership, null);
  assert.deepEqual(s.actions, ["Write lyrics", "Look up again", "Not these words"]);
});

test("the badge is a button only when there is something to go back to", () => {
  assert.equal(resetKind("user", "Feuerschwanz"), "button");
  assert.equal(resetKind("user", undefined), "badge");   // an album from before `auto` was kept
  assert.equal(resetKind("user", ""), "badge");
  assert.equal(resetKind("mb", "anything"), "badge");
  assert.equal(resetKind(undefined, "anything"), null);  // nothing claims this value
});

// -- a save with a panel open (§9, slice 89) ---------------------------------------------------------
//
// The page inserts the lyrics editor and the source picker as extra rows carrying the track's own
// `data-id`. A save that walked every row read `null.value` on the first of them and threw before it
// submitted anything: with a panel open, "Save changes" saved nothing and said nothing (R-299).

/** A stand-in for a rendered row: `dataset`, `classList` and the fields it holds. */
function row(id, { panel = null, fields = ["number", "artist", "title", "trim_start", "trim_end", "disc"] } = {}) {
  const has = new Set(panel ? [] : fields);
  return {
    dataset: { id },
    classList: panel ? [panel] : [],
    querySelector: (selector) => {
      const name = selector.replace(/^\[name=|\]$/g, "");
      return has.has(name) ? { value: `${name} of ${id}` } : null;
    },
  };
}

test("a table with no panels is all tracks", () => {
  const rows = [row("v1"), row("v2"), row("v3")];
  assert.deepEqual(trackRows(rows).map((r) => r.dataset.id), ["v1", "v2", "v3"]);
});

test("the lyrics panel of a track is not one of its rows", () => {
  const rows = [row("v1"), row("v1", { panel: "lyrics" }), row("v2")];
  assert.deepEqual(trackRows(rows).map((r) => r.dataset.id), ["v1", "v2"]);
});

test("nor the source picker, nor both at once", () => {
  const rows = [row("v1"), row("v1", { panel: "lyrics" }), row("v1", { panel: "source" }),
                row("v2", { panel: "source" }), row("v2")];
  assert.deepEqual(trackRows(rows).map((r) => r.dataset.id), ["v1", "v2"]);
});

test("a row without the fields is left out whatever it calls itself", () => {
  // the second test, for a panel kind nobody has thought of yet
  const rows = [row("v1"), { dataset: { id: "v9" }, classList: ["surprise"], querySelector: () => null }];
  assert.deepEqual(trackRows(rows).map((r) => r.dataset.id), ["v1"]);
});

test("and a row with no id at all is not a track", () => {
  const rows = [{ dataset: {}, classList: [], querySelector: () => ({ value: "x" }) }, row("v1")];
  assert.deepEqual(trackRows(rows).map((r) => r.dataset.id), ["v1"]);
});

test("nothing to save is nothing, not a throw", () => {
  assert.deepEqual(trackRows([]), []);
  assert.deepEqual(trackRows(null), []);
});

test("every field a save needs can be read from what comes back", () => {
  const [tr] = trackRows([row("v1"), row("v1", { panel: "lyrics" })]);
  for (const name of ["number", "artist", "title", "trim_start", "trim_end", "disc"]) {
    assert.equal(tr.querySelector(`[name=${name}]`).value, `${name} of v1`);
  }
});
