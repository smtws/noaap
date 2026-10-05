// The part of the page that is arithmetic rather than DOM: matching text, reading lengths,
// arranging tracks, deciding what a panel offers. It is a module so `node --test` can import it
// (tests/js/), and the page imports it with `<script type="module">` — no build step either way.
//
// Nothing in here touches `document`. Where a rule has a twin in Python, the twin is named in the
// comment and `tests/shared/*.json` holds the table both sides are tested against, so the two
// cannot drift apart (DESIGN.md §9, slice 33).

// -- text: folding for the library filter ------------------------------------------------

const LETTERS = { ð: "d", þ: "th", ø: "o", æ: "ae", œ: "oe", ß: "ss", ł: "l", đ: "d", ŋ: "n", ʒ: "z" };

// Folds one character at a time and remembers where each folded character came from, so a
// match can be pointed back at the original text ("ü" -> "ue" is two characters from one).
export function foldMap(text, german) {
  let folded = "";
  const from = [];
  for (let i = 0; i < (text || "").length; i++) {
    let c = text[i].normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
    if (german && (c === "a" || c === "o" || c === "u") && text[i].normalize("NFD").length > 1) c += "e";
    c = c.replace(/[ðþøæœßłđŋʒ]/g, (x) => LETTERS[x]).replace(/[^\p{L}\p{N}]+/gu, " ");
    for (const _ of c) from.push(i);
    folded += c;
  }
  return { folded, from };
}

export const fold = (s) => foldMap(s, false).folded;

// German keyboards without umlauts write "knueppel" for "Knüppel", which folding to
// "knuppel" would miss — so every string is matched (and highlighted) in both spellings.
export const maps = (text) => [foldMap(text, false), foldMap(text, true)];

export const hits = (terms, text) => {
  const both = maps(text);
  return terms.every((term) => both.some((m) => m.folded.includes(term)));
};

// -- time ---------------------------------------------------------------------------------

export const asTime = (seconds) => {
  if (seconds == null) return "";
  const rest = seconds % 60;
  const shown = Number.isInteger(rest) ? String(rest).padStart(2, "0") : rest.toFixed(1).padStart(4, "0");
  return `${Math.floor(seconds / 60)}:${shown}`;
};

export const fmt = (sec) =>
  Number.isFinite(sec) ? `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, "0")}` : "0:00";

// -- lengths: the ⏱ chip and the trim target ----------------------------------------------

// Mirrors plan.py's LENGTH_SLACK / LENGTH_BIG / LENGTH_STUB — change a number there and here.
export const LENGTH = { slack: 5, big: 20, stub: 0.6 };

export const refLength = (t) => t.mb_length || t.lyrics_length || null;
export const ourLength = (t) => t.file_length || (t.duration ? (t.trim_end || t.duration) - (t.trim_start || 0) : null);

// What the pending marks would leave, and how far that is from the length the song is said to
// be. Twin of plan.trimmed_gap; tests/shared/trim_target.json is the table both are tested on.
export function trimTarget(t, total, start, end) {
  if (!total) return { kept: null, gap: null, ref: null };
  const kept = (end == null ? total : Math.min(end, total)) - (start || 0);
  const ref = refLength(t);
  return { kept, gap: ref ? kept - ref : null, ref };
}

// Which band a length gap falls in, which is the chip's colour and the target line's.
export function lengthBand(kept, ref) {
  if (!ref || kept == null) return null;
  if (kept < ref * LENGTH.stub) return "stub";
  const off = Math.abs(kept - ref);
  return off > LENGTH.big ? "big" : off > LENGTH.slack ? "slack" : "close";
}

// A mark is a tenth of a second: `currentTime` carries a dozen decimals of mouse precision that
// mean nothing musically and would end up in the plan and on ffmpeg's command line.
export const roundMark = (seconds, total) => Math.round(Math.min(Math.max(seconds, 0), total) * 10) / 10;

// A typed mark is refused when it would cross the other one — the start cannot sit after the end.
export function markedTrim({ start, end }, which, value, total) {
  if (which === "start") return { start: value >= (end ?? total) ? start : value || null, end };
  return { start, end: value <= (start || 0) ? end : value >= total ? null : value };
}

// -- arranging tracks ----------------------------------------------------------------------

// Each disc counts from 1 again, so the position column never shows two 3s mid-edit. Twin of
// service.arrange (which does the same to a saved plan).
export function numberByDisc(discs) {
  const counts = new Map();
  return discs.map((d) => {
    const disc = Number(d) || 1;
    counts.set(disc, (counts.get(disc) || 0) + 1);
    return counts.get(disc);
  });
}

// A row dropped among another disc's rows joins that disc, at that position. `rows` is
// [{id, disc}] in the order they stand; the result is the new order, with the moved row's disc
// taken from whichever neighbour it now sits beside (the one above, or the one below when it
// landed first). What the server then does with it is service.placed (§9, slice 32).
export function movedRow(rows, id, overId) {
  const from = rows.findIndex((r) => r.id === id);
  const to = rows.findIndex((r) => r.id === overId);
  if (from < 0 || to < 0 || from === to) return rows;
  const moved = { ...rows[from] };
  const rest = rows.filter((_, i) => i !== from);
  const at = to > from ? rows.filter((_, i) => i !== from).findIndex((r) => r.id === overId) + 1
                       : rest.findIndex((r) => r.id === overId);
  rest.splice(at, 0, moved);
  const neighbour = rest[at - 1] ?? rest[at + 1];
  if (neighbour) moved.disc = neighbour.disc;
  return rest;
}

// -- where a track's audio comes from (§9, slice 34) -----------------------------------------------

// Twin of youtube.one_video; tests/shared/video_ids.json is the table both are tested against.
// A playlist, a channel or anything unrecognised is not one video, and the page refuses it here
// rather than sending it — the server refuses it too, because the page is not the only door.
const VIDEO_ID = /^[\w-]{11}$/;
const WATCH = new RegExp(
  "^(?:https?://)?(?:www\\.|m\\.|music\\.)?"
  + "(?:youtube\\.com/(?:watch\\?(?:[^#]*&)?v=|v/|e(?:mbed)?/|shorts/|live/)|youtu\\.be/)"
  + "([\\w-]{11})(?:[?&#/].*)?$");

export function oneVideo(text) {
  const value = (text || "").trim();
  if (VIDEO_ID.test(value)) return value;   // a URL never matches: no `:`, `/` or `.` allowed
  const m = WATCH.exec(value);
  return m ? m[1] : null;
}

export const effectiveId = (t) => t.source_override || t.video_id;

// What a source change costs, in the words the user is asked to confirm — because the marks and
// the timings describe the file that is about to be replaced, and nothing may go quietly.
// A ref means nothing outside the provider that minted it, and some of them are paths: a folder's
// ref is the file's own absolute location (§9, slice 53). In a list or a tooltip that is someone's
// home directory quoted at them, so refs with separators are shown by their last two parts. The
// whole thing appears in one place only — the source panel, where the user asked for it.
export function refLabel(ref) {
  if (!ref || !ref.includes("/")) return ref;
  return ref.split("/").filter(Boolean).slice(-2).join("/");
}

/** Labels for a set of refs at once: the shortest tail that tells each of them apart (§9, slice 77).
 *
 * Two copies of one song in two libraries with the same layout have the same album folder and the
 * same file name, so the two-part label printed them identically and only the numbers beside them
 * differed. This walks one part further up for as long as two labels would collide, and gives up at
 * the whole ref rather than inventing a distinction. Paths of the user's are not shown in full
 * anywhere else, so a label only grows for the copies that need it. */
export function copyLabels(refs) {
  const out = new Map();
  const parts = (ref) => String(ref || "").split("/").filter(Boolean);
  for (const ref of refs || []) out.set(ref, refLabel(ref));
  for (let depth = 3; depth <= 12; depth++) {
    const seen = new Map();
    for (const [ref, label] of out) seen.set(label, (seen.get(label) || 0) + 1);
    const clashing = [...out].filter(([, label]) => seen.get(label) > 1);
    if (!clashing.length) break;
    for (const [ref] of clashing) {
      const bits = parts(ref);
      if (bits.length < depth) continue;          // nothing further up to take: leave it as it is
      out.set(ref, bits.slice(-depth).join("/"));
    }
  }
  return out;
}

// One line about one candidate: what it is, how good it is, and what it costs. Numbers only —
// the words around them belong to the page, and the link belongs to the provider (§9, slice 54).
export function candidateLine(c, inUse, refused) {
  const waiting = c.undecided && !inUse && !refused;
  const bits = [inUse ? "in use" : refused ? "refused"
    : waiting ? `nothing could choose: ${c.why || ""}`.trim() : (c.why || c.added_by || "")];
  if (c.codec) bits.push(c.bitrate ? `${c.codec} ${Math.round(c.bitrate / 1000)} kbps` : c.codec);
  if (c.cutoff_khz) bits.push(`to ${c.cutoff_khz} kHz${c.full_band ? " (all it can hold)" : ""}`);
  if (c.length) bits.push(asTime(c.length));
  if (c.bytes) bits.push(`${(c.bytes / 1e6).toFixed(1)} MB`);
  return bits.filter(Boolean).join(" \u00b7 ");
}

// Copies a pass found and could not rank (§9, slice 55). The twin of `PlanTrack.undecided_copies`,
// and the same two answers empty it: taking one makes it the track's ref, refusing one is kept.
export function awaitingChoice(track) {
  const refused = new Set(track.refused_candidates || []);
  const chosen = effectiveId(track);
  return (track.candidates || []).filter((c) => c.undecided && c.ref !== chosen && !refused.has(c.ref));
}

// `copy` is the listed candidate being taken, where the user is answering the pass rather than
// naming a video: then this is another copy of the same song, not another recording of it, and the
// confirm has to say which (§9, slice 55).
export function sourceChange(track, wanted, copy = null) {
  const to = wanted || track.video_id;
  if (to === effectiveId(track)) return null;
  const marks = track.trim_start != null || track.trim_end != null
    ? `${asTime(track.trim_start || 0)}\u2013${track.trim_end == null ? "end" : asTime(track.trim_end)}`
    : null;
  const lines = [
    `\u201c${track.artist} \u2013 ${track.title}\u201d`,
    "",
    wanted
      ? `Take the audio from ${refLabel(to)} instead of the source's own ${refLabel(track.video_id)}.`
      : `Back to the source's own ${refLabel(track.video_id)}.`,
    copy ? "The track is fetched again from that copy."
         : "The track is downloaded again \u2014 it is a different recording.",
  ];
  if (marks) lines.push(`The trim ${marks} belongs to the current file and will be cleared.`);
  if (track.lyrics === "synced") {
    lines.push("Your lyrics are kept, but their timings were written for the current file.");
  }
  lines.push("", "OK: fetch it now. Cancel: nothing changes.");
  return { to, marks, back: !wanted, lines };
}

// The panel's notice when the words beside a track were timed against another file (§9, slice 34).
// The two lengths are only named when they really differ: a file measured again after the same
// song was fetched from another upload can read 3:50 against 3:49 purely from rounding, and a
// pair of numbers that look the same invites "so what?" rather than saying anything.
export const STALE_BY = 1;  // seconds; the twin of lyrics.STALE_BY

export function timingNotice(d) {
  const t = d && d.timings;
  if (!t) return null;
  const drift = t.was != null && t.now != null && Math.abs(t.now - t.was) > STALE_BY;
  return `these timings were written for a different file${drift ? `, ${fmt(t.was)} \u2192 ${fmt(t.now)}` : ""}`;
}

// -- the two clocks, and stamping to them (§9, slice 35) -------------------------------------------
//
// A trimmed track is played from its untouched **original** (§9, slice 15: the trim marks count from the
// start of the video, so the player must hear the file those numbers describe), while a lyric
// stamp counts from the start of the file on disk. The two clocks differ by exactly what was cut
// off the front — and by the *saved* mark, not one being edited: the `.lrc` belongs to the file
// that is there, not to the trim someone is still placing.

/** How much to add to a stamp made against the file on disk to reach the same spot in what is loaded.
 *
 * Zero unless the player is holding the **original** of a cut track: the stamps belong to the cut file,
 * so the trim has to be added back to find them in the longer one (§9, slice 35). Keyed on what was
 * loaded and not on the plan, because `o=1` can come back as the cut file (§9, slice 88). */
export const trimOffset = (playing) =>
  (playing && playing.trimmed && playing.playingOriginal !== false ? playing.savedStart || 0 : 0);
export const tenth = (seconds) => Math.round(seconds * 10) / 10;
export const toFileClock = (seconds, offset = 0) => Math.max(0, tenth(seconds - offset));
export const toPlayerClock = (seconds, offset = 0) => Math.max(0, seconds + offset);

// Exactly what a hand-typed stamp looks like, so nothing can tell the two apart afterwards.
export function stampText(seconds) {
  const t = Math.max(0, Math.round(seconds * 10));
  return `[${String(Math.floor(t / 600)).padStart(2, "0")}:${String(Math.floor((t % 600) / 10)).padStart(2, "0")}.${t % 10}]`;
}

// The same shape app.js reads timed lines with, plus the space that follows it.
const STAMP = /^(\s*)\[(\d{1,3}):(\d{2}(?:[.:]\d{1,3})?)\]\s?/;

export const stampOf = (line) => {
  const m = STAMP.exec(line || "");
  return m ? Number(m[2]) * 60 + parseFloat(m[3].replace(":", ".")) : null;
};

// Replace the stamp on a line, or put one there; `null` takes it off and leaves the words.
export function withStamp(line, seconds) {
  const words = (line || "").replace(STAMP, "");
  if (seconds == null) return words;
  return words ? `${stampText(seconds)} ${words}` : stampText(seconds);
}

export const lineAt = (text, caret) => ((text || "").slice(0, Math.max(0, caret)).match(/\n/g) || []).length;

export function lineStart(text, index) {
  const lines = (text || "").split("\n");
  let at = 0;
  for (let i = 0; i < Math.min(index, lines.length); i++) at += lines[i].length + 1;
  return at;
}

// One tap: the line the cursor is in takes the time, and the cursor moves on, so a whole song
// can be stamped without reaching for the mouse. A line that already had one is rewritten.
export function tapped(text, caret, seconds) {
  const lines = (text || "").split("\n");
  const i = Math.min(lineAt(text, caret), lines.length - 1);
  const line = lines[i];
  lines[i] = withStamp(line, seconds);
  const value = lines.join("\n");
  // **the caret stays on the line it just stamped** (§9, slices 94 and 96, amended 2026-09-30 on the
  // user's own word: *"setting a timestamp still sets focus to the next line. usually you have to
  // finetune it, so it should stay on the line you just set."*). The nudges act on the line the caret
  // is in, so moving away from it was moving away from the four controls that correct the stamp just
  // written. Moving on is the down arrow, as in any editor.
  //
  // Its *place* in the line is kept too, measured from the words rather than from the raw line: the
  // stamp that was just written is in front of them and would otherwise push the caret along.
  const words = line.replace(STAMP, "");
  const inWords = Math.max(0, caret - lineStart(text, i) - (line.length - words.length));
  const prefix = lines[i].length - words.length;
  return { text: value, caret: lineStart(value, i) + prefix + Math.min(inWords, words.length),
           line: i, at: tenth(seconds), last: i >= lines.length - 1 };
}

/** How tall the editor's box is for this text (§9, slice 96).
 *
 * It used to be computed once, when the editor opened: a lyric written into an empty editor kept the
 * smallest box however long it grew. It grows with what is typed, up to the height a full lyric gets,
 * and **never shrinks while the editor is open** — a box that jumps back when a line is deleted moves
 * the words somebody is reading.
 */
export const EDITOR_BOX = { least: 8, most: 26, spare: 2 };

export function editorRows(text, now = 0) {
  const lines = (text || "").split("\n").length;
  const wanted = Math.min(EDITOR_BOX.most, Math.max(EDITOR_BOX.least, lines + EDITOR_BOX.spare));
  return Math.max(wanted, Number(now) || 0);
}

/** Where a textarea has to be scrolled for the line the caret is on to be seen (§9, slice 94).
 *
 * **Moving a caret does not scroll a textarea.** Chrome scrolls to the caret for typing and for the
 * arrow keys, not for a `setSelectionRange` — and every control in this editor sets the selection
 * itself. The stamp is the only one that moves the caret to *another* line, so it is the only one that
 * can put the line being worked on out of sight: measured on a 34-line lyric in an 8-row editor, twelve
 * stamps walked the caret from line 1 to line 13 with `scrollTop` at 0 the whole way. From the eighth
 * on, the user was stamping a line they could not see — which is what "it loses the line" was.
 *
 * Returns the `scrollTop` the box should have, or null when the line is already in view. It keeps a
 * line of context where there is one, so the line does not sit flat against the edge.
 */
export function scrollToLine({ line = 1, lineHeight = 0, clientHeight = 0, scrollTop = 0, lines = 0 } = {}) {
  if (!(lineHeight > 0) || !(clientHeight > 0)) return null;   // nothing measurable to scroll
  const top = Math.max(0, line - 1) * lineHeight;
  const room = Math.floor(clientHeight / lineHeight);
  const context = room > 2 ? lineHeight : 0;    // one line above or below, where the box has room for it
  const highest = Math.max(0, lines * lineHeight - clientHeight);
  if (top < scrollTop) return Math.min(highest, Math.max(0, top - context));
  if (top + lineHeight > scrollTop + clientHeight) {
    return Math.min(highest, top + lineHeight + context - clientHeight);
  }
  return null;
}

// A nudge is the same rewrite with the stamp's own value moved; a line with no stamp has
// nothing to move, and says so rather than growing one by accident.
export function nudged(line, delta) {
  const at = stampOf(line);
  if (at == null) return null;
  const to = Math.max(0, tenth(at + delta));
  return { line: withStamp(line, to), at: to };
}

// Every stamped line by the same amount; the words between verses keep their place in the file.
export function shifted(text, delta) {
  let moved = 0;
  const lines = (text || "").split("\n").map((line) => {
    const at = stampOf(line);
    if (at == null) return line;
    moved++;
    return withStamp(line, Math.max(0, tenth(at + delta)));
  });
  return { text: lines.join("\n"), moved };
}

// -- the words being edited, read as a clock (\u00a79.39) -------------------------------------------
//
// While the editor is open the textarea is the truth for playback: the follow-along highlight and
// the click-to-seek list beside it are drawn from these two functions, not from the saved sidecar,
// so a proposal can be followed through a whole song before anything is written. The stamps are in
// the **file's** clock, like every stamp in the editor (\u00a79.35); converting the player's time is the
// caller's job, exactly as it is for a tap.

export function timedLines(text) {
  return (text || "").split("\n").map((line) => ({ words: line.replace(STAMP, ""), at: stampOf(line) }));
}

// The line being sung: the last stamped line at or before this moment, in document order. Document
// order and not the smallest distance, because a shift or a kept stamp can leave a file out of
// order, and then what the reader wants is the line the file says comes next \u2014 the same walk the
// page has always used for a saved sidecar. A line with no stamp is never the answer; -1 means the
// song has not reached the first stamp yet.
export function nowLine(lines, at) {
  let found = -1;
  (lines || []).forEach((line, i) => { if (line && line.at != null && line.at <= at) found = i; });
  return found;
}

// -- stamps a provider proposed (\u00a79.36) ------------------------------------------------------

// The provider is given the words without their stamps, one line each, blank lines dropped; what
// comes back is in the same order. Putting it into the textarea is therefore a walk down the lines:
// blanks keep their place, a line the provider would not place keeps its words and loses its stamp,
// and nothing is saved until the user saves it.
export function applyStamps(text, timed) {
  const stamps = (timed && timed.lines) || [];
  const unplaced = [];
  let i = 0;
  let placed = 0;
  let kept = 0;
  const out = (text || "").split("\n").map((line, n) => {
    const words = withStamp(line, null).trim();
    if (!words) {
      // a stamp with no words behind it — LRCLIB's trailing outro marker, and a legitimate LRC
      // device for "silence starts here". It was never sent, so it is never rewritten; the notice
      // says how many there are, because a kept stamp can end up out of order after a shift.
      if (stampOf(line) != null) kept++;
      return line;
    }
    const got = stamps[i++];
    if (!got || got.start == null) {
      unplaced.push(n);
      return words;                       // its own words, with no time it cannot vouch for
    }
    placed++;
    return withStamp(words, got.start);
  });
  return { text: out.join("\n"), unplaced, placed, kept, total: placed + unplaced.length };
}

// What the editor says over the proposal. Never "yours": the words may be, the clock is not.
export function alignNotice(timed, result) {
  if (!result) return null;
  const by = (timed && timed.provider ? `${timed.provider}/${timed.model}` : "a provider");
  const p = (timed && timed.parameters) || {};
  // two methods ran and one of them lost the song: the stamps are the primary's, all of them, and
  // the notice says how total the disagreement was (\u00a79.38, measured in catalog Y)
  // Two shapes of whole-track disagreement (\u00a79.44): one where the evidence can say which method
  // lost the song, and one where it cannot and the first method is kept by policy as before.
  const whole = p.lost_method
    ? ` The two methods placed the whole track differently, and ${p.lost_method} is the one that lost `
      + `it: ${p.lost_why}. These stamps are ${p.kept_method}'s \u2014 play the first line before you save them.`
    : p.one_method
    ? ` A second method disagreed about the whole track \u2014 ${p.lost} of ${p.compared} lines more than `
      + `${p.lost_beyond || 5} seconds apart, and nothing here can say which of them is right \u2014 so this is `
      + "one method's word: play the first line before you save it."
    : "";
  const left = result.unplaced.length;
  const kept = result.kept || 0;
  // the check is an extra: when it could not run at all the alignment still stands, and saying so is
  // the difference between "checked and agreed" and "nobody looked" (§9, slice 38)
  const checked = whole || (p.unchecked
    ? ` It could not be checked against a second method (${p.unchecked}), so this is one method's word.`
    : p.disagreed && Number(p.disagreed) > 0
    ? ` Two methods were compared and disagreed on ${p.disagreed} of ${p.compared} lines, which is why those have no stamp.`
    : p.verified_against ? " A second method agreed with every line it could compare." : "");
  // **why a line was not placed** (\u00a79, slice 81): forced alignment puts every line somewhere, so
  // "could not be placed" here means the result was taken back — because nobody was singing there,
  // or because the words could not have been sung that fast. The reasons are the aligner's own.
  const why = p.unsupported ? ` ${String(p.unsupported).split("; ")[0]}${
    String(p.unsupported).split("; ").length > 1 ? `, and ${String(p.unsupported).split("; ").length - 1} more` : ""}.` : "";
  // **placed by listening** (§9, slice 83): the provider wrote down what it heard and these lines were
  // matched to it, so an unplaced line means its words were not heard — which is an answer about the
  // recording, not a failure of the method, and the line's own count says how close it came.
  const heard = timed && timed.method === "listen"
    ? ` Placed by listening: ${p.heard_words || 0} words were heard in the recording, and a line is `
      + `placed when ${Math.round(Number(p.enough || 0.5) * 100)}% of its own words are among them, in order.`
      + (p.not_heard ? ` ${String(p.not_heard).split("; ")[0]}${
          String(p.not_heard).split("; ").length > 1 ? `, and ${String(p.not_heard).split("; ").length - 1} more` : ""}.` : "")
    : "";
  // and the arithmetic's own objection to what is left: stamps where nobody sings, or piled together
  const silent = Number(p.own_in_silence || 0);
  const piled = Number(p.own_piled || 0);
  const objection = silent || piled
    ? ` The check objects: ${[silent ? `${silent} stamp${silent > 1 ? "s sit" : " sits"} where nobody is singing` : "",
        piled ? `${piled} sit on top of each other` : ""].filter(Boolean).join(" and ")} \u2014 play the first lines before you save.`
    : "";
  return `timed by ${by} \u2014 a machine's proposal, nothing is saved yet: press \u25b6 on the first line`
    + (left ? `. ${left} line${left > 1 ? "s" : ""} could not be placed and kept no stamp.${why}` : ".")
    + heard + checked + objection
    + (kept
      ? kept > 1
        ? ` ${kept} stamped lines without words were left as they were; check they are still in order.`
        : " 1 stamped line without words was left as it was; check it is still in order."
      : "");
}

// A draft is a transcript: lines the provider heard, with a stamp where it gave one. It goes into
// the editor as text like any other, because the editor is where a draft is turned into words
// somebody meant (\u00a79.37).
export function draftText(timed) {
  return ((timed && timed.lines) || [])
    .map((line) => (line.start == null ? line.text : withStamp(line.text, line.start)))
    .join("\n");
}

// Said over a draft, and it never says "yours". Half a song is a realistic outcome for some tracks,
// which the spike measured, so the label says that rather than pretending to a transcript.
export function draftNotice(timed) {
  if (!timed) return null;
  const by = timed.provider ? `${timed.provider}/${timed.model}` : "a provider";
  const p = timed.parameters || {};
  // What the old notice said — "11 of 11 lines came with a time" — is true of every draft ever
  // made and tells nobody anything: the machine wrote those lines *from* times. What a person
  // needs to know is how much of their song it actually heard (\u00a79.45).
  const covered = Number(p.covered || 0);
  const length = Number(p.length || 0);
  const gaps = Number(p.gaps || 0);
  const heard = p.heard ? ` It listened to ${p.heard}.` : "";
  const how = length
    ? `. Words for ${asTime(covered)} of ${asTime(length)} of audio`
      + (gaps ? `, with ${gaps} gap${gaps > 1 ? "s" : ""} longer than ${p.gap_longer_than || 6} s marked in the text.` : ".")
    : ".";
  return `drafted by ${by} \u2014 a machine's guess, half a song for some tracks: read it before you save it`
    + how + heard;
}

// -- what a panel offers --------------------------------------------------------------------

// The lyrics panel, from what /api/lyrics answered: what the header says, whose the words are,
// and which actions may be offered. The rules are §9, slice 21's — the user's words are not lrclib's to
// replace, and there is nothing to reject when no entry was matched.
export function lyricsPanelState(d) {
  const mine = d.owner === "user";
  // words that came with the recording: nobody looks them up, nobody publishes them, and the panel
  // says whose they are rather than offering to replace them with a stranger's (§9, slice 74)
  const fromSource = d.owner === "source";
  const words = Boolean(d.text);
  return {
    where: words ? (d.status === "synced" ? "with timestamps" : "plain text") : "no words yet",
    ownership: mine ? "yours"
      : fromSource ? `${d.words_by || "the source"}’s own captions`
      : words && d.lrclib_id ? `lrclib #${d.lrclib_id}` : null,
    actions: [
      words ? "Edit" : "Write lyrics",
      ...(mine || fromSource ? [] : ["Look up again"]),
      ...(!mine && !fromSource && d.lrclib_id ? ["Not these words"] : []),
    ],
  };
}

// Giving the words back to LRCLIB (\u00a79.42). Publishing is public and cannot be undone, so the page
// says exactly what leaves this machine, and says it before anything is sent — the same sentence the
// job log then writes. The server decides *whether* it may be offered; this is only how it is said.
export function publishConfirm(d) {
  const p = (d && d.publish) || {};
  if (!p.can) return null;
  return [
    "These words are about to be published to LRCLIB, for everyone.",
    "",
    `    ${p.artist} \u2014 ${p.title}`,
    `    album: ${p.album || "\u2014"}`,
    `    length: ${Math.round(p.length)} s (the file's own, which is what the timestamps follow)`,
    `    ${p.lines} lines, with their timestamps, and the same words without them`,
    "",
    "LRCLIB is a public database and takes no account. A publish cannot be taken back,",
    "edited or deleted by you afterwards, and noaap never sends it twice.",
    "",
    "OK: publish them. Cancel: nothing leaves this machine.",
  ].join("\n");
}

// What the button says, and why it is not there.
export function publishState(d) {
  const p = (d && d.publish) || {};
  if (p.published) return { show: true, why: "", label: "\u2713 published to lrclib", can: false, title:
    `These words were published on ${p.published}. A publish cannot be undone, and the same words are never sent twice.` };
  if (p.can) return { show: true, why: "", label: "\u2191 publish to lrclib", can: true, title:
    "Give these words back: LRCLIB has no entry like them, and somebody else looking for this song would find yours.\nIt is public and cannot be undone." };
  // Not offered, but there is a reason: the panel shows it, exactly as a refused MusicBrainz seed
  // does. A button that is simply absent teaches nobody why, and "no, because these are lrclib's
  // own words" is a different answer from "no".
  if (p.why) return { show: false, why: p.why, label: "", can: false, title: p.why };
  // Nothing to say: a track with no words needs no explanation of why they cannot be given back.
  return { show: false, why: "", label: "", can: false, title: "" };
}

// An lrclib entry that is nearly this recording (\u00a79.46). The panel's job here is to say what is
// known and what is not, in the user's terms: the words exist, whether they belong to this file, and
// whose clock the stamps are. Nothing here decides anything — the server did that, or said it could
// not, and this only puts it into words.
export function nearMiss(d) {
  const fit = d && d.fit;
  // A track that has words is past all of this: the only thing left worth saying is where they came
  // from, and never an offer to take some. (Found on the scratch library, where a sidecar the plan
  // did not know about became the user's own words while a stale verdict still sat in the plan.)
  const has = Boolean(d && d.text);
  if (has && fit && (fit.decided === "shown" || fit.decided === "unclear")) return null;
  if (!fit) {
    return d && d.can_check && !has
      ? { say: "lrclib may have words for this title that are a few seconds off this file. "
             + "\u2696 check them against the audio.", action: "check" }
      : null;
  }
  const apart = Math.abs(Number(fit.ours || 0) - Number(fit.theirs || 0));
  const gap = `${apart.toFixed(0)} s`;
  switch (fit.decided) {
    case "words+stamps":
      return { say: `lrclib's entry is ${gap} from this file, and its words and timings fit it `
        + `(they cover ${Math.round(Number(fit.span || 0) * 100)}% of the singing).`, action: null };
    case "words":
      return { say: `These are the song's words, but lrclib's entry is ${fit.why === "a clip" ? "of the whole song and this file is a clip of it" : "another cut of it"}`
        + ` \u2014 ${gap} apart. The words are kept; the timings are this file's own, placed by the aligner.`,
        action: null };
    case "reject":
      return { say: `lrclib's nearest entry is not this song: the aligner could not place `
        + `${Math.round(Number(fit.unplaced || 0) * 100)}% of its words in this audio. It will not be offered again.`,
        action: null };
    case "shown":
      return { say: `lrclib has words for this title, for a ${asTime(Number(fit.theirs || 0))} recording; `
        + `this file is ${asTime(Number(fit.ours || 0))} \u2014 probably ${fit.why === "a clip" ? "a clip of the song" : "another cut"}. `
        + "Nothing was taken; you can take the words as plain text.", action: "plain" };
    case "words by hand":
      return { say: "These are lrclib's words, taken without their timings on your say-so.", action: null };
    default:  // unclear: the instrument does not know, so the person is given the numbers
      return { say: `lrclib's entry is ${gap} from this file. The aligner placed `
        + `${Math.round((1 - Number(fit.unplaced || 0)) * 100)}% of its lines across `
        + `${Math.round(Number(fit.span || 0) * 100)}% of the singing, which is neither a fit nor a miss. `
        + "Nothing was taken; you can take the words as plain text, or listen and decide.", action: "plain" };
  }
}

// Offering an album to MusicBrainz (\u00a79.43). noaap never submits anything: the button opens
// *their* release editor with the boxes filled in, and the person reviews it signed in as
// themselves. So this half is only about when to offer, and what to say first.
export function canSeed(plan) {
  if (!plan) return { can: false, why: "" };
  if (plan.mbid) return { can: false, why: "MusicBrainz already has this release" };
  if (plan.kind === "compilation") return { can: false, why: "MusicBrainz wants releases that exist as releases, not compilations" };
  if (plan.kind === "artist_playlist") return { can: false, why: "this is a playlist somebody made, not a release" };
  if (!(plan.tracks || []).some((t) => t.state === "done")) return { can: false, why: "nothing has been downloaded yet" };
  if (!String(plan.album || "").trim() || !String(plan.albumartist || "").trim()) {
    return { can: false, why: "an album needs a title and an artist before it can be offered" };
  }
  return { can: true, why: "" };
}

export function seedConfirm(plan) {
  const done = (plan.tracks || []).filter((t) => t.state === "done").length;
  return [
    `MusicBrainz's release editor is about to open, with ${done} track${done === 1 ? "" : "s"} already filled in:`,
    "",
    `    ${plan.albumartist} \u2014 ${plan.album}${plan.year ? ` (${plan.year})` : ""}`,
    "    one Digital Media medium, the titles and the lengths measured from your files,",
    "    the playlist's URL, and an edit note saying where it came from.",
    "",
    "noaap submits nothing. The form opens in a new tab, signed in as you, and nothing reaches",
    "MusicBrainz until you press their own submit button. Check every field first:",
    "the titles come from YouTube, and MusicBrainz wants releases that were really released.",
    "",
    "OK: open the form. Cancel: nothing opens.",
  ].join("\n");
}

// A recording whose length MusicBrainz has wrong (\u00a79.43). Seeding cannot fix a recording \u2014 the
// format is for releases \u2014 so what is possible is their edit page and the two numbers.
export function lengthFix(track, by = 10) {
  const ours = track && (track.file_length || track.duration);
  if (!track || !track.mbid || !ours || !track.mb_length) return null;
  const apart = Math.abs(ours - track.mb_length);
  return apart > by ? { ours: Math.round(ours * 10) / 10, theirs: Math.round(track.mb_length * 10) / 10,
    apart: Math.round(apart * 10) / 10 } : null;
}

export function fixConfirm(track, fix) {
  return [
    `MusicBrainz has a different length for “${track.title}”:`,
    "",
    `    their recording: ${asTime(fix.theirs)}`,
    `    your file:       ${asTime(fix.ours)}  (${fix.apart} s apart)`,
    "",
    "Their recording page is about to open so you can look. noaap cannot seed a correction —",
    "the seeding format is for releases, not recordings — so the change is yours to make, and",
    "only if you are sure: a file can be shorter because it was trimmed, or longer because the",
    "upload has an intro. Nothing is sent from here.",
    "",
    "OK: open the recording. Cancel: nothing opens.",
  ].join("\n");
}

// The badge on a field: a button back to what noaap derived, a plain badge, or nothing.
// Nothing is offered where nothing was derived — an album from before `auto` was recorded has
// no value to go back to (§9, slice 29).
export function resetKind(provenance, derived) {
  if (provenance !== "user") return provenance ? "badge" : null;
  return derived === undefined || derived === null || derived === "" ? "badge" : "button";
}

// -- keeping the line that is being sung in view (§9, slice 68) ---------------------------
//
// The rolling list scrolled the active line **out** of view on every step, and the cause was one
// arithmetic mistake: the page set `box.scrollTop = line.offsetTop - …`, and `offsetTop` is measured
// from the nearest *positioned* ancestor — which inside a table is the `td`, whatever the box does.
// Measured on the installed page: in the editor's preview the box starts 645 px below that `td`
// (the textarea is above it), so every target was 645 px — **28 lines** — too far down. In the
// read-only panel the same mistake is 36 px, so it only drifts. Both lists are this one function.
//
// So the caller passes the line's top **relative to the list's own content**, and this decides where
// to scroll. It is here because it is arithmetic, and because a node test can hold it.

/** Where the list should scroll so the active line is comfortably in view — or `null` for "leave it".
 *
 * `margin` is how many lines to keep above and below where the box is tall enough to allow it; a box
 * that cannot hold that much simply centres. A line already comfortably in view is left alone, so the
 * list does not fight somebody who has scrolled it themselves.
 */
export function scrollForActive({ boxHeight, boxScroll, contentHeight, lineTop, lineHeight, margin = 1 }) {
  const furthest = Math.max(0, (contentHeight || 0) - (boxHeight || 0));
  const centred = Math.min(furthest, Math.max(0, Math.round(lineTop - (boxHeight - lineHeight) / 2)));
  if (!boxHeight || !lineHeight) return null;
  const keep = boxHeight >= lineHeight * (2 * margin + 1) ? lineHeight * margin : 0;
  const top = boxScroll + keep;
  const bottom = boxScroll + boxHeight - keep;
  if (lineTop >= top && lineTop + lineHeight <= bottom) return null;   // already where it should be
  return centred === boxScroll ? null : centred;
}

// -- claiming a draft as your own (§9, slice 69) -------------------------------------------
//
// A draft is refused a publish, and rightly: `publishable` says *these words are a draft by
// deepgram/nova-3*. But the advice could not be followed — the editor read `words_by` once when it
// opened and sent it back on **every** save, so a draft stayed a draft however much of it somebody
// rewrote. Nothing ever cleared the mark.
//
// It is not cleared by editing either. One changed character of a machine's guess is not authorship,
// and a publish cannot be taken back. So it takes a statement, made once, deliberately.

/** The label of that statement, and why it is offered — or `offer: false` where there is no draft. */
export function claimOffer(d) {
  if (!d || !d.words_by) return { offer: false, label: "", title: "" };
  // **not for words that came with the recording** (§9, slice 74). The claim exists so somebody can
  // take ownership of a machine's draft *of this recording*; a post's captions are the creator's own
  // writing, and correcting a line of somebody else's text is not authorship of it. Editing them
  // changes nothing about whose they are, which is why there is no control that would suggest it.
  if (d.owner === "source") return { offer: false, label: "", title: "" };
  return {
    offer: true,
    label: CLAIM_LABEL,
    title: `On record these words are a draft by ${d.words_by}. Ticking this says the words in the `
         + "editor are yours now, which is what lets them be given back to lrclib. Editing them is not "
         + "the same thing: one changed character of a machine's guess is not authorship.",
  };
}

/** What the player should do about the trim window, given where the playhead is (§9, slice 77).
 *
 * Previewing a trim means skipping the head and stopping at the end. The part that was wrong: a
 * **jump** past the end was treated as having played into it, so dragging the bar — or clicking a
 * line whose stamp lies beyond the cut — silently started the *next track*. To a person that is the
 * sound stopping and, a moment later, other music. Playing into the end still moves on; landing
 * beyond it by a jump stops there, where the audio ends.
 *
 * `previous` is the position at the last tick; a gap larger than `JUMP` means somebody moved it.
 *
 * **The window belongs to whatever the player actually loaded** (§9, slice 87). A cut track is played
 * from its untouched original, and *that* is the file the window applies to. Where the player is
 * holding the cut file instead — which happens for the seconds between a save and the page learning
 * that the file has changed — the window is already in the bytes, and applying it again skipped the
 * head twice: the user's "after saving it starts as if the cut part was the original, starting the
 * front trim into the trimmed part". So `onTheOriginal` decides, and it is set from the URL the player
 * asked for, not from the plan's fields, which can be half a save old.
 */
/** The rows of the album table that really are tracks (§9, slice 89).
 *
 * The page inserts panels — the lyrics editor, the source picker — as extra `tr`s after the row they
 * belong to, and those carry the same `data-id` as the track. They have none of its input fields, so a
 * save that walked every row in the tbody read `null.value` and **threw before it submitted anything**:
 * with a lyrics panel open, "Save changes" saved nothing and said nothing (R-299). A row is a track row
 * when it is not one of those panels *and* carries the fields a save needs — the second test on its own
 * would be enough, and it is there so that a panel kind nobody has thought of yet cannot slip through.
 */
export const PANEL_ROW = /^(lyrics|source)$/;

export function trackRows(rows, fields = "[name=number]") {
  return [...(rows || [])].filter(
    (tr) => tr && tr.dataset && tr.dataset.id
            && ![...(tr.classList || [])].some((c) => PANEL_ROW.test(c))
            && tr.querySelector(fields));
}

/** What the header's recycle-bin button says, and whether it is there at all (§9, slice 91).
 *
 * The bin used to live at the bottom of the settings panel, where the user did not think it belonged.
 * It has its own place now, beside the library's own actions — and it is only offered when it holds
 * something, because an empty bin is not news.
 */
/** What belongs in the header (§9, slice 91).
 *
 * The user, twice: the recycle bin does not belong in the settings, and *"Update library" / "Repair
 * library"* do not belong in the header next to the search. What is left is what you reach for without
 * reading: the search, the bin when it holds something, the theme and the settings.
 */
export const HEADER = ["open", "bin", "theme", "gear"];

export function headerHas(ids) {
  const seen = [...(ids || [])].filter((id) => HEADER.includes(id));
  return { extra: [...(ids || [])].filter((id) => !HEADER.includes(id)), complete: seen.length === HEADER.length };
}

export function binLabel(count) {
  const n = Number(count) || 0;
  return n > 0
    ? { hidden: false, text: `Recycle bin (${n})`,
        title: `${n} thing${n > 1 ? "s" : ""} noaap moved aside instead of deleting. Nothing leaves the bin on its own.` }
    : { hidden: true, text: "Recycle bin",
        title: "What noaap moved aside instead of deleting. Nothing leaves it on its own." };
}

/** Sources as a list of what is set up (§9, slice 95).
 *
 * The user: *"sources as cards or rows that can be added and removed and have their own config dialog
 * once you add or edit them, so you have all sources together visible and do not have each source's
 * settings mess exposed right away."* Slice 92 put every field of every provider on the page at once;
 * what a person wants to see is **which** sources they have, and the fields only when they open one.
 *
 * A provider is a row when something about it is set: a session (a browser or a cookies file) or one of
 * its switches. `post_cap` is not evidence — it has a default of 200 that nobody chose — so only the
 * fields the page offers count.
 */
export const SOURCE_FIELDS = ["cookies_from_browser", "cookies_file", "audio_from_video", "captions"];

export const BROWSER_LABEL = { firefox: "Firefox", chrome: "Chrome", chromium: "Chromium",
                               brave: "Brave", edge: "Edge", vivaldi: "Vivaldi", opera: "Opera" };

export const browserLabel = (name) =>
  BROWSER_LABEL[String(name || "").split(":")[0]] || String(name || "");

const SOURCE_NAMES = { youtube: "YouTube", soundcloud: "SoundCloud", bandcamp: "Bandcamp" };
export const sourceLabel = (name) =>
  SOURCE_NAMES[name] || String(name || "").charAt(0).toUpperCase() + String(name || "").slice(1);

/** Which of a provider's offered fields hold something. */
export const fieldsSet = (fields = {}) =>
  SOURCE_FIELDS.filter((field) => field in fields && Boolean(fields[field]));

/** One line saying what is configured, in the order it is asked for. */
export function sourceSummary(fields = {}) {
  const parts = [];
  const session = [];
  if (fields.cookies_from_browser) session.push(`from ${browserLabel(fields.cookies_from_browser)}`);
  if (fields.cookies_file) session.push("from a cookies file");
  if (session.length) parts.push(`session ${session.join(" and ")}`);
  else if ("cookies_from_browser" in fields || "cookies_file" in fields) parts.push("no session — public posts only");
  if (fields.audio_from_video) parts.push("audio taken out of video posts");
  if (fields.captions) parts.push("captions kept as lyrics");
  return parts.join(" · ");
}

/** One watched folder, as a row says it (§9, slice 98). */
export function folderSummary(watch = {}) {
  const parts = [watch.shape === "library" ? "this library, watching itself" : "what is dropped here is taken in"];
  if (watch.folder) parts.push(watch.folder);
  if (watch.folder && watch.there === false) parts.push("the folder is not there");
  else if (watch.looked) parts.push(`last looked at ${watch.looked}`);
  else parts.push("not looked at yet");
  if (watch.waiting) parts.push(`${watch.waiting} arrival(s) it could not hand over`);
  return parts.join(" · ");
}

/** The sources, as rows: the providers that are set up, and **every watched folder** (§9, slice 98).
 *
 * The user: *"intake and watch folders are sources as well, and there might even be multiple of
 * those."* They are: a folder somebody drops music into is where music comes from, exactly as a
 * Patreon account is, and it belongs in the one list rather than in a section of its own under
 * Library. A provider is at most one row; a watched folder is a row each, however many there are.
 */
export function sourceRows(sources = {}, watches = []) {
  const rows = [];
  const unset = [];
  for (const name of Object.keys(sources).sort()) {
    const fields = sources[name] || {};
    const set = fieldsSet(fields);
    if (set.length) {
      rows.push({ kind: "provider", name, label: sourceLabel(name), set,
                  summary: sourceSummary(fields), fields });
    } else {
      unset.push(name);
    }
  }
  (watches || []).forEach((watch, index) => {
    rows.push({ kind: "folder", index, name: watch.name, label: watch.name || "a watched folder",
                summary: folderSummary(watch), watch });
  });
  // a folder can always be added, and another after that — which is the point of the user's "multiple"
  return { rows, unset, folders: rows.filter((r) => r.kind === "folder").length,
           none: rows.length === 0 };
}

/** The watches after one row is edited or added: that row and no other (§9, slice 98). */
export function watchesAfter(watches = [], index, next) {
  const out = (watches || []).map((w) => ({ name: w.name, folder: w.folder, shape: w.shape || "intake" }));
  const one = { name: String(next?.name || "").trim(), folder: String(next?.folder || "").trim(),
                shape: next?.shape === "library" ? "library" : "intake" };
  if (index == null || index < 0 || index >= out.length) out.push(one);
  else out[index] = one;
  return out;
}

/** The watches without one row. */
export const watchesWithout = (watches = [], index) =>
  (watches || []).filter((_, i) => i !== index)
    .map((w) => ({ name: w.name, folder: w.folder, shape: w.shape || "intake" }));

/** Which fields a provider's dialog shows: its own, and nothing of anybody else's. */
export const dialogFields = (name, sources = {}) =>
  SOURCE_FIELDS.filter((field) => field in (sources[name] || {}));

/** What clearing a source sends: that provider's offered fields, emptied, and nothing else. */
export function clearedSource(name, sources = {}) {
  const out = {};
  for (const field of dialogFields(name, sources)) {
    out[`${name}_${field}`] = field.startsWith("cookies") ? "" : false;
  }
  return out;
}

/** What a Remove has to say before it does it: the provider, and what of it goes. */
export function removeConfirm(name, sources = {}) {
  const fields = sources[name] || {};
  const said = [];
  if (fields.cookies_from_browser) said.push(`the session from ${browserLabel(fields.cookies_from_browser)}`);
  if (fields.cookies_file) said.push(`the path of the cookies file (${fields.cookies_file})`);
  if (fields.audio_from_video) said.push("taking the audio out of video posts");
  if (fields.captions) said.push("keeping the captions as lyrics");
  const label = sourceLabel(name);
  return `Remove ${label}'s settings?\n\n`
    + (said.length ? `This clears ${said.join(", ")}.\n\n` : "")
    + `No file of yours is touched, and ${label} still works for anything that needs no login. `
    + "You can set it up again at any time.";
}

/** What the library is set to want, and what one album is excepted from (§9, slice 100).
 *
 * The user: *"if i do an intake with deliberately few options checked to speed things up and decide
 * that i now want to have the image embedded everywhere i am not going to hop through 700 albums
 * manually."* So these are settings, not per-album records, and the only per-album thing is an
 * exception — which can only take something away, because an album that could ask for *more* would
 * be a second state for the library to be in.
 */
export const STATE_SWITCHES = [
  ["musicbrainz", "Look albums up at MusicBrainz",
   "names, years, covers and tracklists, for every album that may be looked up"],
  ["lyrics", "Look words up at LRCLIB",
   "a `.lrc` beside the track, and the words in its tag where the next switch allows it"],
  ["cover_beside", "A cover beside each album",
   "`cover.jpg` in the album folder — what a file manager and most players show"],
  ["cover_embedded", "The cover inside the files",
   "every track carries the picture; costs a rewrite of each file when it is switched on"],
  ["lyrics_embedded", "The words inside the files",
   "the `LYRICS` tag beside the `.lrc`, for players that read only tags"],
  ["rename_adopted", "Rename albums taken in where they stood",
   "off by default: a collection taken in keeps the names its owner gave it"],
  ["retag_adopted", "Write noaap's tags into albums taken in where they stood",
   "off by default, and it never removes a field noaap does not model"],
  ["tidy_adopted_tags", "Tidy the values of albums taken in where they stood",
   "off by default: what their tags say is what they mean — no `(Live in Dresden)` taken out of a "
   + "title, no artist replaced by the album's, no `2003-01-01` cut to `2003`. On, they get the "
   + "same tidying a fetched album does, from the next check or repair"],
  ["drop_comments", "No comment in the files this library writes",
   "off by default, and the only field a write takes away rather than leaves. On, every pass that "
   + "writes a file removes its comment — a ripper's `ripped by …`, a release site's address — in "
   + "whichever way the format keeps one. The check counts them before anything goes"],
  ["drop_wm_frames", "No Windows Media library ids in them either",
   "off by default. `PRIV` frames whose owner begins `WM/` are a media player's own library ids — "
   + "`WMCollectionID`, `WMContentID`, `MediaClassPrimaryID` — written by something that is not "
   + "noaap and meaning nothing outside it. Only mp3 has them, and the check counts them first"],
];

export const EXCEPTION_LABELS = {
  names: "Keep this album's names",
  tags: "Write no tags into this album's files",
  cover_embedded: "Do not embed the cover here",
  lyrics_embedded: "Do not embed the words here",
  musicbrainz: "Do not look this album up at MusicBrainz",
  lyrics: "Do not look this album's words up at LRCLIB",
};

/** Which of the library's operations this album's own exceptions hold back. */
export function heldBack(state = {}, exceptions = {}) {
  const off = [];
  for (const [key, on] of Object.entries(exceptions || {})) {
    if (!on) continue;
    if (key === "names" && state.rename_adopted) off.push("rename_adopted");
    else if (key === "tags" && state.retag_adopted) off.push("retag_adopted");
    else if (state[key]) off.push(key);
  }
  return off;
}

/** One line for the album view: what this album is excepted from, or nothing. */
export const saysExceptions = (exceptions = {}) =>
  Object.keys(EXCEPTION_LABELS).filter((key) => (exceptions || {})[key])
    .map((key) => EXCEPTION_LABELS[key]).join(" · ");

/** Which timing fields a slot's provider actually uses (§9, slice 92).
 *
 * The settings showed every one of them whatever was selected — an endpoint for `local`, two API keys
 * for a machine with no account anywhere. A field is shown when the provider of that slot uses it, and
 * nothing is lost by hiding one: what is not shown is not sent, and what is not sent is left alone.
 */
export const TIMING_FIELDS = {
  none: [],
  local: ["device", "verify"],
  http: ["endpoint"],
  elevenlabs: ["key"],
  deepgram: ["key"],
};

export function timingFields({ align = "none", draft = "none" } = {}) {
  const wanted = new Set();
  const keys = [];
  for (const provider of [align || "none", draft || "none"]) {
    for (const field of TIMING_FIELDS[provider] || []) {
      if (field === "key") keys.push(provider);
      else wanted.add(field);
    }
  }
  return { fields: [...wanted], keys: [...new Set(keys)],
           shows: (name) => wanted.has(name), showsKey: (vendor) => keys.includes(vendor) };
}

/** Why a set of watched folders cannot stand (§9, slice 92) — the same rules `config.watch_trouble`
 * applies, said in the page before anything is sent, so a typo is answered where it was typed.
 */
const _norm = (p) => String(p || "").trim().replace(/\/+$/, "");
const _inside = (folder, other) => Boolean(other) && (folder === other || folder.startsWith(`${other}/`));

export function watchTrouble(watches = [], library = "") {
  const out = [];
  const seen = new Set();
  const lib = _norm(library);
  for (const w of watches) {
    const folder = _norm(w.folder);
    const name = String(w.name || "").trim();
    const called = name || folder || "a watched folder";
    if (!folder) { out.push(`${called}: name the folder it should look at`); continue; }
    if (!folder.startsWith("/")) out.push(`${called}: ${folder} is not an absolute path`);
    if (!name) out.push(`${folder}: give it a name — the watcher writes down what it did under it`);
    else if (seen.has(name)) out.push(`${name}: two watched folders share that name`);
    seen.add(name);
    if (lib && (w.shape || "intake") === "intake" && (_inside(lib, folder) || _inside(folder, lib))) {
      out.push(`${called}: an intake folder may not hold the library, and the library may not hold it`
               + " — a library that watches itself is the `library` shape");
    }
  }
  for (const a of watches) {
    for (const b of watches) {
      if (a !== b && _norm(b.folder) && _inside(_norm(b.folder), _norm(a.folder))) {
        out.push(`${String(b.name || "").trim() || _norm(b.folder)}: lies inside `
                 + `${String(a.name || "").trim() || _norm(a.folder)}; watched folders may not be nested`);
      }
    }
  }
  return out;
}

/** What a folder about to be taken in offers, and why not (§9, slice 92). */
export function takeInState({ folder = "", mode = "merge", check = null, running = false } = {}) {
  const named = String(folder || "").trim();
  // a check belongs to the folder and the way of taking it in that it was asked about, and to nothing
  // else: its lines stop being shown the moment either changes
  const matches = Boolean(check && check.folder === named && check.mode === mode);
  if (!named) {
    return { matches, canCheck: false, canApply: false, note: "Name a folder — a local path, or a mounted share." };
  }
  if (!named.startsWith("/") && !/^[A-Za-z]:[\\/]/.test(named)) {
    return { matches, canCheck: false, canApply: false, note: "Name the folder in full, from the root." };
  }
  if (running) {
    return { matches, canCheck: false, canApply: false,
             note: "Something is writing to the library — taking a folder in waits for it." };
  }
  if (!matches) {
    const first = {
      merge: "Check first: it compares both sides and writes nothing. The other folder is never written to.",
      adopt: "Check first: it reads the folder and writes nothing.",
      "take-in": "Check first: it reads the whole collection and writes nothing — not even the record "
                 + "of what every file was, which the real run writes before it touches anything.",
    };
    return { matches, canCheck: true, canApply: false, note: first[mode] || first.adopt };
  }
  return { matches, canCheck: true, canApply: Boolean(check.lines?.length),
           note: check.lines?.length
             ? `Apply does exactly what this check listed: ${check.lines.length} line(s).`
             : "That check found nothing to do." };
}

/** What the repair section offers, given the last check and the library as it is now (§9, slice 91).
 *
 * A repair renames files, rewrites tags and cuts audio, so it is two steps: a **check** that writes
 * nothing and lists what it would do, and an **apply** that is only offered once a check has been read.
 * A check taken before the library changed is stale — what it listed is no longer what would happen —
 * and says so instead of being applied.
 */
export function repairState({ check = null, version = null, running = false } = {}) {
  if (running) {
    return { canCheck: false, canApply: false, stale: false,
             note: "Something is writing to the library — a repair waits until it is finished." };
  }
  if (!check) {
    return { canCheck: true, canApply: false, stale: false,
             note: "Check first: it reads the library and writes nothing." };
  }
  if (check.version && version && check.version !== version) {
    return { canCheck: true, canApply: false, stale: true,
             note: "The library has changed since this check — run it again before applying it." };
  }
  // **what it found, not how much it said**: the summary line is always there, so the number of albums
  // the pass would touch is what decides whether there is anything to apply
  if (!(check.albums || 0)) {
    return { canCheck: true, canApply: false, stale: false, note: "That check found nothing to do." };
  }
  return { canCheck: true, canApply: true, stale: false,
           note: `Apply does exactly what this check listed: ${check.albums} album(s).` };
}

export const JUMP = 1.5;

/** What the player must ask for to hear this track (§9, slice 87).
 *
 * A cut track is played from its untouched original, so that the marks and what is heard share one
 * clock. The `c=` token is the shape of the file being asked for — the trim it is cut to, or its
 * length when it is not cut — so that a file which has just been *replaced* is never reused from the
 * media cache: measured, the element went on reporting 194.85 s for a file that had become 186.23 s.
 */
export function audioRequest(entry) {
  // **only where the original is really kept** (§9, slice 88). The server answers `o=1` with the cut
  // file when `.originals/` holds nothing, so asking for one that is not there leaves the page adding
  // the trim to a file that already carries it — every lyric stamp then lands `trim_start` too late.
  const original = Boolean(entry.trimmed) && entry.original_kept !== false;
  const shape = entry.trimmed || (entry.file_length == null ? "" : String(entry.file_length));
  return { original, token: shape,
           query: (original ? "&o=1" : "") + (shape ? `&c=${encodeURIComponent(shape)}` : "") };
}

/** What a finished write job changes about a queue entry (§9, slice 87).
 *
 * Returns the fields to take from the fresh plan. **Never the file being played**: the element is in
 * the middle of something the user started, and a save is not a request to restart it — the page
 * updates what it knows and the next start of that track uses it. Marks the user has moved and not
 * saved are kept, because they are the user's work and the server does not know about them yet.
 */
export function syncEntry(entry, fresh) {
  const editing = entry.start !== entry.savedStart || entry.end !== entry.savedEnd;
  const patch = { trimmed: fresh.trimmed ?? null, file_length: fresh.file_length,
                  duration: fresh.duration, savedStart: fresh.trim_start ?? null,
                  savedEnd: fresh.trim_end ?? null,
                  // whether an untouched original is kept decides which file the next start asks for
                  original_kept: fresh.original_kept !== false };
  return editing ? patch : { ...patch, start: fresh.trim_start ?? null, end: fresh.trim_end ?? null };
}

export function trimGuard({ current, start, end, previous = null, jumped = null,
                            dragging = false, unsaved = false, onTheOriginal = true }) {
  if (!onTheOriginal) return {};   // the file the player is holding already carries the cut
  // the player knows when it has just seeked; everyone else can tell from the gap since the last tick
  const moved = jumped !== null ? jumped
    : previous !== null && Math.abs(current - previous) > JUMP;
  // **not after a jump**: somebody who asks to be before the trim start is asking for exactly that,
  // and answering it by moving them to the mark is the "every jump point restarts the track" the user
  // reported (§9, slice 88). Playing into the head still skips it, which is what the preview is for.
  if (start && current < start - 0.4 && !dragging && !moved) return { seekTo: start };
  if (end && current > end) {
    if (moved || unsaved || dragging) return { seekTo: Math.max(end - 0.05, start || 0), pause: true };
    return { next: true };
  }
  return {};
}

// The exact words the refusal tells somebody to look for. `publishable` in lyrics.py repeats them, and
// a case greps this file to keep the two the same — an instruction that names a control which reads
// differently is not an instruction.
export const CLAIM_LABEL = "I have corrected these words, they are mine";

/** What a save sends as `words_by`: nothing once the words have been claimed, else the mark as it was. */
export function wordsAfterClaim(wordsBy, claimed) {
  return claimed ? "" : (wordsBy || "");
}

// -- polling (§9, slice 139; R-499 item 3) ---------------------------------------------------
//
// The timer fired every 700 ms while a job ran, and on the user's NAS library one `/api/state`
// answer took 107 seconds — so every tick opened another request behind the last one and the page
// queued dozens of identical walks, with anything else (a cover, a track) waiting its turn. One
// request at a time, and a slow answer is waited for rather than called a dead server.

/** How long to wait before the next poll, and whether this tick may ask at all. */
export const pollPlan = ({ polling = false, busy = false, waiting = false, offline = false } = {}) =>
  polling
    ? { ask: false, next: 1000 }
    : { ask: true, next: offline ? 3000 : busy || waiting ? 700 : 8000 };

/** Whether a failed poll means the server is gone. A slow answer never reaches here: the page
 *  opens no second request while one is in flight, and the ceiling is far above the staleness
 *  bound the server states, so anything that does fail really is gone. */
export const pollFailureIsOffline = () => true;

/** The ceiling for one state request: well above the server's own `stale_after` and above the one
 *  walk a cold start costs (23 s on the user's library over NFS). */
export const POLL_CEILING_MS = 120000;

// -- "Identify with MusicBrainz" (§9, slice 142; R-506 item 2) ---------------------------------
//
// The user pinned a release by hand and nothing happened until a pass was run from a terminal: the
// panel had no way to say "go and look this album up". It is the same two steps as a repair — look
// first, apply what was listed — because the lookup rewrites titles, numbers and discs, and a
// person who cannot see that first is being asked to trust it blind.

/** What the Identify button may do, and what to say under it. */
export function identifyState({ check = null, running = false, pinned = false,
                                version = null } = {}) {
  if (running) {
    return { canCheck: false, canApply: false, stale: false,
             note: "Something is writing to this library — identifying waits until it is finished." };
  }
  if (!check) {
    return { canCheck: true, canApply: false, stale: false,
             note: pinned
               ? "Asks MusicBrainz about the release you pinned. It writes nothing yet."
               : "Asks MusicBrainz which release this album is. It writes nothing yet." };
  }
  if (check.version && version && check.version !== version) {
    return { canCheck: true, canApply: false, stale: true,
             note: "This album has changed since you looked — look again before applying it." };
  }
  if (!(check.lines || []).length) {
    return { canCheck: true, canApply: false, stale: false,
             note: check.matched
               ? "MusicBrainz agrees with what this album already says — nothing to change."
               : "MusicBrainz has no release that matches this album." };
  }
  return { canCheck: true, canApply: true, stale: false,
           note: `Apply writes exactly what is listed: ${check.lines.length} change(s).` };
}

/** The lines of an identify check, in the order the panel shows them. A plain list, because the
 *  server's own words are what a person is being asked to agree to (§9, slice 85). */
export const identifyLines = (job) =>
  (job?.log || []).filter((line) => line.trim() && !line.startsWith("==="))
                  .map((line) => line.replace(/^ {2}/, ""));

// -- where an album came from (§9, slice 143; R-509 item 2) ------------------------------------
//
// The album panel ended in `<a href=p.source_url>open on YouTube</a>` for every album, whatever its
// source. For the 1,312 albums the user adopted from folders that is a link to a path on the NAS
// labelled as YouTube — it opens nothing, and it says something untrue about where the music came
// from. The folder provider has always answered `url_for` with None and said why: "nothing to open
// in a browser. A file manager would need a `file://`, and offering one that half the desktops
// ignore is worse than offering none."

/** How the panel should show where an album came from: a link, or words.
 *
 *  A link only where the source really is one a browser can open, and named after the provider that
 *  minted it rather than after YouTube (§9, slice 51: the page must not decide what a ref means).
 */
export function sourceOpen({ source_url = "", provider = "" } = {}) {
  const url = String(source_url || "");
  if (/^https?:\/\//i.test(url)) {
    return { href: url, label: `open on ${sourceLabel(provider) || "the source"}` };
  }
  if (!url) return null;
  return { text: `taken in from ${url}` };
}

/** One weighed-and-refused release as the panel lists it (§9, slice 145). */
export function offerLine(one = {}) {
  const where = [one.date, one.country].filter(Boolean).join(", ") || "no date";
  const shape = (one.media || []).join("/") || String(one.tracks ?? "?");
  const fit = one.matched == null ? "not opened"
            : `${one.matched} of ${one.needed} titles fitted`;
  return `${one.title || "untitled"} (${where}) · ${shape} track(s) · ${fit}`;
}

/** Whether the panel has candidates to offer, and what to say above them. */
export function offersState({ offered = [], mbid = null, pinned = false } = {}) {
  if (!offered.length) return null;
  return {
    note: pinned
      ? "MusicBrainz could not fit the release you pinned. These are the ones it weighed:"
      : `MusicBrainz weighed ${offered.length} release(s) and none fitted. Pin the right one and `
        + "identify again:",
    rows: offered.map((one) => ({ id: one.id, label: offerLine(one),
                                  current: Boolean(mbid) && one.id === mbid })),
  };
}
