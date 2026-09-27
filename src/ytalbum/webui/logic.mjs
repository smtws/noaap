// The part of the page that is arithmetic rather than DOM: matching text, reading lengths,
// arranging tracks, deciding what a panel offers. It is a module so `node --test` can import it
// (tests/js/), and the page imports it with `<script type="module">` — no build step either way.
//
// Nothing in here touches `document`. Where a rule has a twin in Python, the twin is named in the
// comment and `tests/shared/*.json` holds the table both sides are tested against, so the two
// cannot drift apart (DESIGN.md §9.33).

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
// landed first). What the server then does with it is service.placed (§9.32).
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

// -- where a track's audio comes from (§9.34) -----------------------------------------------

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
export function sourceChange(track, wanted) {
  const to = wanted || track.video_id;
  if (to === effectiveId(track)) return null;
  const marks = track.trim_start != null || track.trim_end != null
    ? `${asTime(track.trim_start || 0)}\u2013${track.trim_end == null ? "end" : asTime(track.trim_end)}`
    : null;
  const lines = [
    `\u201c${track.artist} \u2013 ${track.title}\u201d`,
    "",
    wanted
      ? `Take the audio from ${to} instead of the playlist's own ${track.video_id}.`
      : `Back to the playlist's own video, ${track.video_id}.`,
    "The track is downloaded again \u2014 it is a different recording.",
  ];
  if (marks) lines.push(`The trim ${marks} belongs to the current file and will be cleared.`);
  if (track.lyrics === "synced") {
    lines.push("Your lyrics are kept, but their timings were written for the current file.");
  }
  lines.push("", "OK: fetch it now. Cancel: nothing changes.");
  return { to, marks, back: !wanted, lines };
}

// The panel's notice when the words beside a track were timed against another file (§9.34).
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

// -- the two clocks, and stamping to them (§9.35) -------------------------------------------
//
// A trimmed track is played from its untouched **original** (§9.15: the trim marks count from the
// start of the video, so the player must hear the file those numbers describe), while a lyric
// stamp counts from the start of the file on disk. The two clocks differ by exactly what was cut
// off the front — and by the *saved* mark, not one being edited: the `.lrc` belongs to the file
// that is there, not to the trim someone is still placing.

export const trimOffset = (playing) => (playing && playing.trimmed ? playing.savedStart || 0 : 0);
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
  lines[i] = withStamp(lines[i], seconds);
  const value = lines.join("\n");
  const next = Math.min(i + 1, lines.length - 1);
  return { text: value, caret: lineStart(value, next), line: i, at: tenth(seconds), last: i >= lines.length - 1 };
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
  // the difference between "checked and agreed" and "nobody looked" (§9.38)
  const checked = whole || (p.unchecked
    ? ` It could not be checked against a second method (${p.unchecked}), so this is one method's word.`
    : p.disagreed && Number(p.disagreed) > 0
    ? ` Two methods were compared and disagreed on ${p.disagreed} of ${p.compared} lines, which is why those have no stamp.`
    : p.verified_against ? " A second method agreed with every line it could compare." : "");
  return `timed by ${by} \u2014 a machine's proposal, nothing is saved yet: press \u25b6 on the first line`
    + (left ? `. ${left} line${left > 1 ? "s" : ""} could not be placed and kept no stamp.` : ".")
    + checked
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
// and which actions may be offered. The rules are §9.21's — the user's words are not lrclib's to
// replace, and there is nothing to reject when no entry was matched.
export function lyricsPanelState(d) {
  const mine = d.owner === "user";
  const words = Boolean(d.text);
  return {
    where: words ? (d.status === "synced" ? "with timestamps" : "plain text") : "no words yet",
    ownership: mine ? "yours" : words && d.lrclib_id ? `lrclib #${d.lrclib_id}` : null,
    actions: [
      words ? "Edit" : "Write lyrics",
      ...(mine ? [] : ["Look up again"]),
      ...(!mine && d.lrclib_id ? ["Not these words"] : []),
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
    "edited or deleted by you afterwards, and ytalbum never sends it twice.",
    "",
    "OK: publish them. Cancel: nothing leaves this machine.",
  ].join("\n");
}

// What the button says, and why it is not there.
export function publishState(d) {
  const p = (d && d.publish) || {};
  if (p.published) return { show: true, label: "\u2713 published to lrclib", can: false, title:
    `These words were published on ${p.published}. A publish cannot be undone, and the same words are never sent twice.` };
  if (p.can) return { show: true, label: "\u2191 publish to lrclib", can: true, title:
    "Give these words back: LRCLIB has no entry like them, and somebody else looking for this song would find yours.\nIt is public and cannot be undone." };
  if (p.why) return { show: false, label: "", can: false, title: p.why };
  return { show: false, label: "", can: false, title: "" };
}

// Offering an album to MusicBrainz (\u00a79.43). ytalbum never submits anything: the button opens
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
    "ytalbum submits nothing. The form opens in a new tab, signed in as you, and nothing reaches",
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
    "Their recording page is about to open so you can look. ytalbum cannot seed a correction —",
    "the seeding format is for releases, not recordings — so the change is yours to make, and",
    "only if you are sure: a file can be shorter because it was trimmed, or longer because the",
    "upload has an intro. Nothing is sent from here.",
    "",
    "OK: open the recording. Cancel: nothing opens.",
  ].join("\n");
}

// The badge on a field: a button back to what ytalbum derived, a plain badge, or nothing.
// Nothing is offered where nothing was derived — an album from before `auto` was recorded has
// no value to go back to (§9.29).
export function resetKind(provenance, derived) {
  if (provenance !== "user") return provenance ? "badge" : null;
  return derived === undefined || derived === null || derived === "" ? "badge" : "button";
}
