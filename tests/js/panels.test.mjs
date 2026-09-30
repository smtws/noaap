// What a panel offers, which is where the ownership rules of §9, slice 21 and §9, slice 29 become visible.
import { test } from "node:test";
import assert from "node:assert/strict";

import { HEADER, headerHas, repairState, binLabel, trackRows, lyricsPanelState, resetKind } from "../../src/noaap/webui/logic.mjs";

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

// -- the recycle bin's own place in the header (§9, slice 91) -----------------------------------------

test("an empty bin is not offered at all", () => {
  const said = binLabel(0);
  assert.equal(said.hidden, true);
  assert.equal(said.text, "Recycle bin");
  assert.match(said.title, /moved aside instead of deleting/);
});

test("a bin with things in it says how many", () => {
  const said = binLabel(4);
  assert.equal(said.hidden, false);
  assert.equal(said.text, "Recycle bin (4)");
  assert.match(said.title, /^4 things noaap moved aside/);
});

test("one thing reads as one thing", () => {
  assert.equal(binLabel(1).text, "Recycle bin (1)");
  assert.match(binLabel(1).title, /^1 thing noaap/);
});

test("a server that says nothing about the bin is taken as empty", () => {
  for (const nothing of [undefined, null, "", NaN]) assert.equal(binLabel(nothing).hidden, true);
});

test("and a count that arrives as a string still counts", () => {
  assert.equal(binLabel("7").text, "Recycle bin (7)");
});

// -- repairing is a check and then an apply (§9, slice 91) --------------------------------------------

test("with no check behind it, only the check is offered", () => {
  const said = repairState({});
  assert.equal(said.canCheck, true);
  assert.equal(said.canApply, false);
  assert.match(said.note, /Check first/);
});

test("a check that listed something may be applied", () => {
  const said = repairState({ check: { version: "v1", albums: 2, lines: ["01 would be renamed", "…"] },
                             version: "v1" });
  assert.equal(said.canApply, true);
  assert.equal(said.stale, false);
  assert.match(said.note, /exactly what this check listed: 2 album/);
});

test("a check taken before the library changed is stale, not applied", () => {
  const said = repairState({ check: { version: "v1", albums: 1, lines: ["01 would be renamed"] },
                             version: "v2" });
  assert.equal(said.canApply, false);
  assert.equal(said.stale, true);
  assert.match(said.note, /changed since this check/);
});

test("a check that found nothing offers nothing to apply", () => {
  // the summary line is always in the log, so the albums it would touch is what counts
  const said = repairState({ check: { version: "v1", albums: 0, lines: ["0 album(s) would be tidied up"] },
                             version: "v1" });
  assert.equal(said.canApply, false);
  assert.match(said.note, /found nothing to do/);
});

test("while something is writing, neither step is offered", () => {
  const said = repairState({ check: { version: "v1", albums: 1, lines: ["x"] }, version: "v1",
                             running: true });
  assert.equal(said.canCheck, false);
  assert.equal(said.canApply, false);
  assert.match(said.note, /waits until it is finished/);
});

// -- what belongs in the header (§9, slice 91) --------------------------------------------------------

test("the header holds the search, the bin, the theme and the settings — and nothing else", () => {
  assert.deepEqual(HEADER, ["open", "bin", "theme", "gear"]);
  const said = headerHas(["open", "bin", "theme", "gear"]);
  assert.deepEqual(said.extra, []);
  assert.equal(said.complete, true);
});

test("a library action in the header is named as out of place", () => {
  const said = headerHas(["open", "update", "repair", "bin", "theme", "gear"]);
  assert.deepEqual(said.extra, ["update", "repair"]);
});

test("and one missing is noticed too", () => {
  assert.equal(headerHas(["open", "theme", "gear"]).complete, false);
  assert.equal(headerHas([]).complete, false);
});
