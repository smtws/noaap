// What typing in the search field costs (DESIGN §9, slice 151; R-523).
//
// The user: the field was unresponsive, letters not taken for seconds. The library is 1,523 albums
// and 20,261 tracks, and `shownAlbums()` folded every album line and every track title, in both
// spellings, each time it was asked — while `renderLibrary` asked six times per keystroke and then
// serialised the whole filtered list to compare it with the last one.
//
// These cases hold the two things that made it slow: folding must happen once per text rather than
// once per keystroke, and matching must not need the per-character map that highlighting needs.
import { test } from "node:test";
import assert from "node:assert/strict";

import { foldMap, foldedBoth, hits, hitsIn, maps, searchTerms } from "../../src/noaap/webui/logic.mjs";

test("the folded keys say exactly what the old matcher said", () => {
  const lines = ["Knüppel aus dem Sack", "Mötley Crüe — Dr. Feelgood", "AC/DC – Thunderstruck",
                 "Njǫrð", "Þrúðheimr", "Blöde Frage, Saufgelage", "1989"];
  for (const line of lines) {
    for (const typed of ["knueppel", "knuppel", "motley", "crue", "thunder", "njord",
                         "thrudheimr", "saufgelage", "1989", "zzz", "dr feelgood"]) {
      const terms = searchTerms(typed);
      assert.equal(hitsIn(terms, foldedBoth(line)), hits(terms, line),
        `“${typed}” against “${line}”`);
    }
  }
});

test("an empty search matches everything, as every-of-none does", () => {
  assert.equal(hitsIn(searchTerms(""), foldedBoth("anything")), true);
  assert.equal(searchTerms("   ").length, 0);
});

test("all the terms have to be found, not just one", () => {
  const keys = foldedBoth("Mötley Crüe — Dr. Feelgood");
  assert.ok(hitsIn(searchTerms("motley feelgood"), keys));
  assert.ok(!hitsIn(searchTerms("motley nonesuch"), keys));
});

test("both spellings are kept, so a keyboard without umlauts still finds it", () => {
  const [plain, german] = foldedBoth("Knüppel");
  assert.equal(plain, "knuppel");
  assert.equal(german, "knueppel");
});

test("matching does not build the map that highlighting needs", () => {
  // `maps` carries a `from` array per character; that is most of the cost of folding and a filter
  // never reads it. If this ever regresses, typing pays for highlighting it does not do.
  const both = maps("Knüppel");
  assert.ok(both[0].from.length > 0, "maps still carries the per-character trail");
  const keys = foldedBoth("Knüppel");
  assert.deepEqual(keys, [both[0].folded, both[1].folded], "the same two spellings");
  assert.deepEqual(keys.map((k) => typeof k), ["string", "string"],
    "plain strings — no per-character trail to build or carry");
});

test("folding a library's worth of text once is cheap enough to do on a keystroke", () => {
  // The real shape: 1,523 albums and 20,261 tracks. Folded once into keys, then one letter typed
  // only runs `includes` over them. The budget is deliberately loose — this guards the complexity,
  // not the machine.
  const albums = Array.from({ length: 1523 }, (_, i) => `Artist ${i} Album ${i} ${1970 + (i % 50)}`);
  const tracks = Array.from({ length: 20261 }, (_, i) => `Artist ${i % 1523} Song Number ${i}`);

  const keys = [...albums, ...tracks].map(foldedBoth);          // once, when the library changes

  const started = process.hrtime.bigint();
  for (const typed of ["s", "so", "son", "song", "song 1", "song 12"]) {
    const terms = searchTerms(typed);
    let found = 0;
    for (const k of keys) if (hitsIn(terms, k)) found++;
    assert.ok(found > 0, `“${typed}” finds something`);
  }
  const perKeystroke = Number(process.hrtime.bigint() - started) / 1e6 / 6;
  assert.ok(perKeystroke < 50, `${perKeystroke.toFixed(1)} ms per keystroke over 21,784 keys`);
});

test("folding the same text on every keystroke is what was slow", () => {
  // the shape of the old code, at a hundredth of the size, to show the difference is in the
  // folding and not in the matching
  const lines = Array.from({ length: 218 }, (_, i) => `Artist ${i} Song Number ${i}`);
  const terms = searchTerms("song 1");

  const foldEachTime = process.hrtime.bigint();
  for (let round = 0; round < 6; round++) for (const line of lines) hits(terms, line);
  const folding = Number(process.hrtime.bigint() - foldEachTime) / 1e6;

  const keys = lines.map(foldedBoth);
  const readyKeys = process.hrtime.bigint();
  for (let round = 0; round < 6; round++) for (const k of keys) hitsIn(terms, k);
  const matching = Number(process.hrtime.bigint() - readyKeys) / 1e6;

  assert.ok(matching * 5 < folding,
    `matching ready keys (${matching.toFixed(2)} ms) must be far under folding every time ` +
    `(${folding.toFixed(2)} ms) — a hundredth of the user's library, six renders`);
});
