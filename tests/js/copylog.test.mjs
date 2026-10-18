import assert from "node:assert/strict";
import { test } from "node:test";

import { COPIED, COPY_FAILED, copyLog, logText } from "../../src/noaap/webui/logic.mjs";

// q38, R-764: the expanded log of a job gets a button that copies every line, as shown
const JOB = { id: 7, log: ["=== [1/1] Disturbed — Immortalized", "  11 The Sound of Silence", "outcome ok "] };

test("the copied text is every line of the log, as the expanded view shows it", async () => {
  const board = { text: null, async writeText(t) { this.text = t; } };
  assert.equal(await copyLog(logText(JOB), board), COPIED);
  assert.equal(board.text, "=== [1/1] Disturbed — Immortalized\n  11 The Sound of Silence\noutcome ok ");
});

test("the confirmation says when the clipboard refuses", async () => {
  const board = { async writeText() { throw new Error("NotAllowedError"); } };
  assert.equal(await copyLog("x", board), COPY_FAILED);
});

test("over plain http there is no clipboard API, and the select-and-copy is asked", async () => {
  const asked = [];
  assert.equal(await copyLog("one\ntwo", undefined, (t) => { asked.push(t); return true; }), COPIED);
  assert.deepEqual(asked, ["one\ntwo"]);
  assert.equal(await copyLog("one", undefined, () => false), COPY_FAILED);
  assert.equal(await copyLog("one", undefined), COPY_FAILED);
});

test("a job without a log copies nothing, not 'undefined'", () => {
  assert.equal(logText({ id: 1 }), "");
  assert.equal(logText(null), "");
});

test("the confirmation is short and visible", () => {
  assert.ok(COPIED.length <= 10 && COPY_FAILED.length <= 12);
});
