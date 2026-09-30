// What the settings view offers, and what it refuses before anything is sent (§9, slice 92).
import { test } from "node:test";
import assert from "node:assert/strict";

import { TIMING_FIELDS, takeInState, timingFields, watchTrouble } from "../../src/noaap/webui/logic.mjs";

// -- 1. a field is shown only when the selected provider uses it ---------------------------------

test("nothing selected asks for nothing", () => {
  const said = timingFields({ align: "none", draft: "none" });
  assert.deepEqual(said.fields, []);
  assert.deepEqual(said.keys, []);
  assert.equal(said.shows("endpoint"), false);
});

test("local asks where it runs and whether it checks itself, and for no endpoint or key", () => {
  const said = timingFields({ align: "local", draft: "local" });
  assert.deepEqual(said.fields.sort(), ["device", "verify"]);
  assert.equal(said.shows("endpoint"), false);
  assert.deepEqual(said.keys, []);
});

test("http asks for the endpoint and nothing else", () => {
  const said = timingFields({ align: "http", draft: "none" });
  assert.deepEqual(said.fields, ["endpoint"]);
  assert.equal(said.shows("device"), false);
  assert.equal(said.shows("verify"), false);
});

test("a vendor asks for its own key and not the other's", () => {
  const said = timingFields({ align: "elevenlabs", draft: "none" });
  assert.deepEqual(said.keys, ["elevenlabs"]);
  assert.equal(said.showsKey("elevenlabs"), true);
  assert.equal(said.showsKey("deepgram"), false);
  assert.deepEqual(said.fields, []);
});

test("the two slots are asked for together, each once", () => {
  const said = timingFields({ align: "local", draft: "deepgram" });
  assert.deepEqual(said.fields.sort(), ["device", "verify"]);
  assert.deepEqual(said.keys, ["deepgram"]);
  const both = timingFields({ align: "deepgram", draft: "deepgram" });
  assert.deepEqual(both.keys, ["deepgram"]);          // one key, asked for once
});

test("an unset slot is `none`, not a missing provider", () => {
  assert.deepEqual(timingFields({}).fields, []);
  assert.deepEqual(timingFields({ align: "", draft: "" }).fields, []);
  assert.deepEqual(timingFields({ align: "made up" }).fields, []);
});

test("every provider the page can offer has an answer", () => {
  for (const kind of ["none", "local", "http", "elevenlabs", "deepgram"]) {
    assert.ok(Array.isArray(TIMING_FIELDS[kind]), kind);
  }
});

// -- 3. taking a folder in: check, then apply exactly that --------------------------------------

test("a folder has to be named, in full", () => {
  assert.equal(takeInState({ folder: "" }).canCheck, false);
  assert.match(takeInState({ folder: "" }).note, /Name a folder/);
  assert.equal(takeInState({ folder: "Music" }).canCheck, false);
  assert.match(takeInState({ folder: "Music" }).note, /from the root/);
  assert.equal(takeInState({ folder: "/mnt/nas/Music" }).canCheck, true);
});

test("apply waits for a check of this very folder, in this very mode", () => {
  const check = { folder: "/mnt/nas/Music", mode: "merge", lines: ["  would take: one"] };
  const said = takeInState({ folder: "/mnt/nas/Music", mode: "merge", check });
  assert.equal(said.canApply, true);
  assert.equal(said.matches, true);
  assert.match(said.note, /1 line/);
  // another folder, or the other way of taking it in, is not what that check was about
  for (const other of [{ folder: "/mnt/other", mode: "merge" }, { folder: "/mnt/nas/Music", mode: "adopt" }]) {
    const then = takeInState({ ...other, check });
    assert.equal(then.canApply, false);
    assert.equal(then.matches, false, "and its lines are not shown for another question");
  }
});

test("a check that found nothing has nothing to apply", () => {
  const check = { folder: "/mnt/nas/Music", mode: "adopt", lines: [] };
  const said = takeInState({ folder: "/mnt/nas/Music", mode: "adopt", check });
  assert.equal(said.canApply, false);
  assert.match(said.note, /found nothing/);
});

test("merge says the other folder is not written to; adopt does not claim that", () => {
  assert.match(takeInState({ folder: "/mnt/nas/Music", mode: "merge" }).note, /never written to/);
  assert.doesNotMatch(takeInState({ folder: "/mnt/nas/Music", mode: "adopt" }).note, /never written to/);
});

test("a write already running is waited for, not raced", () => {
  const said = takeInState({ folder: "/mnt/nas/Music", running: true });
  assert.equal(said.canCheck, false);
  assert.equal(said.canApply, false);
  assert.match(said.note, /writing to the library/);
});

// -- 4. watched folders: the same rules the config applies, said here ---------------------------

test("a watched folder needs a name and an absolute path", () => {
  assert.deepEqual(watchTrouble([{ name: "nas", folder: "/mnt/nas/in" }], "/home/u/Music"), []);
  assert.match(watchTrouble([{ name: "nas", folder: "incoming" }])[0], /not an absolute path/);
  assert.match(watchTrouble([{ name: "", folder: "/mnt/nas/in" }])[0], /give it a name/);
  assert.match(watchTrouble([{ name: "nas", folder: "" }])[0], /name the folder/);
});

test("two watched folders may not share a name", () => {
  const trouble = watchTrouble([{ name: "a", folder: "/m/x" }, { name: "a", folder: "/m/y" }]);
  assert.deepEqual(trouble, ["a: two watched folders share that name"]);
});

test("an intake folder may not be the library, hold it, or sit inside it", () => {
  const lib = "/home/u/Music";
  for (const folder of [lib, "/home/u", `${lib}/incoming`]) {
    assert.match(watchTrouble([{ name: "in", folder, shape: "intake" }], lib)[0],
                 /may not hold the library/, folder);
  }
  // the library watching itself is the other shape, and is how it is done
  assert.deepEqual(watchTrouble([{ name: "lib", folder: lib, shape: "library" }], lib), []);
});

test("watched folders may not be nested", () => {
  const trouble = watchTrouble([{ name: "outer", folder: "/m" }, { name: "inner", folder: "/m/in" }]);
  assert.deepEqual(trouble, ["inner: lies inside outer; watched folders may not be nested"]);
  // a trailing slash is the same folder, not a different one
  assert.equal(watchTrouble([{ name: "a", folder: "/m/" }, { name: "b", folder: "/m" }]).length, 2);
});

test("nothing watched is not trouble", () => {
  assert.deepEqual(watchTrouble([], "/home/u/Music"), []);
  assert.deepEqual(watchTrouble(), []);
});
