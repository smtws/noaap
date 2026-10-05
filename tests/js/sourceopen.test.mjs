import assert from "node:assert/strict";
import { test } from "node:test";

import { sourceOpen } from "../../src/noaap/webui/logic.mjs";

test("a YouTube album gets a link, named after YouTube", () => {
  const got = sourceOpen({ source_url: "https://www.youtube.com/playlist?list=OLAK5uy_abc",
                           provider: "youtube" });
  assert.deepEqual(got, { href: "https://www.youtube.com/playlist?list=OLAK5uy_abc",
                          label: "open on YouTube" });
});

test("another provider's link is named after that provider, not YouTube", () => {
  assert.equal(sourceOpen({ source_url: "https://soundcloud.com/x/sets/y", provider: "soundcloud" }).label,
               "open on SoundCloud");
  assert.equal(sourceOpen({ source_url: "https://www.patreon.com/posts/123", provider: "patreon" }).label,
               "open on Patreon");
});

test("an album adopted from a folder is words, not a link", () => {
  const got = sourceOpen({ source_url: "/mnt/speicherzwerg-nfs/c/Musik/Apocalyptica/Cult",
                           provider: "folder" });
  assert.equal(got.href, undefined, "a path is not something a browser opens");
  assert.equal(got.text, "taken in from /mnt/speicherzwerg-nfs/c/Musik/Apocalyptica/Cult");
});

test("a relative folder address is words too", () => {
  assert.deepEqual(sourceOpen({ source_url: "./", provider: "folder" }),
                   { text: "taken in from ./" });
});

test("no source at all shows nothing", () => {
  assert.equal(sourceOpen({ source_url: "", provider: "folder" }), null);
  assert.equal(sourceOpen({}), null);
  assert.equal(sourceOpen(), null);
});

test("an unknown provider with a real URL still gets a link", () => {
  const got = sourceOpen({ source_url: "https://example.com/x", provider: "" });
  assert.equal(got.label, "open on the source");
});

test("a file:// address is not offered as a link", () => {
  assert.equal(sourceOpen({ source_url: "file:///mnt/music/x", provider: "folder" }).href, undefined);
});
