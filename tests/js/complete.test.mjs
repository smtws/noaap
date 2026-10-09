// §9, slice 162: what the "Complete from the release" dialog offers.
import { test } from "node:test";
import assert from "node:assert/strict";

import { completeRows } from "../../src/noaap/webui/logic.mjs";

test("a hit is ticked, a video and a miss cannot be", () => {
  const rows = completeRows([
    { name: "1-06", title: "Für Dich", length: 256, video: false,
      hits: [{ ref: "sA5e", title: "Letzte Instanz Heilig 06 Fur Dich", channel: "IronClad", off: 0.4 }] },
    { name: "1-13", title: "When Love Goes Wrong", length: null, video: false, hits: [] },
    { name: "3-01", title: "Extras", length: null, video: true, hits: [{ ref: "v", title: "x" }] },
  ]);
  assert.deepEqual(rows.map((r) => [r.name, r.can, r.ref]), [["1-06", true, "sA5e"], ["1-13", false, null], ["3-01", false, "v"]]);
  assert.equal(rows[0].title, "1-06 Für Dich (4:16)");
  assert.equal(rows[0].why, "“Letzte Instanz Heilig 06 Fur Dich” by IronClad, +0 s");
  assert.equal(rows[1].why, "nothing close enough was found");
  assert.equal(rows[2].why, "a video on the release — not completed");
});
