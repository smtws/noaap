// P116, D2: a refresh after a finished job redrew the editor and took back what the user typed.
import { test } from "node:test";
import assert from "node:assert/strict";

import { unsavedEdits } from "../../src/noaap/webui/logic.mjs";

test("only the fields the user typed into are kept", () => {
  const fields = [
    { row: "", name: "album", value: "Vol. 1", shown: "Vol. 1" },
    { row: "v1", name: "title", value: "Der W zwo drei (D2 edit)", shown: "Der W zwo drei" },
    { row: "v2", name: "title", value: "Untouched", shown: "Untouched" },
    { row: "", name: "year", value: "", shown: "1999" },   // cleared on purpose is an edit too
  ];
  assert.deepEqual(unsavedEdits(fields), [
    { row: "v1", name: "title", value: "Der W zwo drei (D2 edit)" },
    { row: "", name: "year", value: "" },
  ]);
});

test("nothing typed, nothing kept", () => {
  assert.deepEqual(unsavedEdits([]), []);
  assert.deepEqual(unsavedEdits([{ row: "a", name: "artist", value: "X", shown: "X" }]), []);
});

test("a moved album's new id comes from the job", async () => {
  const { movedTo } = await import("../../src/noaap/webui/logic.mjs");
  assert.equal(movedTo({ result: [{ plan: { source_id: "/m/A Band/An Album" } }] }, "/m/a band/an album"), "/m/A Band/An Album");
  assert.equal(movedTo({ result: [{ plan: { source_id: "/m/x" } }] }, "/m/x"), null);
  assert.equal(movedTo(null, "/m/x"), null);
});
