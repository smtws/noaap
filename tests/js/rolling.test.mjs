// The rolling lyrics list: where it must scroll so the line being sung is in view (§9, slice 68).
//
// The bug this holds: `offsetTop` is measured from the nearest positioned ancestor, which inside a
// table is the `td` — not the scrolling box. Measured on the installed page, the editor's preview box
// starts **645 px** below that `td`, so every step scrolled 645 px (28 lines) too far and the active
// line was never in view. The arithmetic is here; the page passes a top relative to the list's content.
import assert from "node:assert/strict";
import { test } from "node:test";

import { scrollForActive } from "../../src/noaap/webui/logic.mjs";

// the editor's preview, as measured: 174 px of box, 23 px lines, 4067 px of content
const editor = { boxHeight: 174, contentHeight: 4067, lineHeight: 23 };

test("a line below the view is centred", () => {
  const want = scrollForActive({ ...editor, boxScroll: 0, lineTop: 900 });
  assert.equal(want, Math.round(900 - (174 - 23) / 2));
  // the property that matters, and the one the user asked for: the line is *inside* the box
  assert.ok(want <= 900 && 900 + 23 <= want + 174, "and it is fully in view afterwards");
});

test("a line above the view is centred too", () => {
  assert.equal(scrollForActive({ ...editor, boxScroll: 2000, lineTop: 300 }), 300 - 75);
});

test("a line already in view with a line's margin is left alone", () => {
  // box 174 high from 800: comfortable band is 823..951
  assert.equal(scrollForActive({ ...editor, boxScroll: 800, lineTop: 860 }), null);
});

test("but one inside the view yet against its edge is brought in", () => {
  assert.equal(scrollForActive({ ...editor, boxScroll: 800, lineTop: 805 }), 805 - 75,
               "less than a line from the top edge is not comfortable");
  assert.equal(scrollForActive({ ...editor, boxScroll: 800, lineTop: 800 + 174 - 23 - 5 }), 946 - 75);
});

test("the first line does not scroll above the start", () => {
  assert.equal(scrollForActive({ ...editor, boxScroll: 500, lineTop: 0 }), 0);
});

test("the last line does not scroll past the end", () => {
  const last = 4067 - 23;
  assert.equal(scrollForActive({ ...editor, boxScroll: 0, lineTop: last }), 4067 - 174);
});

test("a box shorter than three lines centres instead of keeping margins", () => {
  const tiny = { boxHeight: 40, contentHeight: 400, lineHeight: 23, boxScroll: 0 };
  assert.equal(scrollForActive({ ...tiny, lineTop: 100 }), Math.round(100 - (40 - 23) / 2));
});

test("nothing to decide without a box or a line", () => {
  assert.equal(scrollForActive({ boxHeight: 0, boxScroll: 0, contentHeight: 0, lineTop: 0, lineHeight: 23 }), null);
  assert.equal(scrollForActive({ ...editor, boxScroll: 0, lineTop: 0, lineHeight: 0 }), null);
});

test("a list that does not scroll at all is left alone", () => {
  const short = { boxHeight: 200, contentHeight: 120, lineHeight: 23, boxScroll: 0 };
  assert.equal(scrollForActive({ ...short, lineTop: 90 }), null);
});

test("the mistake it replaces, in the numbers that were measured", () => {
  // what the page used to compute for line 10 of the editor's preview, and what it should have
  const lineTop = 166 + Math.round((174 - 23) / 2);     // the line's true top in the content
  const asCoded = lineTop + 645 - 174 / 2 + 23 / 2;      // offsetTop was 645 px too large
  const want = scrollForActive({ ...editor, boxScroll: 0, lineTop });
  assert.ok(Math.abs(asCoded - want) > 600, "the old arithmetic missed by 28 lines");
  assert.ok(want <= lineTop && lineTop + 23 <= want + 174, "the new one puts the line inside the box");
});
