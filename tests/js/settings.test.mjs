// What the settings view offers, and what it refuses before anything is sent (§9, slice 92).
import { test } from "node:test";
import assert from "node:assert/strict";

import { TIMING_FIELDS, clearedSource, dialogFields, folderSummary, removeConfirm, sourceRows,
         sourceSummary, takeInState, timingFields, watchTrouble, watchesAfter, watchesWithout }
  from "../../src/noaap/webui/logic.mjs";

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

// -- 6. sources as a list, and a dialog per provider (§9, slice 95) ------------------------------

// what the server answers: every provider that *can* be set up, with the fields it has
const PATREON_SET = { cookies_from_browser: "firefox", cookies_file: "", audio_from_video: true,
                      captions: false, post_cap: 200 };
const NOTHING = { cookies_from_browser: "", cookies_file: "" };

test("nothing set up is a list with no rows and everything on offer", () => {
  const said = sourceRows({ patreon: { ...NOTHING, audio_from_video: false, captions: false, post_cap: 200 },
                            soundcloud: { ...NOTHING } });
  assert.equal(said.none, true);
  assert.deepEqual(said.rows, []);
  assert.deepEqual(said.unset, ["patreon", "soundcloud"]);
});

test("one source set up is one row, and the other is still on offer", () => {
  const said = sourceRows({ patreon: PATREON_SET, soundcloud: NOTHING });
  assert.equal(said.rows.length, 1);
  assert.equal(said.rows[0].label, "Patreon");
  assert.equal(said.rows[0].summary, "session from Firefox · audio taken out of video posts");
  assert.deepEqual(said.unset, ["soundcloud"], "add offers only what is not set up");
});

test("several sources set up are several rows, in a settled order", () => {
  const said = sourceRows({ soundcloud: { cookies_from_browser: "chrome", cookies_file: "" },
                            patreon: PATREON_SET });
  assert.deepEqual(said.rows.map((r) => r.label), ["Patreon", "SoundCloud"]);
  assert.deepEqual(said.unset, []);
  assert.equal(said.rows[1].summary, "session from Chrome");
});

test("a default that nobody chose is not a setting", () => {
  // `post_cap` is 200 out of the box, and the page never offers it: it cannot make a row
  const said = sourceRows({ patreon: { ...NOTHING, audio_from_video: false, captions: false, post_cap: 200 } });
  assert.equal(said.none, true);
});

test("a switch without a session is a row, and says what it can do", () => {
  const said = sourceRows({ patreon: { ...NOTHING, captions: true } });
  assert.equal(said.rows.length, 1);
  assert.equal(said.rows[0].summary, "no session — public posts only · captions kept as lyrics");
});

test("a cookies file reads as a session without naming the file", () => {
  assert.equal(sourceSummary({ cookies_from_browser: "", cookies_file: "/home/u/c.txt" }),
               "session from a cookies file");
  assert.equal(sourceSummary({ cookies_from_browser: "firefox:dev", cookies_file: "/home/u/c.txt" }),
               "session from Firefox and from a cookies file");
});

test("a dialog holds its own provider's fields and nobody else's", () => {
  const sources = { patreon: PATREON_SET, soundcloud: NOTHING };
  assert.deepEqual(dialogFields("patreon", sources),
                   ["cookies_from_browser", "cookies_file", "audio_from_video", "captions"]);
  assert.deepEqual(dialogFields("soundcloud", sources), ["cookies_from_browser", "cookies_file"]);
  assert.deepEqual(dialogFields("nobody", sources), []);
});

test("removing a source clears that provider and touches no other", () => {
  const sources = { patreon: PATREON_SET, soundcloud: { cookies_from_browser: "chrome", cookies_file: "" } };
  const cleared = clearedSource("patreon", sources);
  assert.deepEqual(cleared, { patreon_cookies_from_browser: "", patreon_cookies_file: "",
                              patreon_audio_from_video: false, patreon_captions: false });
  assert.ok(Object.keys(cleared).every((k) => k.startsWith("patreon_")));
  // soundcloud has two fields, so clearing it sends two
  assert.deepEqual(Object.keys(clearedSource("soundcloud", sources)),
                   ["soundcloud_cookies_from_browser", "soundcloud_cookies_file"]);
});

test("a removal says what it clears, and what still works afterwards", () => {
  const said = removeConfirm("patreon", { patreon: PATREON_SET });
  assert.match(said, /Remove Patreon's settings\?/);
  assert.match(said, /the session from Firefox/);
  assert.match(said, /taking the audio out of video posts/);
  assert.match(said, /still works for anything that needs no login/);
  assert.doesNotMatch(said, /cookies file/, "there is none set, so it is not mentioned");
});


// -- 7. a watched folder is a source too (§9, slice 98) -----------------------------------------
//
// The user: *"intake and watch folders are sources as well, and there might even be multiple of
// those."* A provider is at most one row; a folder is a row each.

const DROP = { name: "drop", folder: "/mnt/nas/incoming", shape: "intake", there: true,
               looked: "2026-09-30 21:04", waiting: 0 };
const ITSELF = { name: "itself", folder: "/home/u/Music", shape: "library", there: true,
                 looked: null, waiting: 0 };
const GONE = { name: "old", folder: "/mnt/usb/in", shape: "intake", there: false, looked: null, waiting: 3 };

test("no watched folder is no row, and a provider is still one", () => {
  const said = sourceRows({ patreon: PATREON_SET }, []);
  assert.deepEqual(said.rows.map((r) => r.kind), ["provider"]);
  assert.equal(said.folders, 0);
});

test("one watched folder is one row beside the provider", () => {
  const said = sourceRows({ patreon: PATREON_SET }, [DROP]);
  assert.deepEqual(said.rows.map((r) => [r.kind, r.label]), [["provider", "Patreon"], ["folder", "drop"]]);
  assert.equal(said.rows[1].summary,
               "what is dropped here is taken in · /mnt/nas/incoming · last looked at 2026-09-30 21:04");
  assert.equal(said.rows[1].index, 0, "and it knows which watch it is");
});

test("three watched folders are three rows, in the config's order", () => {
  const said = sourceRows({ patreon: PATREON_SET }, [DROP, ITSELF, GONE]);
  assert.equal(said.folders, 3);
  assert.deepEqual(said.rows.map((r) => r.label), ["Patreon", "drop", "itself", "old"]);
  assert.deepEqual(said.rows.filter((r) => r.kind === "folder").map((r) => r.index), [0, 1, 2]);
});

test("a row says what the watcher knows about its folder", () => {
  assert.match(folderSummary(ITSELF), /^this library, watching itself/);
  assert.match(folderSummary(ITSELF), /not looked at yet/);
  assert.match(folderSummary(GONE), /the folder is not there/);
  assert.match(folderSummary(GONE), /3 arrival\(s\) it could not hand over/);
  assert.doesNotMatch(folderSummary(GONE), /not looked at yet/, "a folder that is gone says that first");
});

test("editing one folder touches no other", () => {
  const after = watchesAfter([DROP, ITSELF], 0, { name: "drop", folder: "/mnt/nas/in2", shape: "intake" });
  assert.deepEqual(after[0], { name: "drop", folder: "/mnt/nas/in2", shape: "intake" });
  assert.deepEqual(after[1], { name: "itself", folder: "/home/u/Music", shape: "library" });
  assert.equal(after.length, 2);
});

test("a folder can always be added, however many there are", () => {
  let watches = [];
  for (const n of [1, 2, 3]) {
    watches = watchesAfter(watches, null, { name: `n${n}`, folder: `/m/${n}` });
    assert.equal(watches.length, n);
  }
  assert.deepEqual(watches.map((w) => w.shape), ["intake", "intake", "intake"], "intake unless said otherwise");
});

test("removing one folder removes that one", () => {
  assert.deepEqual(watchesWithout([DROP, ITSELF, GONE], 1).map((w) => w.name), ["drop", "old"]);
  assert.deepEqual(watchesWithout([DROP], 0), []);
  assert.deepEqual(watchesWithout([DROP], 5).map((w) => w.name), ["drop"], "an index that is not there changes nothing");
});

test("the dialog's rules are the config's rules, over the whole set", () => {
  const library = "/home/u/Music";
  // a new folder inside the library, added as intake
  const bad = watchesAfter([DROP], null, { name: "in", folder: `${library}/incoming` });
  assert.match(watchTrouble(bad, library)[0], /may not hold the library/);
  // …and as the library shape it is exactly what that shape is for
  const good = watchesAfter([DROP], null, { name: "in", folder: library, shape: "library" });
  assert.deepEqual(watchTrouble(good, library), []);
  // a name another folder already has
  const twice = watchesAfter([DROP], null, { name: "drop", folder: "/mnt/other" });
  assert.match(watchTrouble(twice, library)[0], /share that name/);
  // and one nested in another
  const nested = watchesAfter([DROP], null, { name: "inner", folder: "/mnt/nas/incoming/more" });
  assert.match(watchTrouble(nested, library)[0], /may not be nested/);
});
