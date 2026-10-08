// The block a draft says may not be the song (DESIGN §9, slice 159; R-535).
//
// On a live track the transcriber is right and the draft is wrong: after the song it writes down
// the singer thanking the audience and introducing the band. The server marks it; nothing is
// dropped until somebody presses the button.
import { test } from "node:test";
import assert from "node:assert/strict";

import { draftText, talkBlock, withoutTalk } from "../../src/noaap/webui/logic.mjs";

// what the server sends for `Schandmaul/Anderswelt (Live)`
const LIVE = {
  lines: [
    { text: "Zwischen aller Zeiten, zwischen Weltes schlägt mein Herz.", start: 100.31 },
    { text: "Himmelswärts,", start: 104.64 },
    { text: "… after 145 s of silence — maybe talk, not lyrics", start: 109.92 },
    { text: "Dankeschön.", start: 254.79 },
    { text: "Und weiter geht's mit den Gästen.", start: 257.75 },
  ],
  parameters: { maybe_talk_from: "254.79", maybe_talk_lines: "2" },
};

const STUDIO = { lines: [{ text: "Meine Braut sollst du sein,", start: 72.17 }], parameters: {} };

test("a marked draft says how many lines it doubts and offers to drop them", () => {
  const said = talkBlock(LIVE);

  assert.equal(said.lines, 2);
  assert.equal(said.from, 254.79);
  assert.match(said.note, /come after a long silence/);
  assert.match(said.action, /Drop the 2 lines after the silence/);
});

test("one doubted line is said in the singular", () => {
  const one = talkBlock({ parameters: { maybe_talk_from: "100", maybe_talk_lines: "1" } });
  assert.match(one.note, /The last 1 line come/);
  assert.match(one.action, /Drop the 1 line after the silence/);
});

test("a draft with nothing to doubt offers nothing", () => {
  assert.equal(talkBlock(STUDIO), null);
  assert.equal(talkBlock({ parameters: { maybe_talk_lines: "0" } }), null);
  assert.equal(talkBlock(null), null);
  assert.equal(talkBlock({}), null);
});

test("dropping takes the block and its marker, and nothing above them", () => {
  const text = draftText(LIVE);
  const left = withoutTalk(text, LIVE);

  assert.match(text, /Dankeschön/, "it is in the draft to begin with");
  assert.doesNotMatch(left, /Dankeschön/);
  assert.doesNotMatch(left, /maybe talk/, "the marker goes with the block it marked");
  assert.match(left, /Himmelswärts/, "and the song is untouched");
  assert.equal(left.split("\n").length, 2);
});

test("dropping nothing is the text as it was", () => {
  const text = draftText(STUDIO);
  assert.equal(withoutTalk(text, STUDIO), text);
  assert.equal(withoutTalk(text, null), text);
});

test("a draft whose marker a person has already edited away is left alone", () => {
  // they deleted the marker line by hand; dropping must not then cut at the wrong place
  const edited = "Himmelswärts,\nDankeschön.";
  assert.equal(withoutTalk(edited, LIVE), edited);
});

test("the stamps a draft carries survive the drop", () => {
  const stamped = { ...LIVE, lines: LIVE.lines.map((l) => ({ ...l })) };
  const left = withoutTalk(draftText(stamped), stamped);
  assert.match(left, /\[01:40\.3\]/, "the first line keeps its own stamp");
});
