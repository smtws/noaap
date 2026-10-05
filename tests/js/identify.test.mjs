import assert from "node:assert/strict";
import { test } from "node:test";

import { identifyLines, identifyState } from "../../src/noaap/webui/logic.mjs";

test("with nothing looked up yet it offers to look, and writes nothing", () => {
  const s = identifyState({});
  assert.equal(s.canCheck, true);
  assert.equal(s.canApply, false);
  assert.match(s.note, /writes nothing yet/);
});

test("a pinned release is named as the thing it will ask about", () => {
  assert.match(identifyState({ pinned: true }).note, /the release you pinned/);
  assert.match(identifyState({ pinned: false }).note, /which release this album is/);
});

test("nothing may be done while the library is being written to", () => {
  const s = identifyState({ running: true, check: { lines: ["01 would be retagged"] } });
  assert.equal(s.canCheck, false);
  assert.equal(s.canApply, false);
});

test("a check with changes may be applied, and says how many", () => {
  const s = identifyState({ check: { lines: ["01 would be retagged", "02 would be renamed"] } });
  assert.equal(s.canApply, true);
  assert.match(s.note, /exactly what is listed: 2 change\(s\)/);
});

test("a check that found nothing tells which kind of nothing it was", () => {
  assert.match(identifyState({ check: { lines: [], matched: true } }).note, /agrees with what/);
  assert.match(identifyState({ check: { lines: [], matched: false } }).note, /no release that matches/);
});

test("a check from before the album changed may not be applied", () => {
  const s = identifyState({ version: "bbb", check: { version: "aaa", lines: ["x"] } });
  assert.equal(s.stale, true);
  assert.equal(s.canApply, false);
  assert.match(s.note, /look again before applying/);
});

test("a check of the same version is not stale", () => {
  const s = identifyState({ version: "aaa", check: { version: "aaa", lines: ["x"] } });
  assert.equal(s.stale, false);
  assert.equal(s.canApply, true);
});

test("the lines are the server's own, without the album header or its indent", () => {
  const job = { log: ["=== DOMINUM — Cult", "  01 would be retagged: album nothing → “Cult”", "", "  a cover would be saved"] };
  assert.deepEqual(identifyLines(job), ["01 would be retagged: album nothing → “Cult”",
                                        "a cover would be saved"]);
  assert.deepEqual(identifyLines(null), []);
});
