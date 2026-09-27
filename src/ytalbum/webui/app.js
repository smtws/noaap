import { LENGTH, alignNotice, applyStamps, asTime, canSeed, draftNotice, draftText, effectiveId, fixConfirm, fmt,
         fold, foldMap, hits, lengthBand, lengthFix, lineAt, lineStart, lyricsPanelState, maps, markedTrim, movedRow,
         nearMiss, nudged, numberByDisc, oneVideo, ourLength, publishConfirm, publishState, refLength,
         resetKind, roundMark,
         seedConfirm, shifted, sourceChange, stampOf, tapped, tenth, timingNotice, toFileClock, trimOffset, trimTarget }
  from "./logic.mjs";

// ytalbum web UI. No framework, no build step. All server text goes in via textContent.
"use strict";

const $ = (sel) => document.querySelector(sel);
const PROV = { mb: "MB", yt_music: "YT Music", yt_title: "title", playlist: "playlist", user: "you" };
let state = { albums: [], jobs: [], busy: false };
let waitingFor = null; // job id whose result the results panel is waiting for
let openLog = null; // job id whose full log is expanded
let pollTimer = null;

// DOM's replaceChildren turns null into the text "null"; drop empty children first
const kids = (...children) => children.flat(Infinity).filter((c) => c != null && c !== false && c !== "");
const fill = (el, ...children) => el.replaceChildren(...kids(...children));

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Ytalbum": "1" },
    body: JSON.stringify(body),
  };
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

const mine = new Map(); // job id -> { button, label } for jobs started in this page

async function submit(action, body, button = null) {
  if (button) setWorking(button, true);
  try {
    const { job } = await api(`/api/${action}`, body);
    mine.set(job.id, { button, label: job.label });
    state.jobs.unshift(job);
    state.busy = true;
    renderJobs();
    renderActivity();
    schedulePoll(300);
    return job.id;
  } catch (e) {
    if (button) setWorking(button, false);
    toast(e.message, "failed");
    return null;
  }
}

function setWorking(button, on) {
  if (on) {
    button.dataset.label = button.dataset.label || button.textContent;
    button.textContent = "Working…";
  } else if (button.dataset.label) {
    button.textContent = button.dataset.label;
  }
  button.classList.toggle("working", on);
  button.disabled = on;
}

// when a job of ours finishes: free its button, say how it went
function settleJobs() {
  for (const [id, info] of mine) {
    const job = state.jobs.find((j) => j.id === id);
    if (!job || ["queued", "running"].includes(job.state)) continue;
    mine.delete(id);
    if (info.button?.isConnected) setWorking(info.button, false);
    const last = (job.log || []).filter((l) => !l.startsWith("  ")).at(-1) || "";
    const text = { done: "✓", failed: "✗", blocked: "⏸", cancelled: "⏹" }[job.state] + ` ${info.label}` + (last ? ` — ${last}` : "");
    toast(text, job.state);
  }
}

function toast(text, kind = "done") {
  const el = h("div", { class: `toast ${kind === "cancelled" ? "blocked" : kind}`, role: "status" }, text);
  $("#toasts").append(el);
  setTimeout(() => el.classList.add("gone"), kind === "done" ? 5000 : 9000);
  setTimeout(() => el.remove(), kind === "done" ? 5600 : 9600);
}

// the header says what is going on right now
function renderActivity() {
  const running = state.jobs.find((j) => j.state === "running") || state.jobs.find((j) => j.state === "queued");
  const el = $("#activity");
  el.hidden = !state.busy || !running;
  if (!el.hidden) {
    const queued = state.jobs.filter((j) => j.state === "queued").length;
    fill(el, h("span", { class: "dot" }), h("strong", {}, running.label),
      h("span", { class: "muted" }, " ", (running.log || []).at(-1) || "starting…"),
      queued > (running.state === "queued" ? 1 : 0) ? h("span", { class: "badge" }, `+${queued - (running.state === "queued" ? 1 : 0)} queued`) : null,
      cancelButton(running));
  }
}

// -- polling -------------------------------------------------------------------

// A job in the read lane changes nothing on disk, so the album panel must NOT be rebuilt when one
// finishes: an alignment's or a draft's whole purpose is to put text into the open editor, and a
// rebuild would throw that text away before the user could look at it (§9.36). The lane comes from
// the server with every job, so this cannot drift from the list the server actually uses.
let ranWrite = false;

async function poll() {
  try {
    const prevBusy = state.busy;
    state = await api("/api/state");
    if (state.jobs?.some((j) => ["queued", "running"].includes(j.state) && j.lane !== "read")) ranWrite = true;
    if (state.tracks_version && state.tracks_version !== trackIndex.version) loadTracks();
    renderLibrary();
    renderJobs();
    renderSettings();
    renderActivity();
    settleJobs();
    if (waitingFor) {
      const job = state.jobs.find((j) => j.id === waitingFor);
      if (job && !["queued", "running"].includes(job.state)) {
        waitingFor = null;
        showResult(await api(`/api/job?id=${job.id}`));
      }
    }
    if (openLog) renderLog(await api(`/api/job?id=${openLog}`).catch(() => null));
    if (prevBusy && !state.busy) {
      if (ranWrite) refreshAlbumPanel();
      ranWrite = false;
    }
    setOffline(false);
  } catch (e) {
    console.warn("poll failed", e);
    setOffline(true);
  }
  schedulePoll(offline ? 3000 : state.busy || waitingFor ? 700 : 8000);
}

let offline = false;

function setOffline(on) {
  if (on === offline) return;
  offline = on;
  $("#offline").hidden = !on;
}

function schedulePoll(ms) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(poll, ms);
}

// -- library ---------------------------------------------------------------------

let gridShows = "";
let artistFilter = null;
let libFilter = "";
let trackIndex = { version: null, albums: {} }; // artist+title per album, for filtering by song

async function loadTracks() {
  const wanted = state.tracks_version;
  try {
    const got = await api("/api/tracks");
    if (got.version !== trackIndex.version) {
      trackIndex = got;
      renderLibrary();
    }
  } catch {
    trackIndex = { version: wanted, albums: {} }; // do not hammer the server on a failure
  }
}

// ignore case, accents and punctuation, so "njord" finds "Dreams of Njǫrð".
// NFD handles the combining marks; these letters are separate characters and never decompose.

// Where each term sits in the original string, as [start, end) ranges, merged and sorted.
function matchRanges(text, terms) {
  const ranges = [];
  for (const m of maps(text)) {
    for (const term of terms) {
      for (let at = m.folded.indexOf(term); at !== -1; at = m.folded.indexOf(term, at + 1)) {
        ranges.push([m.from[at], m.from[at + term.length - 1] + 1]);
      }
    }
  }
  ranges.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged = [];
  for (const r of ranges) {
    const last = merged.at(-1);
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else merged.push([...r]);
  }
  return merged;
}

// The matched part of a string, wrapped in <mark>; plain text when nothing is being filtered.
function marked(text) {
  const terms = libFilter ? fold(libFilter).split(" ").filter(Boolean) : [];
  const ranges = terms.length ? matchRanges(text, terms) : [];
  if (!ranges.length) return text;
  const out = [];
  let at = 0;
  for (const [from, to] of ranges) {
    if (from > at) out.push(text.slice(at, from));
    out.push(h("mark", {}, text.slice(from, to)));
    at = to;
  }
  if (at < text.length) out.push(text.slice(at));
  return out;
}

// An album matches by its own name, or because a song in it does — "pers" finds Perséfone
// inside Vol. 3, not only "Nocturnal Whispers".
const TRACK = { id: 0, artist: 1, title: 2, done: 3, start: 4, end: 5 }; // rows from /api/tracks

function matchingRows(a, terms) {
  return (trackIndex.albums[a.id] || []).filter((r) => hits(terms, `${r[TRACK.artist]} ${r[TRACK.title]}`));
}

function matchingTracks(a, terms) {
  return matchingRows(a, terms).map((r) => `${r[TRACK.artist]} — ${r[TRACK.title]}`);
}

function shownAlbums() {
  let byArtist = artistFilter ? state.albums.filter((a) => a.albumartist === artistFilter) : state.albums;
  if (lengthOnly) byArtist = byArtist.filter((a) => a.length);
  if (!libFilter) return byArtist.map((a) => ({ ...a, matches: null }));
  const terms = fold(libFilter).split(" ").filter(Boolean);
  const out = [];
  for (const a of byArtist) {
    const own = hits(terms, `${a.albumartist} ${a.album} ${a.year || ""}`);
    const songs = matchingTracks(a, terms);
    if (own || songs.length) out.push({ ...a, matches: own ? null : songs });
  }
  return out;
}

let lengthOnly = false;

function renderLengthFilter() {
  const flagged = (artistFilter ? state.albums.filter((a) => a.albumartist === artistFilter) : state.albums).filter((a) => a.length);
  const button = $("#length-filter");
  if (!flagged.length && !lengthOnly) {
    button.hidden = true;
    return;
  }
  button.hidden = false;
  button.textContent = lengthOnly ? "⏱ show all albums" : `⏱ ${flagged.length} with odd lengths`;
  button.setAttribute("aria-pressed", String(lengthOnly));
}

$("#length-filter").addEventListener("click", () => {
  lengthOnly = !lengthOnly;
  renderLibrary();
});

function showArtist(name) {
  artistFilter = name;
  $("#libtitle").textContent = name || "Library";
  $("#artist-actions").hidden = !name;
  renderLibrary();
  $("#grid").querySelector(".card")?.focus();
}

function renderLibrary() {
  const shown = shownAlbums().length;
  const all = (artistFilter ? state.albums.filter((a) => a.albumartist === artistFilter) : state.albums).length;
  const songMatches = shownAlbums().reduce((n, a) => n + (a.matches?.length || 0), 0);
  $("#libpath").textContent = libFilter
    ? `${shown} of ${all} albums` + (songMatches ? `, ${songMatches} track${songMatches > 1 ? "s" : ""}` : "")
    : artistFilter
      ? `${all} albums`
      : state.library || "";
  $("#empty").textContent = lengthOnly
    ? "No album here is far off the length its songs are known to have."
    : libFilter
      ? `Nothing in the library matches “${libFilter}”.`
      : "Nothing here yet. Paste a playlist URL or type an artist above.";
  const grid = $("#grid");
  const signature = JSON.stringify(shownAlbums()) + artistFilter + libFilter + lengthOnly + trackIndex.version;
  if (signature !== gridShows) {
    // rebuilding throws away the focused card, which would break arrow-key navigation
    const focused = document.activeElement?.closest?.("#grid .card")?.dataset.id;
    gridShows = signature;
    fill(grid, shownAlbums().map(card));
    // preventScroll: a rebuild must not drag the viewport to the focused card - it would
    // pull an open album editor out of view whenever a download changes something
    if (focused) grid.querySelector(`.card[data-id="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
  }
  $("#empty").hidden = shownAlbums().length > 0;
  renderPlayMatches();
  renderLengthFilter();
  renderRail();
}

// A rail of the initials in view: 185 albums are a lot of scrolling, and the grid is sorted
// by artist, so the first album of each letter is a place worth jumping to.
const initial = (name) => {
  const c = fold(name).trim()[0] || "#";
  return /[0-9]/.test(c) ? "#" : c.toUpperCase();
};

function renderRail() {
  const rail = $("#rail");
  const albums = shownAlbums();
  const letters = [];
  for (const a of albums) {
    const letter = initial(a.albumartist);
    if (letter !== letters.at(-1)?.letter) letters.push({ letter, id: a.id });
  }
  rail.hidden = letters.length < 3;  // pointless for a handful of albums
  if (rail.hidden) return;
  fill(rail, letters.map(({ letter, id }) =>
    h("button", { type: "button", title: `Jump to ${letter}`, onclick: () => jumpTo(id) }, letter)));
}

function jumpTo(id) {
  const card = $("#grid").querySelector(`.card[data-id="${CSS.escape(id)}"]`);
  if (!card) return;
  card.scrollIntoView({ behavior: "smooth", block: "start" });
  card.focus({ preventScroll: true });  // arrow keys carry on from there
}

// back to the top, once there is enough below to lose your place in
const toTop = $("#to-top");
toTop.addEventListener("click", () => {
  window.scrollTo({ top: 0, behavior: "smooth" });
  $("#libfilter").focus({ preventScroll: true });
});
addEventListener("scroll", () => { toTop.hidden = scrollY < 600; }, { passive: true });

// Only albums that disagree as a whole get a badge: one odd track is normal, half an album
// means the release we matched is not the one we have (or the playlist is not the album).
function lengthBadge(a) {
  if (!a.length) return null;
  const { way, n, of } = a.length;
  const text = { stub: `⏱ ${n} clip${n > 1 ? "s" : ""}`, short: `⏱ ${n}/${of} short`, long: `⏱ ${n}/${of} long` }[way];
  const title = {
    stub: `${n} track${n > 1 ? "s are" : " is"} far shorter than the song — a snippet, a radio edit, or the wrong recording matched`,
    short: `${n} of ${of} tracks fall well under the known length — previews, or the wrong release matched`,
    long: `${n} of ${of} tracks run well over the known length — intros to cut, or the wrong release matched`,
  }[way];
  return h("span", { class: `badge ${way === "long" ? "warn" : "bad"}`, title }, text);
}

function card(a) {
  const cover = a.cover
    ? h("img", { class: "cover", src: `/api/cover?id=${encodeURIComponent(a.id)}&t=${a.done}`, alt: "", loading: "lazy" })
    : h("div", { class: "cover none" }, "♪");
  const status = a.needs_choice ? h("span", { class: "badge warn" }, `${a.needs_choice} need${a.needs_choice > 1 ? "" : "s"} a choice`)
    : a.failed ? h("span", { class: "badge bad" }, `${a.failed} failed`)
    : a.done < a.tracks ? h("span", { class: "badge" }, `${a.done}/${a.tracks}`) : h("span", { class: "badge ok" }, `${a.tracks} tracks`);
  const play = a.done ? h("span", { class: "card-play", role: "button", tabindex: "0", title: "Play album", "aria-label": `Play ${a.album}`,
    onclick: (e) => { e.stopPropagation(); playAlbum(a.id, 0); },
    onkeydown: (e) => { if (e.key === "Enter") { e.stopPropagation(); e.preventDefault(); playAlbum(a.id, 0); } } }, "▶") : null;
  // when the album is only here because a song matched, name the song rather than count it
  const songs = a.matches?.length
    ? h("div", { class: "matchline", title: a.matches.slice(0, 8).join("\n") },
        "♪ ", marked(a.matches[0]), a.matches.length > 1 ? `  +${a.matches.length - 1}` : "")
    : null;
  return h("button", { class: `card${a.id === lastAlbumId ? " current" : ""}`, type: "button", "data-id": a.id,
      onclick: () => openAlbum(a.id),
      title: a.matches?.length ? `${a.albumartist} — ${a.album}\n${a.matches.slice(0, 8).join("\n")}` : `${a.albumartist} — ${a.album}`,
      "aria-keyshortcuts": "Enter P" },
    h("div", { class: "cover-wrap" }, cover, play),
    h("div", { class: "meta" },
      h("div", { class: "title" }, marked(a.album)),
      h("div", { class: "artist link", role: "button", tabindex: "-1", title: `Show only ${a.albumartist}`,
        onclick: (e) => { e.stopPropagation(); showArtist(a.albumartist); } }, marked(a.albumartist)),
      h("div", { class: "info" }, a.year ? `${a.year} ` : "", status, a.mb ? h("span", { class: "badge mb" }, "MB") : null,
        a.lyrics ? h("span", { class: "badge", title: `${a.lyrics} of ${a.tracks} tracks have lyrics` }, `\u266a ${a.lyrics}`) : null,
        lengthBadge(a)),
      songs));
}

// Play what the filter found: the matching songs of each album, or all of an album that
// matched by name — the same thing the cards show.
function playMatches() {
  const terms = fold(libFilter).split(" ").filter(Boolean);
  const wanted = [];
  for (const a of shownAlbums()) {
    const rows = a.matches ? matchingRows(a, terms) : trackIndex.albums[a.id] || [];
    for (const r of rows) {
      if (!r[TRACK.done]) continue;
      wanted.push({ album: a.id, video_id: r[TRACK.id], title: r[TRACK.title], artist: r[TRACK.artist],
        albumName: a.album, start: r[TRACK.start], end: r[TRACK.end] });
    }
  }
  if (!wanted.length) return toast("Nothing downloaded among the matches", "blocked");
  queue = wanted;
  playIndex(0);
}

function renderPlayMatches() {
  const n = libFilter ? shownAlbums().reduce((sum, a) => sum + (a.matches?.length ?? (trackIndex.albums[a.id] || []).filter((r) => r[TRACK.done]).length), 0) : 0;
  const button = $("#play-matches");
  button.hidden = !n;
  button.textContent = `▶ Play ${n} track${n > 1 ? "s" : ""}`;
}

$("#play-matches").addEventListener("click", playMatches);

$("#libfilter").addEventListener("input", (e) => {
  libFilter = e.target.value.trim();
  renderLibrary();
  if (!$("#album").hidden) markAlbumFields();
});
// Enter jumps into the results; Escape clears the filter before anything else closes
$("#libfilter").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("#grid").querySelector(".card")?.focus();
  else if (e.key === "Escape" && libFilter) {
    e.stopPropagation();
    e.target.value = libFilter = "";
    renderLibrary();
    if (!$("#album").hidden) markAlbumFields();
  }
});

// grid: arrows move, Enter opens, P plays
$("#grid").addEventListener("keydown", (e) => {
  const card = e.target.closest(".card");
  if (!card) return;
  const cards = [...document.querySelectorAll("#grid .card")];
  const index = cards.indexOf(card);
  const perRow = Math.max(1, cards.filter((c) => c.offsetTop === cards[0].offsetTop).length);
  const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: perRow, ArrowUp: -perRow, Home: -index, End: cards.length - 1 - index }[e.key];
  if (step !== undefined) {
    e.preventDefault();
    cards[Math.min(Math.max(index + step, 0), cards.length - 1)].focus();
  } else if (e.key.toLowerCase() === "p") {
    e.preventDefault();
    playAlbum(card.dataset.id, 0);
  }
});

// -- one album: view and edit ---------------------------------------------------------

let currentAlbum = null;
let lastAlbumId = (() => { try { return localStorage.getItem("ytalbum-last"); } catch { return null; } })();

// closing the editor hands focus back to the album's tile, so the keyboard keeps working
function closeAlbum(focusId = currentAlbum?.source_id) {
  $("#album").hidden = true;
  currentAlbum = null;
  const grid = $("#grid");
  const tile = focusId && grid.querySelector(`.card[data-id="${CSS.escape(focusId)}"]`);
  (tile || grid.querySelector(".card"))?.focus();
}

function markAlbum(id) {
  lastAlbumId = id;
  try { localStorage.setItem("ytalbum-last", id); } catch { /* private mode */ }
  for (const card of document.querySelectorAll("#grid .card")) card.classList.toggle("current", card.dataset.id === id);
}

async function openAlbum(id) {
  markAlbum(id);
  if (currentAlbum?.source_id !== id) openLyrics.clear(); // another album, another set of panels
  try {
    currentAlbum = await api(`/api/album?id=${encodeURIComponent(id)}`);
  } catch (e) {
    return alert(e.message);
  }
  renderAlbum();
  $("#album").scrollIntoView({ behavior: "smooth", block: "start" });
}

const rowKey = (t) => [t.number, t.artist, t.title, t.state, t.in_source].join("|");

async function refreshAlbumPanel() {
  if (!currentAlbum) return;
  const before = new Map(currentAlbum.tracks.map((t) => [t.video_id, rowKey(t)]));
  try {
    currentAlbum = await api(`/api/album?id=${encodeURIComponent(currentAlbum.source_id)}`);
  } catch { return; /* album moved or gone */ }
  renderAlbum();
  for (const tr of document.querySelectorAll("#album tbody tr")) {
    const t = currentAlbum.tracks.find((x) => x.video_id === tr.dataset.id);
    if (t && before.get(t.video_id) !== rowKey(t)) tr.classList.add("changed");
  }
}

// -- offering things to MusicBrainz (\u00a79.43) -------------------------------------------------
//
// Both of these open one of *their* pages. ytalbum holds no MusicBrainz credentials and submits
// nothing: a seeded form is a form with the boxes filled in, and the person is signed in as
// themselves in their own browser. Everything else here is about saying that first.

function seedButton(p) {
  const { can, why } = canSeed(p);
  // An album MusicBrainz already has needs no explanation — the MB badge is the explanation. The
  // other refusals get one visible line, because a button that is simply absent teaches nobody why.
  if (!can) {
    return why && !p.mbid ? h("span", { class: "muted seed-why", title: why }, why) : null;
  }
  return h("button", { class: "quiet", type: "button",
    title: "Open MusicBrainz's release editor with this album filled in. Nothing is submitted: you review it there, signed in as you.",
    onclick: (e) => seedMusicBrainz(p, e.currentTarget) }, "Add to MusicBrainz");
}

async function seedMusicBrainz(p, button) {
  if (!confirm(seedConfirm(p))) return;
  setWorking(button, true);
  try {
    const seed = await api(`/api/mbseed?id=${encodeURIComponent(p.source_id)}`);
    // a form, not a link: the seeding format is a POST, and it must land in a tab of the user's own
    const form = h("form", { method: "POST", action: seed.url, target: "_blank", hidden: true },
      ...Object.entries(seed.fields).map(([name, value]) => h("input", { type: "hidden", name, value })));
    document.body.append(form);
    form.submit();
    form.remove();
    toast("MusicBrainz opened in a new tab \u2014 nothing was submitted", "done");
  } catch (e) {
    toast(e.message, "failed");
  } finally {
    setWorking(button, false);
  }
}

function provBadge(p) {
  return p ? h("span", { class: `badge ${p === "mb" ? "mb" : p === "user" ? "user" : ""}` }, PROV[p] || p) : null;
}

// A value you overrode is yours until you say otherwise — so the badge that says "you" is also the
// way back to what ytalbum found. Nothing is offered where nothing was derived (DESIGN.md §9.29).
function resetMark(plan, track, name, label = null) {
  const owner = track || plan;
  const derived = owner.auto?.[name];
  const kind = resetKind(owner.provenance?.[name], derived);
  if (kind !== "button") {
    return owner.provenance?.[name] === "user"
      ? h("span", { class: "badge user", title: "Yours. ytalbum derived nothing for this field, so there is nothing to go back to." }, PROV.user)
      : provBadge(owner.provenance?.[name]);
  }
  const what = label || name;
  return h("button", { class: "badge user reset", type: "button",
    title: `Yours${label ? ` (${label})` : ""}. Click to go back to what ytalbum found: “${derived}” — it is then ytalbum's again, and an update or repair may change it.`,
    onclick: (e) => resetField(plan, track, name, e.currentTarget, `${what} → “${derived}”`) }, `${PROV.user} \u21ba`);
}

async function resetField(plan, track, name, button, what) {
  if (name === "source") {
    // going back is a source change like any other, and costs the same (§9.34)
    const change = sourceChange(track, null);
    if (change && !confirm(change.lines.join("\n"))) return;
  }
  const edits = track ? { tracks: [{ video_id: track.video_id, reset: [name] }] } : { reset: [name] };
  const id = await submit("edit", { id: plan.source_id, edits }, button);
  if (id == null) return;
  const job = await jobSettled(id);
  if (!job || job.state !== "done") return;
  toast(`reset ${what}`, "done");
  await refreshAlbumPanel();
}

// -- where a track's audio comes from (§9.34) ---------------------------------------------
//
// The playlist's video stays the track's identity — its place, its name, its match. This only
// says which recording to take the audio from, for the case the playlist holds the film cut and
// the song exists on its own. Everything the old file carried goes with the switch, so the
// confirm says which marks it is about to clear before anything is fetched.
const openSource = new Set();

function sourceMark(p, t) {
  const own = t.source_override;
  return h("button", { class: "quiet small src-pick" + (own ? " on" : ""), type: "button",
    title: own ? `Audio from ${own}, not the playlist's ${t.video_id} \u2014 click to change it or go back`
      : "Take the audio from another video \u2014 the same song without the film around it",
    onclick: (e) => toggleSource(e.currentTarget, p, t) }, "\u21c4");
}

function toggleSource(button, p, t) {
  const row = button.closest("tr");
  const open = panelUnder(row, "source");
  if (open) {
    openSource.delete(t.video_id);
    return open.remove();
  }
  lastPanelUnder(row).after(h("tr", { class: "source", "data-id": t.video_id },
    h("td", { colspan: "8" }, sourcePanel(p, t))));
  openSource.add(t.video_id);
  document.querySelector(`tr.source[data-id="${CSS.escape(t.video_id)}"] input`)?.focus();
}

function closeSource(el, t) {
  openSource.delete(t.video_id);
  el.closest("tr.source")?.remove();
}

function sourcePanel(p, t) {
  const id = effectiveId(t);
  const note = h("div", { class: "muted source-note" });
  const field = h("input", { type: "text", name: "source", value: t.source_override || "",
    placeholder: "YouTube URL or video id", "aria-label": `audio source of ${t.title}`, size: 34,
    onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); e.currentTarget.parentElement.querySelector("button.use").click(); } } });
  return h("div", { class: "source-panel" },
    h("div", { class: "muted" }, `${t.artist} \u2014 ${t.title} \u00b7 audio from `,
      h("a", { href: `https://www.youtube.com/watch?v=${id}`, target: "_blank", rel: "noopener" }, id),
      t.source_override ? " \u2014 yours, not the playlist's video " : " \u2014 the playlist's own video",
      t.source_override ? resetMark(p, t, "source", "audio source") : null),
    h("div", { class: "panel-actions" }, field,
      h("button", { class: "small use", type: "button", onclick: (e) => useSource(e.currentTarget, p, t, field.value, note) }, "Use this video"),
      h("button", { class: "quiet small", type: "button", onclick: (e) => closeSource(e.currentTarget, t) }, "Cancel")),
    note,
    h("div", { class: "muted" }, "The track keeps its place, its name and your words \u2014 only the audio is fetched again, "
      + "from the video you name here. Its length becomes the one the \u23f1 chip is measured against."));
}

function useSource(button, p, t, text, note) {
  const value = (text || "").trim();
  const chosen = value ? oneVideo(value) : null;
  note.classList.remove("bad");
  if (value && !chosen) {
    note.classList.add("bad");
    note.textContent = "That is not a single video. Paste the watch link of one video, or its 11-character id \u2014 "
      + "a playlist or a channel cannot be a track's source.";
    return;
  }
  const change = sourceChange(t, chosen === t.video_id ? null : chosen);
  if (!change) {
    note.textContent = "That is already where this track's audio comes from.";
    return;
  }
  if (!confirm(change.lines.join("\n"))) return;
  submit("edit", { id: p.source_id, edits: { tracks: [{ video_id: t.video_id, source: change.back ? "" : change.to }] } }, button);
  closeSource(button, t);
}

// The .lrc file beside the track is the original; the tag is a copy of it, so what is
// shown here is what a player reads.
const HAS_WORDS = (t) => t.lyrics === "synced" || t.lyrics === "plain";
const openLyrics = new Set(); // video ids whose lyrics panel is open, so a refresh keeps them

function lyricsMark(p, t) {
  if (t.state !== "done") return null; // no file yet, so nothing to put words beside
  const title = HAS_WORDS(t)
    ? (t.lyrics === "synced" ? "Lyrics with timestamps — click to read or edit" : "Lyrics without timestamps — click to read or edit")
    : t.lyrics === "instrumental"
      ? "LRCLIB has no words for this one (its entry is marked instrumental) — click to write your own"
      : "No lyrics — click to write them";
  return h("button", { class: "quiet small lyr" + (HAS_WORDS(t) ? "" : " empty"), type: "button", title,
    onclick: (e) => toggleLyrics(e.currentTarget, p, t) }, t.lyrics === "instrumental" && !HAS_WORDS(t) ? "no words" : "\u266a");
}

// A track row can carry more than one panel under it (the words, and where the audio comes
// from), so neither may assume it is the immediate next row.
const PANEL = /^(lyrics|source)$/;
const panelUnder = (row, kind) => {
  for (let n = row.nextElementSibling; n && [...n.classList].some((c) => PANEL.test(c)); n = n.nextElementSibling) {
    if (n.classList.contains(kind)) return n;
  }
  return null;
};
const lastPanelUnder = (row) => {
  let last = row;
  for (let n = row.nextElementSibling; n && [...n.classList].some((c) => PANEL.test(c)); n = n.nextElementSibling) last = n;
  return last;
};

async function toggleLyrics(button, p, t) {
  const row = button.closest("tr");
  const open = panelUnder(row, "lyrics");
  if (open) {
    openLyrics.delete(t.video_id);
    return open.remove();
  }
  button.classList.add("working");
  try {
    lastPanelUnder(row).after(await lyricsRow(p, t));
    openLyrics.add(t.video_id);
  } catch (e) {
    toast(e.message, "failed");
  }
  button.classList.remove("working");
}

// A poll, a save or any other refresh rebuilds the table; an open panel has to come back with
// it, or reading along while a job runs would close the words in front of you.
async function reopenLyrics() {
  const p = currentAlbum;
  if (!p) return;
  for (const id of [...openLyrics]) {
    const row = document.querySelector(`#album tbody tr[data-id="${CSS.escape(id)}"]`);
    const track = p.tracks.find((x) => x.video_id === id);
    if (!row || !track) {
      openLyrics.delete(id);
      continue;
    }
    if (panelUnder(row, "lyrics")) continue;
    try {
      lastPanelUnder(row).after(await lyricsRow(p, track));
    } catch { /* the album moved or the track is gone; the next render tidies up */ }
    if (currentAlbum !== p) return; // the user opened another album meanwhile
  }
}

async function lyricsRow(p, t, editing = false, draft = null) {
  const d = await api(`/api/lyrics?id=${encodeURIComponent(p.source_id)}&v=${encodeURIComponent(t.video_id)}`);
  // a draft is not on the disk and must not look as if it were: it opens the editor over whatever
  // the server has, and only a Save puts it anywhere (§9.37)
  if (draft) Object.assign(d, { text: draft.text, words_by: draft.by, draft: draft.notice });
  return h("tr", { class: "lyrics", "data-id": t.video_id }, h("td", { colspan: "8" }, lyricsPanel(p, t, d, editing)));
}

// Reading and writing are the same panel: the .lrc beside the track is the original either way,
// and saving here does exactly what the ownership contract does for a file edited on disk.
function lyricsPanel(p, t, d, editing) {
  const { where, actions } = lyricsPanelState(d);
  const head = h("div", { class: "muted" }, `${t.artist} — ${t.title} · ${where}`,
    d.words_by ? h("span", { class: "badge warn", title: `These words were drafted by ${d.words_by} and are a machine's guess.` },
      `words by ${d.words_by.split("/")[0]}`) : null,
    d.timed_by ? h("span", { class: "badge", title: `The words are yours; these timestamps were placed by ${d.timed_by}.` },
      `timed by ${d.timed_by.split("/")[0]}`) : null,
    d.owner === "user"
      ? h("span", { class: "badge", title: "Your words. A lyrics run never replaces them — delete them to let LRCLIB answer again." }, "yours")
      : d.lrclib_id && d.text  // the id is kept after a clear (it is how a file is recognised as ours), but with no words it would read as if lrclib had some
        ? h("a", { href: `https://lrclib.net/api/get/${d.lrclib_id}`, target: "_blank", rel: "noopener", title: "the entry these words come from" }, ` \u00b7 lrclib #${d.lrclib_id}`)
        : null);
  const stale = timingNotice(d);
  const notice = stale
    ? h("div", { class: "timing-note", title: "Nothing was changed: the words and their timestamps are as you left them. "
      + "Saving them here says they are for this file." }, `\u26a0 ${stale} \u2014 save them again to say they are for this one`)
    : null;
  if (editing) return h("div", { class: "lyrics-panel" }, head, notice, lyricsEditor(p, t, d));
  return h("div", { class: "lyrics-panel" }, head, notice,
    d.text ? lyricsLines(p, t, d.text) : h("pre", {}, t.lyrics === "instrumental"
      ? "LRCLIB has no words for this recording. You can write them yourself."
      : "No .lrc beside this track. Write the words here, or let a lyrics run look them up."),
    nearMissLine(p, t, d),
    h("div", { class: "lyrics-actions" },
      h("button", { class: "quiet small", type: "button", onclick: (e) => editLyrics(e.currentTarget, p, t, true) },
        actions[0]),
      // only where there is nothing to lose: a draft would otherwise overwrite words somebody has
      canDraft(t) ? h("button", { class: "quiet small", type: "button",
        title: `Ask ${providerFor("transcribe")} what it hears and put that in the editor as a draft.\n`
          + "It is a machine's guess — half a song for some tracks — and nothing is saved until you save it."
          + (sendsAudio("transcribe") ? `\nThe audio of this track is sent to ${providerFor("transcribe")}.` : ""),
        onclick: (e) => draftWords(e.currentTarget, p, t) }, `\u270e draft the words`) : null,
      // the other direction: words of the user's that lrclib has no equal of can be given back
      // (\u00a79.42). Public and irrevocable, so the button says so and the confirm says more.
      publishButton(p, t, d),
      // not offered for words of the user's: those are not lrclib's to replace, and the editor's
      // Delete is the way to let it answer again
      !actions.includes("Look up again") ? null : h("button", { class: "quiet small", type: "button",
        title: "Ask LRCLIB about this one track again, with its title, artist and the length of the file as they are now",
        onclick: (e) => lyricsTrack(e.currentTarget, p, t, false) }, "Look up again"),
      !actions.includes("Not these words") ? null : h("button", { class: "quiet small", type: "button",
        title: `Wrong song: LRCLIB #${d.lrclib_id} is not this recording. It is never offered for this track again, and the next best match is taken if one fits.`,
        onclick: (e) => lyricsTrack(e.currentTarget, p, t, true) }, "Not these words")));
}

function publishButton(p, t, d) {
  const state = publishState(d);
  // Where the words may not be given back, say so in the panel rather than showing nothing: the
  // reason is the useful part, and it was previously only a tooltip on a button that was never
  // rendered, so nobody could ever read it. The seed button beside it has always worked this way.
  if (!state.show) return state.why ? h("span", { class: "muted publish-why", title: state.why }, state.why) : null;
  return h("button", { class: "quiet small", type: "button", title: state.title, disabled: !state.can,
    onclick: (e) => publishLyrics(e.currentTarget, p, t, d) }, state.label);
}

async function publishLyrics(button, p, t, d) {
  const text = publishConfirm(d);
  if (!text || !confirm(text)) return;
  const id = await submit("publish_lyrics", { id: p.source_id, video_id: t.video_id }, button);
  if (id == null) return;
  const job = await jobSettled(id);
  if (!job || job.state !== "done") return;  // submit() and the job log have already said why
  toast("\u2713 published to lrclib \u2014 thank you", "done");
  openLyrics.add(t.video_id);
  await refreshAlbumPanel();  // the panel comes back with the button saying "published"
}

// An lrclib entry that is nearly this recording (\u00a79.46): what is known about it, and the one
// thing the user can do that ytalbum would not do for them.
function nearMissLine(p, t, d) {
  const got = nearMiss(d);
  if (!got) return null;
  return h("div", { class: "muted near-miss" }, got.say,
    got.action === "check"
      ? h("button", { class: "quiet small", type: "button",
          title: "Align lrclib's words to this file and see whether they belong to it.\n"
            + "Nothing is written unless they do.",
          onclick: (e) => checkNearLyrics(e.currentTarget, p, t) }, "\u2696 check them")
      : got.action === "plain"
      ? h("button", { class: "quiet small", type: "button",
          title: "Put lrclib's words beside this track without their timings. They are lrclib's words,"
            + " and the timestamps would be for another recording.",
          onclick: (e) => takePlainWords(e.currentTarget, p, t, d) }, "take the words as plain text")
      : null);
}

async function checkNearLyrics(button, p, t) {
  const id = await submit("check_lyrics", { id: p.source_id, video_id: t.video_id }, button);
  if (id == null) return;
  const job = await jobSettled(id, 2400);
  if (!job || job.state !== "done") return;
  openLyrics.add(t.video_id);
  await refreshAlbumPanel();
}

async function takePlainWords(button, p, t, d) {
  const entry = d.fit && d.fit.entry;
  if (!entry) return;
  if (!confirm([
    "lrclib's words for this title go beside this track, without their timings.",
    "",
    "They are lrclib's words, not yours, and the panel will say so. The timestamps are left out"
      + " because they belong to a recording of a different length; you can time them yourself,"
      + " or with the ⚖ button if a provider is configured.",
    "",
    "OK: take the words. Cancel: nothing changes.",
  ].join("\n"))) return;
  const id = await submit("take_plain_lyrics", { id: p.source_id, video_id: t.video_id, entry }, button);
  if (id == null) return;
  const job = await jobSettled(id);
  if (!job || job.state !== "done") return;
  openLyrics.add(t.video_id);
  await refreshAlbumPanel();
}

async function lyricsTrack(button, p, t, reject) {
  const id = await submit("lyrics_track", { id: p.source_id, video_id: t.video_id, reject }, button);
  if (id == null) return;
  const job = await jobSettled(id);
  if (!job || job.state !== "done") return;
  openLyrics.add(t.video_id);
  await refreshAlbumPanel(); // the panel comes back with whatever lrclib answered this time
}

// The words being edited are what playback follows while the editor is open (§9.39), so the list
// beside the textarea is drawn from the textarea and redrawn as it changes. `input` does not fire for
// an assignment to `.value`, which is how every tool in here writes, so they all go through
// `editorText` and the listener sees their work too.
const PREVIEW_PAUSE = 150;  // ms: long enough that typing does not redraw on every keystroke

function editorText(area, value) {
  area.value = value;
  area.dispatchEvent(new Event("input"));
}

function lyricsEditor(p, t, d) {
  const area = h("textarea", { class: "lyrics-edit", spellcheck: "false",
    rows: Math.min(26, Math.max(8, d.text.split("\n").length + 2)),
    onkeydown: (e) => editorKey(e, p, t, area) });
  area.value = d.text;
  // read-only, and it writes nothing: Cancel leaves the file the truth again, Save makes the
  // textarea the file, and until then this is the only way to hear whether a proposal fits
  const preview = lyricsLines(p, t, d.text);
  preview.classList.add("preview");
  // it needs a name, or it reads as the saved lyrics shown twice: the point of it is that while the
  // editor is open these are a *different* truth from the file beside the track
  const previewHead = h("div", { class: "muted preview-head" },
    "what you are editing, as it will play — click a line to hear it");
  let pending = null;
  area.addEventListener("input", () => {
    clearTimeout(pending);
    pending = setTimeout(() => {
      preview.replaceChildren(...Array.from(lyricsLines(p, t, area.value).children));
      markLyricLine();  // the moment has not moved, but the stamps under it have
    }, PREVIEW_PAUSE);
  });
  // what a provider proposed, carried to the Save so the plan can record whose clock — and whose
  // words — these are
  const timing = { by: d.timed_by || "", words: d.words_by || "", checked: null };
  const proposal = h("div", { class: "timing-note", hidden: !d.draft }, d.draft ? `\u26a0 ${d.draft}` : "");
  const by = h("input", { type: "text", class: "shift-by", value: "-0.5", size: 5, "aria-label": "seconds to move every stamp by",
    onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); shiftStamps(area, by); } } });
  const nudge = (delta, label) => h("button", { class: "quiet small", type: "button",
    title: `Move this line's stamp by ${label} s and play it from there (Alt+${delta < 0 ? "←" : "→"}${Math.abs(delta) > 0.1 ? " with Shift" : ""})`,
    onclick: () => nudgeStamp(p, t, area, delta) }, label);
  return h("div", { class: "editor-with-preview" }, area,
    h("div", { class: "panel-actions stamp-tools" },
      h("button", { class: "quiet small", type: "button",
        title: "Write the moment you are hearing on this line, in the file's own clock, and move to the next line (Ctrl+Enter).\nPlay the track first.",
        onclick: () => tapStamp(p, t, area) }, "⏱ stamp this line"),
      h("button", { class: "quiet small", type: "button", title: "Play from this line's stamp (Alt+Enter)",
        onclick: () => playLine(p, t, area) }, "▶"),
      nudge(-0.5, "−0.5"), nudge(-0.1, "−0.1"), nudge(0.1, "+0.1"), nudge(0.5, "+0.5"),
      canAlign() ? h("button", { class: "quiet small", type: "button",
        title: "Ask the configured timing provider to place these words on this file's clock.\n"
          + "It writes nothing: the stamps appear here and you decide whether to save them.\n"
          + "Without a GPU this takes a couple of minutes for a four-minute track.",
        onclick: (e) => alignWords(e.currentTarget, p, t, area, proposal, timing) }, "⚖ align these words") : null,
      h("span", { class: "muted stamp-clock" })),
    previewHead, preview,
    proposal,
    h("div", { class: "panel-actions" },
      h("span", { class: "muted" }, "shift every stamp by"), by, h("span", { class: "muted" }, "s"),
      h("button", { class: "quiet small", type: "button",
        title: "Move every timestamped line by that many seconds. Nothing is saved until you press Save.",
        onclick: () => shiftStamps(area, by) }, "shift all")),
    h("div", { class: "lyrics-actions" },
      h("button", { class: "small", type: "button", onclick: (e) => saveLyrics(e.currentTarget, p, t, area.value, timing.by, timing.words, timing.checked) }, "Save"),
      h("button", { class: "quiet small", type: "button", onclick: (e) => editLyrics(e.currentTarget, p, t, false) }, "Cancel"),
      d.text ? h("button", { class: "quiet small danger", type: "button",
        title: "Remove the .lrc beside this track. Its tag goes with it, and a later “look up all again” may fetch LRCLIB's words.",
        onclick: (e) => saveLyrics(e.currentTarget, p, t, "") }, "Delete") : null,
      h("span", { class: "muted" }, "A line like [01:23.4] Words becomes clickable and follows the song.")));
}

// -- stamping the words to the file's clock (§9.35) ------------------------------------------
//
// A trimmed track plays from its untouched original, so the player's display is the video's clock
// while a stamp belongs to the file on disk. Nobody should read one clock and type the other: the
// tap converts, the nudges move a stamp by a tenth or a half and play it back, and the readout
// says what the current moment is *in the file*. The textarea stays the only source of truth —
// each of these rewrites its text, and nothing is saved until Save.

const nowPlaying = (p, t) => (isPlaying(p.source_id, t.video_id) ? queue[qi] : null);

function tapStamp(p, t, area) {
  const playing = nowPlaying(p, t);
  if (!playing) return toast("Play this track first — a stamp is the moment you are hearing", "blocked");
  const got = tapped(area.value, area.selectionStart, toFileClock(audio.currentTime, trimOffset(playing)));
  editorText(area, got.text);
  area.focus();
  area.setSelectionRange(got.caret, got.caret);
}

function currentLine(area) {
  const lines = area.value.split("\n");
  return { lines, i: Math.min(lineAt(area.value, area.selectionStart), lines.length - 1) };
}

function nudgeStamp(p, t, area, delta) {
  const { lines, i } = currentLine(area);
  const got = nudged(lines[i], delta);
  if (!got) return toast("This line has no stamp yet — stamp it first", "blocked");
  lines[i] = got.line;
  editorText(area, lines.join("\n"));
  const caret = lineStart(area.value, i);
  area.focus();
  area.setSelectionRange(caret, caret);
  seekLyric(p, t, got.at);  // hearing it is the point; the seek converts back to the player's clock
}

function playLine(p, t, area) {
  const { lines, i } = currentLine(area);
  const at = stampOf(lines[i]);
  if (at == null) return toast("This line has no stamp yet", "blocked");
  seekLyric(p, t, at);
}

function shiftStamps(area, by) {
  const delta = Number(String(by.value).replace(",", "."));
  if (!Number.isFinite(delta) || delta === 0) return toast("Type how many seconds to move every stamp by, e.g. -2.4", "blocked");
  const got = shifted(area.value, delta);
  if (!got.moved) return toast("There are no stamps to move yet", "blocked");
  editorText(area, got.text);
  toast(`moved ${got.moved} stamp${got.moved > 1 ? "s" : ""} by ${delta > 0 ? "+" : ""}${tenth(delta)} s — nothing is saved until you press Save`);
}

// Only offered where a provider says it can do it; with the default provider (`none`) there is no
// button at all and the page is what it was before any of this existed (§9.36). Each capability asks
// its own slot (§9.40): aligning and drafting can be two different providers, and everything that
// follows from *which* — the name in the confirm, whether the audio leaves, the price — follows the
// slot, never "the provider".
const canAlign = () => (state.settings?.timing?.capabilities || []).includes("align");
const canTranscribe = () => (state.settings?.timing?.capabilities || []).includes("transcribe");
// a draft is only for a track with nothing to lose: no words, or only the note that LRCLIB has none
const canDraft = (t) => canTranscribe() && !HAS_WORDS(t);
const providerFor = (what) =>
  state.settings?.timing?.[what === "align" ? "align_provider" : "draft_provider"]
  || state.settings?.timing?.provider || "the provider";
const sendsAudio = (what) => Boolean(state.settings?.timing?.sends_audio?.[what]);

// Asked once per provider per session, before the first request that leaves the machine. Not a
// setting to be forgotten: the user is told what is about to happen, in the moment it happens.
const told = new Set();

function mayLeave(what, capability) {
  const name = providerFor(capability);
  if (!sendsAudio(capability) || told.has(name)) return true;
  if (!confirm([`The audio of this track is sent to ${name} to ${what}.`, "",
    "It leaves this machine and this network. Local providers (`local`, `http`) never do that.",
    `${name} charges for it — the settings panel shows their list price — and ytalbum never retries,`,
    "so one press is one request.", "", "OK: send it. Cancel: nothing is sent."].join("\n"))) return false;
  told.add(name);
  return true;
}

async function draftWords(button, p, t) {
  if (!mayLeave("write down what it hears", "transcribe")) return;
  const id = await submit("draft", { id: p.source_id, video_id: t.video_id }, button);
  if (id == null) return;
  const job = await jobSettled(id, 4800);
  if (!job || job.state !== "done") return;
  const timed = job.result?.timed;
  if (!timed) return toast("the provider answered with nothing", "failed");
  const row = button.closest("tr.lyrics");
  openLyrics.add(t.video_id);
  row.replaceWith(await lyricsRow(p, t, true,
    { text: draftText(timed), by: job.result.by || "", notice: draftNotice(timed) }));
}

async function alignWords(button, p, t, area, notice, timing) {
  if (!mayLeave("place these words on its clock", "align")) return;
  const id = await submit("align", { id: p.source_id, video_id: t.video_id, text: area.value }, button);
  if (id == null) return;
  notice.hidden = false;
  notice.textContent = "⏳ asking the timing provider — minutes, on a machine without a GPU";
  const job = await jobSettled(id, 4800);  // up to twenty minutes: a CPU box is slow, not broken
  if (!job || job.state !== "done") {
    notice.hidden = true;
    return;  // submit() and the job log have already said why
  }
  const timed = job.result?.timed;
  if (!timed) {
    notice.hidden = true;
    return toast("the timing provider answered with nothing", "failed");
  }
  const got = applyStamps(area.value, timed);
  editorText(area, got.text);
  timing.by = job.result.by || "";
  // what the two methods said about themselves, carried to the Save so the library keeps the
  // evidence and not only the verdict (§9.44)
  timing.checked = timed.parameters || null;
  notice.textContent = `⚠ ${alignNotice(timed, got)}`;
  area.focus();
  area.setSelectionRange(0, 0);
}

function editorKey(e, p, t, area) {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); return tapStamp(p, t, area); }
  if (e.key === "Enter" && e.altKey) { e.preventDefault(); return playLine(p, t, area); }
  if (e.altKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
    e.preventDefault();
    return nudgeStamp(p, t, area, (e.shiftKey ? 0.5 : 0.1) * (e.key === "ArrowLeft" ? -1 : 1));
  }
}

// The readout runs on its own small timer rather than on `timeupdate`, which fires about four
// times a second: a tenth that changes every 250 ms is not a tenth. With no editor open it costs
// one selector lookup.
function renderStampClock() {
  for (const el of document.querySelectorAll(".stamp-clock")) {
    const id = el.closest("tr.lyrics")?.dataset.id;
    const playing = id && currentAlbum && isPlaying(currentAlbum.source_id, id) ? queue[qi] : null;
    if (!playing) {
      el.textContent = "";
      continue;
    }
    const offset = trimOffset(playing);
    // both clocks, but only where they differ: on an untrimmed track one number is the answer
    el.textContent = offset
      ? `in file ${asTime(toFileClock(audio.currentTime, offset))} · player ${asTime(tenth(audio.currentTime))}`
      : `in file ${asTime(toFileClock(audio.currentTime, 0))}`;
    el.title = offset
      ? "The file on disk starts where the trim cut it, and the player is holding the untouched original, "
        + "so its own display counts from the start of the video. A stamp belongs to the first number."
      : "The moment you are hearing, in the file's own clock — which is what a stamp holds.";
  }
}
setInterval(renderStampClock, 100);

// swap the panel between reading and editing without asking the server again
async function editLyrics(button, p, t, editing) {
  const row = button.closest("tr.lyrics");
  try {
    row.replaceWith(await lyricsRow(p, t, editing));
  } catch (e) {
    toast(e.message, "failed");
  }
}

async function saveLyrics(button, p, t, text, timedBy = "", wordsBy = "", checked = null) {
  const id = await submit("save_lyrics",
    { id: p.source_id, video_id: t.video_id, text, timed_by: timedBy, words_by: wordsBy,
      checked: checked || undefined }, button);
  if (id == null) return;
  const job = await jobSettled(id);
  if (!job || job.state !== "done") return; // submit() already showed why
  openLyrics.add(t.video_id);
  await refreshAlbumPanel(); // renders the new ♪ and puts the panel back, with the saved words
}

async function jobSettled(id, tries = 120) {
  for (let i = 0; i < tries; i++) {
    const job = await api(`/api/job?id=${id}`).catch(() => null);
    if (!job) return null;
    if (!["queued", "running"].includes(job.state)) return job;
    await new Promise((r) => setTimeout(r, 250));
  }
  return null;
}

const LRC_LINE = /^\s*\[(\d{1,3}):(\d{2}(?:[.:]\d{1,3})?)\]\s*(.*)$/;

// Timed lines can be clicked: the song jumps there. Checking whether the words and the
// audio still line up is the quickest way to see that a file carries an intro.
function lyricsLines(p, t, text) {
  const lines = text.split("\n").map((line) => {
    const m = LRC_LINE.exec(line);
    if (!m) return h("div", { class: "line" }, line || "\u00a0");
    const at = Number(m[1]) * 60 + parseFloat(m[2].replace(":", "."));
    const jump = () => seekLyric(p, t, at);
    return h("div", { class: "line timed", role: "button", tabindex: "0", title: `Play from ${fmt(at)}`, "data-at": at,
      onclick: jump, onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); jump(); } } },
      h("span", { class: "at" }, fmt(at)), m[3] || "\u00a0");
  });
  return h("div", { class: "lines" }, lines);
}

async function seekLyric(p, t, at) {
  if (!isPlaying(p.source_id, t.video_id)) {
    const i = p.tracks.filter((x) => x.state === "done").findIndex((x) => x.video_id === t.video_id);
    if (i < 0) return toast("This track has not been downloaded yet", "blocked");
    await playAlbum(p.source_id, i);
  }
  const playing = queue[qi];
  // the lyrics were matched against the file as it is on disk; when that file was cut, the
  // player is holding the original, so the trim has to be added back to reach the same spot —
  // the trim the file was *cut* to, not a mark someone is still placing (§9.35)
  const target = at + trimOffset(playing);
  const go = () => { audio.currentTime = target; audio.play().catch(() => {}); };
  if (audio.readyState >= 1) go();
  else audio.addEventListener("loadedmetadata", go, { once: true });
}

function renderAlbum() {
  const p = currentAlbum;
  const panel = $("#album");
  const field = (label, name, value, type = "text") =>
    h("label", {}, h("span", {}, label, " ", resetMark(p, null, name)), h("input", { type, name, value: value ?? "" }));
  const rows = p.tracks.map((t) =>
    h("tr", { "data-id": t.video_id, class: isPlaying(p.source_id, t.video_id) ? "playing" : "" },
      h("td", { class: "play" },
        t.state === "done" ? h("button", { class: "row-play", type: "button", title: "Play from here", "aria-label": `Play ${t.title}`,
          onclick: () => playAlbum(p.source_id, p.tracks.filter((x) => x.state === "done").findIndex((x) => x.video_id === t.video_id)) }, "▶") : null),
      h("td", { class: "num" }, h("div", { class: "cell-row" },
        h("span", { class: "grip", title: "Drag to move this track — or focus it and press Alt+↑ / Alt+↓", "aria-hidden": "true",
          onpointerdown: startRowDrag }, "⋮⋮"),
        h("input", { type: "number", name: "number", class: "num", min: "1", step: "1", value: t.number,
          "aria-label": `position of ${t.title}`, title: "Position — change it and the album keeps your order" }))),
      h("td", {}, h("input", { type: "text", name: "artist", value: t.artist, "aria-label": "artist" })),
      h("td", {}, h("input", { type: "text", name: "title", value: t.title, "aria-label": "title" })),
      h("td", { class: "disc" },
        h("input", { type: "number", name: "disc", class: "disc", min: "1", step: "1", value: t.disc,
          "aria-label": `disc of ${t.title}`, title: "Which disc this track belongs to" })),
      h("td", { class: "trim" }, h("div", { class: "cell-row" },
        h("input", { type: "text", name: "trim_start", value: asTime(t.trim_start), placeholder: "0:00", "aria-label": "cut from the front", size: 5,
          oninput: (e) => suggestEnd(e.currentTarget, t) }),
        h("input", { type: "text", name: "trim_end", value: asTime(t.trim_end), placeholder: "end", "aria-label": "play until", size: 5 }),
        lengthChip(t),
        t.channel ? h("button", { class: "quiet small", type: "button", title: `Apply this trim to every track from ${t.channel} in the library`,
          onclick: (e) => trimChannel(t, e.currentTarget) }, "⇉") : null)),
      h("td", { class: "src" },
        t.provenance.artist === "user" ? resetMark(p, t, "artist", "artist") : null,
        resetMark(p, t, "title", "title"),
        sourceMark(p, t)),
      h("td", { class: "src actions-cell" },
        t.state === "done" ? h("span", { class: "badge ok" }, "✓")
          : t.error_kind === "no_audio_stream" ? h("button", { class: "quiet small", type: "button", title: t.error || "", onclick: (e) => askAudioChoice(p, t, e.currentTarget) }, "no audio — choose")
          : t.state === "failed" ? h("span", { class: "badge bad", title: t.error || "" }, "failed") : h("span", { class: "badge" }, "pending"),
        t.ext === "m4a" ? h("span", { class: "badge", title: "audio taken from the video stream (copied, not re-encoded)" }, "m4a") : null,
        t.in_source ? null : h("span", { class: "badge", title: "no longer in the source playlist" }, "gone"),
        lyricsMark(p, t),
        h("button", { class: "quiet small danger-text", type: "button", title: "Delete this track (file is removed)",
          onclick: (e) => deleteTrack(p, t, e.currentTarget) }, "✕"))));
  const skipped = (p.skipped || []).map((s) => h("li", { class: "muted" }, `${s.title} — ${s.reason}`));
  const gone = p.tracks.filter((t) => !t.in_source);
  fill(panel,
    h("div", { class: "panel-head" },
      h("div", { class: "album-head" },
        h("img", { class: "album-cover", src: `/api/cover?id=${encodeURIComponent(p.source_id)}&t=${p.tracks.filter((t) => t.state === "done").length}`,
          alt: "", title: "Play album", onclick: () => playAlbum(p.source_id, 0), onerror: (e) => { e.currentTarget.hidden = true; } }),
        h("div", {}, h("h2", {},
          h("span", { class: "link", role: "button", tabindex: "0", title: `Show all albums by ${p.albumartist}`,
            onclick: () => { const name = p.albumartist; closeAlbum(); showArtist(name); },
            onkeydown: (e) => { if (e.key === "Enter") { const name = p.albumartist; closeAlbum(); showArtist(name); } } }, p.albumartist),
          ` — ${p.album}`, p.year ? h("span", { class: "muted" }, ` (${p.year})`) : null),
          h("div", { class: "muted" }, `${p.kind.replace("_", " ")} · ${p.tracks.length} tracks · ${p.folder}`))),
      seedButton(p),
      h("button", { class: "quiet", type: "button", onclick: () => closeAlbum() }, "Close")),
    h("form", { id: "albumform", onsubmit: saveAlbum },
      h("div", { class: "fields" }, field("Album artist", "albumartist", p.albumartist), field("Album", "album", p.album), field("Year", "year", p.year, "number")),
      p.provenance.order === "user"
        ? h("div", { class: "muted order-mark" }, "Track order is yours ",
          h("button", { class: "badge user reset", type: "button",
            title: "The order you set is kept through every update. Click to hand it back: nothing is renumbered now, but the next update may put the album in the source's order again.",
            onclick: (e) => resetField(p, null, "order", e.currentTarget, "the track order") }, `${PROV.user} \u21ba`))
        : null,
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", { title: "position in the album" }, "#"), h("th", {}, "Artist"), h("th", {}, "Title"), h("th", { title: "each disc is numbered from 1" }, "disc"), h("th", { title: "cut the front / play until — for label idents and previews" }, "trim"), h("th", {}, "from"), h("th", {}, ""))), h("tbody", {}, rows)),
      skipped.length ? h("details", {}, h("summary", { class: "muted" }, `${skipped.length} skipped`), h("ul", {}, skipped)) : null,
      h("div", { class: "actions" },
        h("button", { type: "submit" }, "Save changes (rename + retag + trim)"),
        h("button", { class: "quiet", type: "button", onclick: (e) => submit("fetch", { urls: [p.source_url] }, e.currentTarget) }, "Re-check source"),
        h("button", { class: "quiet", type: "button",
          title: "Look up the lyrics of every track that has none yet (LRCLIB), as a .lrc file beside it and in its tags.\nShift+click looks up all of them again — lyrics you wrote yourself are kept either way.",
          onclick: (e) => submit("lyrics", { id: p.source_id, refetch: e.shiftKey }, e.currentTarget) }, "Fetch lyrics"),
        h("button", { class: "danger", type: "button", onclick: (e) => deleteAlbum(p, e.currentTarget) }, "Delete album"),
        gone.length ? h("button", { class: "danger", type: "button", onclick: (e) => pruneAlbum(p, gone, e.currentTarget) }, `Remove ${gone.length} track${gone.length > 1 ? "s" : ""} no longer in the playlist`) : null,
        h("a", { href: p.source_url, target: "_blank", rel: "noopener" }, "open on YouTube"))));
  panel.hidden = false;
  markAlbumFields();
  reopenLyrics();
}

// -- reordering by dragging -------------------------------------------------------------
//
// Pointer events, not HTML5 drag-and-drop, because the latter does not exist on touch. The row
// is moved in the table as the pointer passes other rows, so what you see is the arrangement
// you will get; the position column is renumbered on every move, per disc, and a row dropped
// among another disc's rows takes that disc (DESIGN.md §9.32). Nothing is saved until the
// album's save, exactly as a typed position is not.
let rowDrag = null;  // NB the trim handles have their own `dragging`
const movedRows = new Set();  // rows the user has put somewhere since the last save

function rowsOf(tbody) {
  return [...tbody.querySelectorAll("tr")].filter((tr) => tr.dataset.id);
}

// each disc counts from 1 again, so the column never shows two 3s mid-edit
function renumberRows(tbody) {
  const rows = rowsOf(tbody);
  const numbers = numberByDisc(rows.map((tr) => tr.querySelector("[name=disc]").value));
  rows.forEach((tr, i) => { tr.querySelector("[name=number]").value = numbers[i]; });
}

function startRowDrag(event) {
  if (event.button != null && event.button !== 0) return;
  const row = event.currentTarget.closest("tr");
  const tbody = row.parentElement;
  rowDrag = { row, tbody, order: rowsOf(tbody), disc: row.querySelector("[name=disc]").value };
  row.classList.add("dragging");
  // keeps the moves coming when the pointer leaves the row; not every pointer can be captured
  // (a synthetic event, a released touch), and failing to capture must not abort the drag
  try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* moves still arrive on document */ }
  event.preventDefault();  // no text selection, no page scroll on touch
}

function moveRowTo(row, over) {
  if (!over || over === row) return;
  const tbody = row.parentElement;
  const before = rowsOf(tbody).map((tr) => ({ id: tr.dataset.id, disc: tr.querySelector("[name=disc]").value }));
  const after = movedRow(before, row.dataset.id, over.dataset.id);
  if (after === before) return;
  const byId = new Map(rowsOf(tbody).map((tr) => [tr.dataset.id, tr]));
  for (const { id, disc } of after) {
    const tr = byId.get(id);
    tr.querySelector("[name=disc]").value = disc;
    tbody.append(tr);  // appending in the new order is the new order
  }
  movedRows.add(row.dataset.id);  // a moved row keeps its per-disc number often enough to matter
  renumberRows(tbody);
}

document.addEventListener("pointermove", (e) => {
  if (!rowDrag) return;
  const under = document.elementFromPoint(e.clientX, e.clientY)?.closest("tr");
  if (under?.dataset.id && under.parentElement === rowDrag.tbody) moveRowTo(rowDrag.row, under);
});

function endRowDrag(restore) {
  if (!rowDrag) return;
  const { row, tbody, order, disc } = rowDrag;
  rowDrag = null;
  row.classList.remove("dragging");
  if (restore) {
    for (const tr of order) tbody.append(tr);  // put every row back where it was
    row.querySelector("[name=disc]").value = disc;
    renumberRows(tbody);
    toast("move cancelled", "blocked");
  }
}

document.addEventListener("pointerup", () => endRowDrag(false));
document.addEventListener("pointercancel", () => endRowDrag(true));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && rowDrag) endRowDrag(true);
});

// the same move without a mouse: Alt+↑ / Alt+↓ on a focused row
document.addEventListener("keydown", (e) => {
  if (!e.altKey || !["ArrowUp", "ArrowDown"].includes(e.key)) return;
  const row = e.target.closest?.("#album tbody tr[data-id]");
  if (!row) return;
  const sibling = e.key === "ArrowUp" ? row.previousElementSibling : row.nextElementSibling;
  if (!sibling?.dataset.id) return;
  e.preventDefault();
  moveRowTo(row, sibling);
  (e.target.closest("td")?.querySelector(".grip") ? e.target : row.querySelector("[name=number]"))?.focus?.();
});

// The value of an input cannot be highlighted character by character — the whole field is
// tinted instead, so an album opened from a filtered library shows which fields matched.
function markAlbumFields() {
  const terms = libFilter ? fold(libFilter).split(" ").filter(Boolean) : [];
  for (const el of $("#album").querySelectorAll('input[type="text"]')) {
    const maps_ = maps(el.value);
    el.classList.toggle("hit", terms.some((term) => maps_.some((m) => m.folded.includes(term))));
  }
}

function deleteTrack(plan, track, button) {
  const message = `Delete “${track.artist} – ${track.title}”?\n\nThe file is removed and the remaining tracks are renumbered.\nIf the video is still in the playlist, a later update fetches it again.`;
  if (confirm(message)) submit("delete_track", { id: plan.source_id, video_id: track.video_id }, button);
}

function deleteAlbum(plan, button) {
  const n = plan.tracks.length;
  const message = `Delete the album “${plan.albumartist} — ${plan.album}”?\n\n${n} track(s), the cover and the album data are removed from\n${plan.folder}\n\nFiles you put there yourself are kept.`;
  if (confirm(message)) {
    submit("delete_album", { id: plan.source_id }, button).then(() => closeAlbum(null));
  }
}

function pruneAlbum(p, gone, button) {
  const list = gone.map((t) => `  ${t.number}. ${t.artist} – ${t.title}`).join("\n");
  if (confirm(`Delete these files? They are no longer in the YouTube playlist:\n\n${list}`)) submit("prune", { id: p.source_id }, button);
}

// keeps tenths when there are any, so a value set on the player survives a save from the field

const fromTime = (text) => {
  const parts = String(text).trim().split(":");
  if (!text.trim() || parts.some((p) => p.trim() === "" || isNaN(Number(p)))) return null;
  return parts.reduce((acc, p) => acc * 60 + Number(p), 0);
};

// MusicBrainz knows how long the song is: once a start is set, propose where it ends
// only when the known length actually fits this file: MusicBrainz often has another,
// longer version of the same song (live, extended), which would suggest past the end
const usableLength = (t) => t.mb_length && t.duration && t.mb_length < t.duration - 0.5;

// mirrors plan.py's LENGTH_SLACK / LENGTH_BIG / LENGTH_STUB, which are the source of truth

// How far our audio is from what everyone else says the song is. Small differences are
// normal (masters, fades); a big one means an intro to cut, and a file far shorter than the
// song means this is not the song at all — a teaser or a commentary clip.
function lengthChip(t) {
  const ref = refLength(t), ours = ourLength(t);
  if (!ref || !ours) return null;
  const gap = ours - ref;
  const band = lengthBand(ours, ref);
  const stub = band === "stub";
  const klass = { stub: "bad", big: "warn", slack: "", close: "muted" }[band];
  const sources = [t.mb_length ? `MusicBrainz ${asTime(t.mb_length)}` : null, t.lyrics_length ? `LRCLIB ${asTime(t.lyrics_length)}` : null];
  const why = stub ? " — far too short to be this song (a teaser or a commentary clip?)"
    : band === "big" && gap > 0 ? " — an intro or outro to cut?" : "";
  const label = Math.round(Math.abs(gap)) === 0 ? "0:00" : `${gap > 0 ? "+" : "−"}${asTime(Math.round(Math.abs(gap)))}`;
  const title = `${sources.filter(Boolean).join(" · ")} · this file ${asTime(ours)}${why}`;
  // Where the gap is large and MusicBrainz knows this recording, the chip is also the way to their
  // page (§9.43). One place for one fact: a second badge would have printed the same two numbers
  // beside it with the opposite implication, which is how the first version of this looked.
  const fix = lengthFix(t);
  if (!fix) return h("span", { class: `len ${klass}`, title }, label);
  return h("button", { class: `len ${klass} fix`, type: "button",
    title: `${title} · click to open the recording on MusicBrainz, where their length can be corrected`,
    onclick: () => { if (confirm(fixConfirm(t, fix))) window.open(`${state.settings?.musicbrainz_web || "https://musicbrainz.org"}/recording/${t.mbid}/edit`, "_blank", "noopener"); } },
    label);
}

function suggestEnd(startInput, track) {
  const end = startInput.closest("tr").querySelector("[name=trim_end]");
  if (!usableLength(track) || (end.value && end.dataset.suggested !== "1")) return;
  const start = fromTime(startInput.value);
  if (start == null) return;
  end.value = asTime(start + track.mb_length);
  end.dataset.suggested = "1";
  end.title = "suggested from the MusicBrainz length — change it if it cuts too early";
}

function trimChannel(track, button) {
  const row = button.closest("tr");
  const start = row.querySelector("[name=trim_start]").value;
  const end = row.querySelector("[name=trim_end]").value;
  const what = start || end ? `cut ${start || "0:00"}–${end || "end"}` : "remove the trim";
  if (confirm(`Apply to every track from “${track.channel}” in the library: ${what}?`)) {
    submit("trim_channel", { channel: track.channel, start, end }, button);
  }
}

// a track without a separate audio stream: explain, then let the user decide
function askAudioChoice(plan, track, button) {
  const detail = (track.error || "").replace(/^YouTube offers no separate audio stream \(?/, "").replace(/\)$/, "");
  const message = [
    `“${track.artist} – ${track.title}”`,
    "",
    `Twice in a row, all YouTube offered was: ${detail || "a combined video stream"}.`,
    "Usually that means an old or low-quality upload that never had a separate audio track.",
    "",
    "But it can also be temporary — YouTube sometimes withholds the audio formats for a while.",
    "If this is a normal, recent video, close this and press “Re-check source” first:",
    "a later attempt often gets the full-quality Opus.",
    "",
    "OK: take the audio out of that video. It is copied, not re-encoded, and saved as .m4a —",
    "the best available here, but audibly below your other tracks, and the choice sticks.",
    "",
    "Cancel: leave the track out. You can decide later; nothing is lost.",
  ].join("\n");
  if (confirm(message)) {
    submit("edit", { id: plan.source_id, edits: { tracks: [{ video_id: track.video_id, audio_choice: "combined" }] } }, button);
  }
}

function saveAlbum(ev) {
  ev.preventDefault();
  const form = ev.target;
  const edits = {
    album: form.album.value, albumartist: form.albumartist.value, year: form.year.value,
    tracks: [...form.querySelectorAll("tbody tr")].map((tr) => ({
      video_id: tr.dataset.id,
      moved: movedRows.has(tr.dataset.id),
      number: tr.querySelector("[name=number]").value,
      artist: tr.querySelector("[name=artist]").value,
      title: tr.querySelector("[name=title]").value,
      trim_start: tr.querySelector("[name=trim_start]").value,
      trim_end: tr.querySelector("[name=trim_end]").value,
      disc: tr.querySelector("[name=disc]").value,
    })),
  };
  movedRows.clear();  // the arrangement being saved is the arrangement from now on
  submit("edit", { id: currentAlbum.source_id, edits }, ev.submitter);
}

// -- adding: preview / search / channel results -------------------------------------------

function showResult(job) {
  const panel = $("#results");
  const close = h("button", { class: "quiet", type: "button", onclick: () => { panel.hidden = true; clearTimeout(detailTimer); } }, "Close");
  const r = job.result;
  if (job.state !== "done" || !r) {
    fill(panel, h("div", { class: "panel-head" }, h("h2", {}, "That did not work"), close),
      h("pre", {}, (job.log || []).slice(-8).join("\n")));
  } else if (job.kind === "preview") {
    fill(panel, previewView(r.plan, close, r.album_dir));
  } else {
    fill(panel, pickView(r, close));
  }
  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

// What a fetch would write, before anything is downloaded: the same plan the fetch then writes,
// because it comes from the same code path with `dry` set (DESIGN.md §9.28).
function previewView(p, close, known) {
  const gone = p.tracks.filter((t) => t.in_source === false);
  const rows = p.tracks.map((t) => h("tr", { class: t.in_source === false ? "muted" : "" },
    h("td", { class: "num" }, t.number), h("td", {}, t.artist), h("td", {}, t.title),
    h("td", { class: "src" }, provBadge(t.provenance.artist), provBadge(t.provenance.title),
      t.source_override ? h("span", { class: "badge user", title: `its audio comes from ${t.source_override}, which you chose, not the playlist's ${t.video_id}` }, `audio \u2190 ${t.source_override}`) : null,
      t.in_source === false ? h("span", { class: "badge", title: "no longer in the source playlist; a fetch keeps the file, “Remove gone tracks” deletes it" }, "gone") : null)));
  return [
    h("div", { class: "panel-head" },
      h("div", {}, h("h2", {}, `${p.albumartist} — ${p.album}`, p.year ? ` (${p.year})` : ""),
        h("div", { class: "muted" }, `${p.kind.replace("_", " ")} · ${p.tracks.length} tracks → ${p.folder}`),
        known
          // the server's absolute path is no business of the page: the folder above is the answer
          ? h("div", { class: "muted" }, h("span", { class: "badge" }, "already in the library"),
            `${known.replace(/\\/g, "/").endsWith(p.folder) ? "" : ` · it would move to ${p.folder}`}`
            + `${gone.length ? ` · ${gone.length} track(s) no longer in the source` : ""}`
            + " — downloading fetches what is missing and leaves your edits alone")
          : h("div", { class: "muted" }, "new to the library — nothing is written until you press Download")),
      close),
    h("table", {}, h("tbody", {}, rows)),
    p.skipped?.length ? h("p", { class: "muted" }, `skipped: ${p.skipped.map((s) => `${s.title} (${s.reason})`).join("; ")}`) : null,
    h("div", { class: "actions" },
      h("button", { type: "button", onclick: (e) => { submit("fetch", { urls: [p.source_url] }, e.currentTarget).then(() => setTimeout(() => { $("#results").hidden = true; }, 1200)); } },
        known ? "Download what is missing" : "Download"),
      h("button", { class: "quiet", type: "button", onclick: () => { $("#results").hidden = true; } }, "Cancel")),
  ];
}

function pickView(r, close) {
  const have = new Set(state.albums.map((a) => a.id));
  const groups = (r.groups || []).map((g) => {
    const cards = g.refs.map((ref) => pickCard(ref, have.has(ref.id)));
    const all = h("button", { class: "quiet small", type: "button", onclick: () => {
      const boxes = cards.map((c) => c.querySelector("input"));
      const turnOn = boxes.some((b) => !b.checked);
      boxes.forEach((b) => { b.checked = turnOn; b.closest(".pick").classList.toggle("on", turnOn); });
      updatePickCount();
    } }, "select all");
    return h("div", { class: "group" }, h("h3", {}, g.label, h("span", { class: "badge" }, g.refs.length), all), h("div", { class: "picks" }, cards));
  });
  fillDetails((r.groups || []).flatMap((g) => g.refs));
  return [
    h("div", { class: "panel-head" },
      h("div", {}, h("h2", {}, r.groups?.length ? "Found" : "Nothing found"),
        r.channel ? h("a", { class: "muted", href: r.channel, target: "_blank", rel: "noopener" }, "artist channel on YouTube") : null),
      close),
    r.missing?.length ? h("p", { class: "muted missing" }, `Not found on YouTube: ${r.missing.join(" · ")}`) : null,
    ...groups,
    r.groups?.length ? h("div", { class: "actions sticky" },
      h("button", { type: "button", id: "pick-download", disabled: true, onclick: downloadPicked }, "Download selected"),
      h("span", { id: "pick-count", class: "muted" }, "nothing selected")) : null,
  ];
}

function pickCard(ref, inLibrary) {
  const box = h("input", { type: "checkbox", value: ref.url, onclick: (e) => e.stopPropagation() });
  const label = h("label", { class: `pick${inLibrary ? " have" : ""}`, "data-id": ref.id, onclick: () => setTimeout(updatePickCount) },
    h("div", { class: "pick-cover" }, cover(ref), box),
    h("div", { class: "pick-title" }, ref.title),
    h("div", { class: "muted pick-meta" }, pickMeta(ref), inLibrary ? h("span", { class: "badge ok" }, "in library") : null));
  box.addEventListener("change", () => label.classList.toggle("on", box.checked));
  return label;
}

const cover = (ref) => (ref.thumbnail
  ? h("img", { src: `/api/thumb?u=${encodeURIComponent(ref.thumbnail)}`, alt: "", loading: "lazy" })
  : h("div", { class: "cover none" }, "♪"));

const pickMeta = (ref) => h("span", { class: "meta-text" },
  [ref.count ? `${ref.count} tracks` : ref.unknown ? "" : "…", ref.tab === "search" && ref.artist ? `by ${ref.artist}` : null].filter(Boolean).join(" · ") || "\u00a0");

// details (track count, cover) are not in YouTube's listings: a background runner on the
// server fetches them one playlist at a time, we poll and fill them in as they arrive
let detailTimer = null;

async function fillDetails(refs) {
  clearTimeout(detailTimer);
  const panel = $("#results");
  const todo = () => refs.filter((r) => !r.count && !r.unknown);
  const step = async () => {
    if (panel.hidden || !todo().length) return;
    try {
      const known = await api("/api/details", { refs: todo().map((r) => ({ id: r.id, url: r.url })) });
      for (const ref of refs) {
        const info = known[ref.id];
        const card = panel.querySelector(`.pick[data-id="${CSS.escape(ref.id)}"]`);
        if (!info || !card) continue;
        Object.assign(ref, info);
        card.querySelector(".meta-text").replaceWith(pickMeta(ref));
        if (ref.thumbnail && !card.querySelector("img")) card.querySelector(".cover.none").replaceWith(cover(ref));
      }
    } catch (e) {
      console.warn("details failed", e);
    }
    detailTimer = setTimeout(step, 2000);
  };
  step();
}

function updatePickCount() {
  const n = $("#results").querySelectorAll("input[type=checkbox]:checked").length;
  const button = $("#pick-download");
  if (!button) return;
  button.disabled = n === 0;
  $("#pick-count").textContent = n ? `${n} selected` : "nothing selected";
}

function downloadPicked(e) {
  const urls = [...$("#results").querySelectorAll("input[type=checkbox]:checked")].map((c) => c.value);
  if (!urls.length) return;
  submit("fetch", { urls }, e.currentTarget).then(() => setTimeout(() => { $("#results").hidden = true; }, 1200));
}

// -- jobs ------------------------------------------------------------------------------

function cancelButton(job) {
  if (!["queued", "running"].includes(job.state)) return null;
  const asked = (job.log || []).at(-1) === "cancel requested…";
  return h("button", { class: "quiet cancel", type: "button", disabled: asked, title: "Stop after the current step; finished tracks are kept",
    onclick: async (e) => {
      e.stopPropagation();
      setWorking(e.currentTarget, true);
      try { await api("/api/cancel", { id: job.id }); schedulePoll(200); } catch (err) { toast(err.message, "failed"); }
    } }, asked ? "Stopping…" : "Cancel");
}

function renderJobs() {
  const recent = state.jobs.filter((j) => ["queued", "running"].includes(j.state) || Date.now() / 1000 - (j.finished || 0) < 120).slice(0, 4);
  fill($("#jobs"), recent.map((j) =>
    h("div", { class: `job ${j.state}`, "data-id": j.id },
      h("div", {}, h("strong", {}, j.label), " ", h("span", { class: "badge" }, j.state), " ", cancelButton(j), " ",
        h("button", { class: "quiet", type: "button", onclick: () => { openLog = openLog === j.id ? null : j.id; poll(); } }, openLog === j.id ? "hide log" : "log")),
      openLog === j.id ? h("pre", { id: `log-${j.id}` }) : h("div", { class: "line" }, (j.log || []).at(-1) || ""))));
}

function renderLog(job) {
  const pre = job && document.getElementById(`log-${job.id}`);
  if (pre) { pre.textContent = job.log.join("\n"); pre.scrollTop = pre.scrollHeight; }
}

// -- settings ----------------------------------------------------------------------------

const BROWSER_NAMES = { firefox: "Firefox", chrome: "Chrome", chromium: "Chromium", brave: "Brave", edge: "Edge", vivaldi: "Vivaldi", opera: "Opera" };

function renderSettings() {} // the panel is built when opened, so polling never overwrites what you type

// What each provider row says under its label: what that choice means for the audio, and — for the
// ones that charge — their list price with the date it was read (§9.37). One row per capability
// (§9.40), because the two can be different providers and each has its own answer.
function timingHelp(st, what) {
  const base = what === "align"
    ? "who may place timestamps on the words you have: nobody, a model on this machine (the "
      + "ytalbum[timing] extra), another machine running `ytalbum timing-serve`, or a paid service"
    : "who may write down the words of a track that has none — a guess, offered only where there is "
      + "nothing to lose";
  // whether a second method checks every alignment is worth a sentence: it changes what the user
  // gets (fewer stamps, and only agreed ones) and how long they wait (§9.38)
  const second = what === "align" && st.timing?.verifies
    ? " Every alignment is checked against a second method here, which takes about half again as long;"
      + " lines the two disagree about come back without a stamp."
    : "";
  if (!sendsAudio(what)) return `${base}. Local and http never send anything off this network.${second}`;
  const [price, checked] = st.timing?.price?.[what] || ["", ""];
  return `${base}. ⚠ ${providerFor(what)} receives the audio of every track you use it on.`
    + (price ? ` Their list price was ${price} (checked ${checked}).` : "");
}

// A slot offers only the kinds that could ever do its job: the drafting slot never lists a provider
// that merely aligns, and the aligning slot never lists Deepgram, which transcribes and says so.
const providersFor = (st, what) =>
  ["none", "local", "http", ...(st.timing?.vendors || [])]
    .filter((kind) => kind === "none" || (st.timing?.offers?.[kind] || []).includes(what));

function openSettings() {
  const panel = $("#settings");
  if (!panel.hidden) { panel.hidden = true; return; }
  const st = state.settings;
  if (!st) return;
  const browsers = ["", ...st.browsers];
  if (st.cookies_from_browser && !browsers.includes(st.cookies_from_browser)) browsers.push(st.cookies_from_browser);
  const row = (label, help, input) => h("label", { class: "setting" }, h("span", {}, h("strong", {}, label), h("small", { class: "muted" }, help)), input);
  fill(panel,
    h("div", { class: "panel-head" }, h("h2", {}, "Settings"), h("button", { class: "quiet", type: "button", onclick: () => { panel.hidden = true; } }, "Close")),
    h("form", { id: "settingsform", onsubmit: saveSettings },
      row("Library folder", "where albums are stored (created if missing); existing albums are not moved",
        h("input", { type: "text", name: "library", value: st.library })),
      row("YouTube login", "browser whose YouTube session is used — avoids the bot check, needed for age-restricted videos",
        h("select", { name: "cookies_from_browser" }, browsers.map((b) => h("option", { value: b, selected: b === (st.cookies_from_browser || "") }, b ? BROWSER_NAMES[b.split(":")[0]] || b : "none")))),
      row("MusicBrainz", "look up correct names, years, covers and tracklists",
        h("input", { type: "checkbox", name: "musicbrainz", checked: st.musicbrainz })),
      row("Token helper", "proof-of-origin tokens for streams YouTube withholds; server = started on demand",
        h("select", { name: "pot_mode" }, ["server", "script", "off"].map((m) => h("option", { value: m, selected: m === st.pot_mode }, m)))),
      row("Token server stops after", "minutes without YouTube activity",
        h("input", { type: "number", name: "pot_idle_minutes", min: 1, max: 120, value: st.pot_idle_minutes })),
      row("Parallel YouTube requests", "1–4; more is faster but trips YouTube's bot check sooner",
        h("input", { type: "number", name: "concurrency", min: 1, max: 4, value: st.concurrency })),
      row("Placing words on the clock", timingHelp(st, "align"),
        h("select", { name: "timing_align_provider" }, providersFor(st, "align").map((m) =>
          h("option", { value: m, selected: m === providerFor("align") }, m)))),
      row("Drafting words for a track that has none", timingHelp(st, "transcribe"),
        h("select", { name: "timing_draft_provider" }, providersFor(st, "transcribe").map((m) =>
          h("option", { value: m, selected: m === providerFor("transcribe") }, m)))),
      row("Timing endpoint", "for `http`: http://thatmachine:8770 — the audio never leaves your network",
        h("input", { type: "text", name: "timing_endpoint", value: st.timing?.endpoint || "", placeholder: "http://host:8770" })),
      ...(st.timing?.vendors || []).map((v) => row(`${v} API key`,
        st.timing.keys?.[v] ? "a key is set; type a new one to replace it, or a single space to remove it"
          : `needed for the ${v} provider. It stays on this machine and is never shown again.`,
        h("input", { type: "password", name: `timing_${v}_key`, value: "", autocomplete: "off",
          placeholder: st.timing.keys?.[v] ? "•••••••• (set)" : "" }))),
      h("dl", { class: "info" }, Object.entries(st.info).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
      h("div", { class: "actions" }, h("button", { type: "submit" }, "Save settings"))));
  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function saveSettings(ev) {
  ev.preventDefault();
  const f = ev.target;
  const button = ev.submitter;
  setWorking(button, true);
  try {
    state.settings = await api("/api/settings", {
      library: f.library.value, cookies_from_browser: f.cookies_from_browser.value, musicbrainz: f.musicbrainz.checked,
      pot_mode: f.pot_mode.value, pot_idle_minutes: Number(f.pot_idle_minutes.value), concurrency: Number(f.concurrency.value),
      // the two slots are what the page writes now; `timing_provider` stays in the config as the
      // fallback for whatever was there before, and is not touched from here (§9.40)
      timing_align_provider: f.timing_align_provider.value,
      timing_draft_provider: f.timing_draft_provider.value,
      timing_endpoint: f.timing_endpoint.value.trim(),
      // only sent when something was typed: an empty field means "leave the key as it is"
      ...Object.fromEntries((state.settings.timing?.vendors || [])
        .filter((v) => f[`timing_${v}_key`]?.value)
        .map((v) => [`timing_${v}_key`, f[`timing_${v}_key`].value.trim()])),
    });
    toast("✓ Settings saved — they apply from the next job", "done");
    $("#settings").hidden = true;
    poll();
  } catch (e) {
    toast(e.message, "failed");
  } finally {
    setWorking(button, false);
  }
}

$("#gear").addEventListener("click", openSettings);
$("#artist-all").addEventListener("click", () => showArtist(null));
$("#artist-update").addEventListener("click", (e) => submit("update", { artist: artistFilter, deep: e.shiftKey }, e.currentTarget));
$("#artist-new").addEventListener("click", async (e) => {
  const id = await submit("open", { q: artistFilter }, e.currentTarget);
  if (id) {
    waitingFor = id;
    fill($("#results"), h("p", { class: "muted" }, `Looking for albums by “${artistFilter}” …`));
    $("#results").hidden = false;
  }
});

// "/" jumps to the library filter, the way it does in most things that have one
document.addEventListener("keydown", (e) => {
  if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || e.target?.closest?.("input, select, textarea")) return;
  e.preventDefault();
  $("#libfilter").focus();
  $("#libfilter").select();
});

// Escape closes whichever panel is open, and the album editor gives focus back to its tile
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || e.target?.closest?.("input, select, textarea")) return;
  if (!$("#album").hidden) closeAlbum();
  else if (!$("#results").hidden) { $("#results").hidden = true; clearTimeout(detailTimer); }
  else if (!$("#settings").hidden) $("#settings").hidden = true;
  else if (artistFilter) showArtist(null);
});

// -- player ----------------------------------------------------------------------------

const audio = $("#audio");
let queue = []; // [{ album, video_id, title, artist }]
let qi = -1;

const isPlaying = (albumId, videoId) => qi >= 0 && queue[qi].album === albumId && queue[qi].video_id === videoId;

async function playAlbum(albumId, start = 0) {
  markAlbum(albumId);
  let plan;
  try {
    plan = currentAlbum?.source_id === albumId ? currentAlbum : await api(`/api/album?id=${encodeURIComponent(albumId)}`);
  } catch (e) {
    return toast(e.message, "failed");
  }
  queue = plan.tracks.filter((t) => t.state === "done").map((t) => ({
    album: albumId, video_id: t.video_id, title: t.title, artist: t.artist, albumName: plan.album,
    start: t.trim_start, end: t.trim_end, duration: t.duration, trimmed: t.trimmed,
    mb_length: t.mb_length, lyrics_length: t.lyrics_length, file_length: t.file_length,
    savedStart: t.trim_start, savedEnd: t.trim_end,  // what is on disk, to tell editing from listening
  }));
  if (!queue.length) return toast("Nothing downloaded yet in this album", "blocked");
  playIndex(Math.max(0, start));
}

function playIndex(i) {
  if (i < 0 || i >= queue.length) return;
  qi = i;
  const t = queue[i];
  // A cut file no longer contains what trim_start counts from, so the player would skip the
  // head twice (measured: 8s of a trimmed track were unreachable). It plays the untouched
  // original instead and previews the trim itself, which keeps every number on one clock.
  const uncut = t.trimmed ? "&o=1" : "";
  audio.src = `/api/audio?id=${encodeURIComponent(t.album)}&v=${encodeURIComponent(t.video_id)}${uncut}`;
  audio.play().catch((e) => toast(`Cannot play: ${e.message}`, "failed"));
  $("#player").hidden = false;
  document.body.classList.add("has-player");
  $("#p-cover").src = `/api/cover?id=${encodeURIComponent(t.album)}`;
  $("#p-title").textContent = t.title;
  $("#p-artist").textContent = `${t.artist} · ${t.albumName}`;
  // which file you are hearing, because the marks only make sense against the original
  const source = $("#p-source");
  source.hidden = !t.trimmed;
  source.textContent = t.trimmed ? `playing the untouched original · the file on disk is cut to ${t.trimmed}` : "";
  source.title = t.trimmed ? "Trim points count from the start of the video, so a cut track is played from the original kept in .originals/ — the marks and what you hear are on one clock." : "";
  document.querySelectorAll("#album tbody tr").forEach((tr) => tr.classList.toggle("playing", isPlaying(currentAlbum?.source_id, tr.dataset.id)));
  renderTrim();
  if ("mediaSession" in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({ title: t.title, artist: t.artist, album: t.albumName,
      artwork: [{ src: `/api/cover?id=${encodeURIComponent(t.album)}` }] });
  }
}

// the length often arrives only with the file (older albums have none in the plan)
audio.addEventListener("loadedmetadata", renderTrim);
audio.addEventListener("durationchange", renderTrim);
audio.addEventListener("play", () => { $("#p-play").textContent = "⏸"; });
audio.addEventListener("pause", () => { $("#p-play").textContent = "▶"; });
audio.addEventListener("ended", () => (qi + 1 < queue.length ? playIndex(qi + 1) : null));
audio.addEventListener("timeupdate", () => {
  const t = queue[qi];
  if (t) {  // preview the trim while listening: skip the head, stop at the end
    if (t.start && audio.currentTime < t.start - 0.4 && !dragging) audio.currentTime = t.start;
    if (t.end && audio.currentTime > t.end) {
      // while the end is still being placed, stop on it. Running into the next track would
      // take the unsaved trim with it: the buttons then edit and save the wrong song.
      if (t.end !== t.savedEnd || t.start !== t.savedStart) audio.pause();
      else if (qi + 1 < queue.length) playIndex(qi + 1);
      else audio.pause();
    }
  }
  $("#p-time").textContent = fmt(audio.currentTime);
  $("#p-dur").textContent = fmt(audio.duration);
  if (document.activeElement !== $("#p-pos") && audio.duration) $("#p-pos").value = Math.round((audio.currentTime / audio.duration) * 1000);
});
audio.addEventListener("timeupdate", markLyricLine);
audio.addEventListener("error", () => { if (audio.src) toast("This track cannot be played (moved or deleted?)", "failed"); });

// The line being sung is marked while the song plays: seeing it drift away from what you
// hear is the quickest way to tell that a file carries an intro the timestamps know nothing of.
function markLyricLine() {
  const t = queue[qi];
  for (const row of document.querySelectorAll("#album tr.lyrics")) {
    const playing = t && isPlaying(currentAlbum?.source_id, row.dataset.id);
    const at = audio.currentTime - trimOffset(t);
    let active = null;
    if (playing) for (const line of row.querySelectorAll(".line.timed")) if (Number(line.dataset.at) <= at) active = line;
    const before = row.querySelector(".line.now");
    if (before === active) continue;
    before?.classList.remove("now");
    if (!active) continue;
    active.classList.add("now");
    // scrollIntoView would take the page with it and pull the editor out of view
    const box = row.querySelector(".lines");
    box.scrollTop = active.offsetTop - box.clientHeight / 2 + active.offsetHeight / 2;
  }
}
$("#p-pos").addEventListener("change", (e) => { if (audio.duration) audio.currentTime = (e.target.value / 1000) * audio.duration; });
$("#p-play").addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));
$("#p-prev").addEventListener("click", () => (audio.currentTime > 3 ? (audio.currentTime = 0) : playIndex(qi - 1)));
$("#p-next").addEventListener("click", () => playIndex(qi + 1));
// Chrome infers play/pause from the audio element; Firefox and the desktop's media keys
// (MPRIS) only follow explicit handlers and a playback state that is kept current.
if ("mediaSession" in navigator) {
  const handlers = {
    play: () => audio.play(),
    pause: () => audio.pause(),
    stop: () => { audio.pause(); audio.currentTime = 0; },
    previoustrack: () => (audio.currentTime > 3 ? (audio.currentTime = 0) : playIndex(qi - 1)),
    nexttrack: () => playIndex(qi + 1),
    seekbackward: (e) => { audio.currentTime = Math.max(0, audio.currentTime - (e.seekOffset || 10)); },
    seekforward: (e) => { audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + (e.seekOffset || 10)); },
    seekto: (e) => { if (e.seekTime != null) audio.currentTime = e.seekTime; },
  };
  for (const [action, handler] of Object.entries(handlers)) {
    try {
      navigator.mediaSession.setActionHandler(action, handler);
    } catch {
      /* the browser does not know this action */
    }
  }
  audio.addEventListener("play", () => { navigator.mediaSession.playbackState = "playing"; });
  audio.addEventListener("pause", () => { navigator.mediaSession.playbackState = "paused"; });
  audio.addEventListener("timeupdate", () => {
    if (!navigator.mediaSession.setPositionState || !audio.duration) return;
    navigator.mediaSession.setPositionState({ duration: audio.duration, position: audio.currentTime, playbackRate: audio.playbackRate });
  });
}

// Keys in the page itself, which work whatever the desktop does with the media keys.
document.addEventListener("keydown", (e) => {
  if (qi < 0 || e.ctrlKey || e.metaKey || e.altKey || e.target?.closest?.("input, select, textarea, [contenteditable]")) return;
  const step = e.shiftKey ? 30 : 10;
  const actions = {
    " ": () => (audio.paused ? audio.play() : audio.pause()),
    MediaPlayPause: () => (audio.paused ? audio.play() : audio.pause()),
    ArrowLeft: () => { audio.currentTime = Math.max(0, audio.currentTime - step); },
    ArrowRight: () => { audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + step); },
    n: () => playIndex(qi + 1),
    b: () => (audio.currentTime > 3 ? (audio.currentTime = 0) : playIndex(qi - 1)),
  };
  // arrows belong to the grid while a card has focus
  if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && e.target?.closest?.("#grid")) return;
  const act = actions[e.key];
  if (!act) return;
  e.preventDefault();
  act();
});

// -- trim handles on the player ----------------------------------------------------------

let dragging = null;

function trimLimit() {
  const t = queue[qi];
  const total = t?.duration || audio.duration;
  return Number.isFinite(total) && total > 0 ? total : 0;
}

function renderTrim() {
  const t = queue[qi];
  const total = trimLimit();
  const show = Boolean(t && total);
  for (const id of ["#p-keep", "#p-h-start", "#p-h-end"]) $(id).hidden = !show;
  $("#p-trim-actions").hidden = !show;
  if (!show) return;
  const start = t.start || 0;
  const end = t.end == null ? total : t.end;
  $("#p-keep").style.left = `${(start / total) * 100}%`;
  $("#p-keep").style.right = `${100 - (end / total) * 100}%`;
  $("#p-h-start").style.left = `${(start / total) * 100}%`;
  $("#p-h-end").style.left = `${(end / total) * 100}%`;
  $("#p-h-start").title = `Song starts at ${fmt(start)}`;
  $("#p-h-end").title = `Song ends at ${fmt(end)}`;
  renderTarget(t, total);
  syncTrimInputs(t);
}

function renderTarget(t, total) {
  const box = $("#p-target");
  const { kept, gap, ref } = trimTarget(t, total, t.start, t.end);
  if (kept == null) return void (box.textContent = "");
  const source = t.mb_length ? "MusicBrainz" : "LRCLIB";
  const now = t.file_length ? `now ${asTime(t.file_length)} · ` : "";
  if (gap == null) {
    box.className = "muted p-target";
    box.textContent = `${now}keeping ${asTime(kept)} — nobody knows how long this song is`;
    box.title = "No MusicBrainz or LRCLIB length for this track, so there is nothing to aim at.";
    return;
  }
  const off = Math.abs(gap);
  box.className = "p-target " + (kept < ref * LENGTH.stub ? "bad" : off > LENGTH.big ? "warn" : off > LENGTH.slack ? "" : "good");
  box.textContent = `${now}keeping ${asTime(kept)} · ${source} ${asTime(ref)} · ${gap > 0 ? "+" : "−"}${off.toFixed(1)}s`;
  box.title = off > LENGTH.slack
    ? `Still ${off.toFixed(1)} s ${gap > 0 ? "longer" : "shorter"} than ${source} says the song is. Save when you are happy; the ⏱ mark follows the file, not the marks.`
    : `Within ${LENGTH.slack} s of ${source}'s length — the ⏱ mark goes quiet at this length.`;
}

// the text fields in the album view are the same value: keep them in step
function syncTrimInputs(t) {
  if (currentAlbum?.source_id !== t.album) return;
  const row = document.querySelector(`#album tr[data-id="${CSS.escape(t.video_id)}"]`);
  if (!row) return;
  row.querySelector("[name=trim_start]").value = t.start == null ? "" : asTime(t.start);
  row.querySelector("[name=trim_end]").value = t.end == null ? "" : asTime(t.end);
}

function setTrim(which, seconds) {
  const t = queue[qi];
  if (!t) return;
  const total = trimLimit();
  const marks = markedTrim(t, which, roundMark(seconds, total), total);
  [t.start, t.end] = [marks.start, marks.end];
  renderTrim();
}

for (const [id, which] of [["#p-h-start", "start"], ["#p-h-end", "end"]]) {
  $(id).addEventListener("pointerdown", (e) => {
    dragging = which;
    e.currentTarget.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  $(id).addEventListener("pointermove", (e) => {
    if (dragging !== which) return;
    const rect = $("#p-trim").getBoundingClientRect();
    setTrim(which, ((e.clientX - rect.left) / rect.width) * trimLimit());
  });
  $(id).addEventListener("pointerup", (e) => {
    dragging = null;
    e.currentTarget.releasePointerCapture(e.pointerId);
    const t = queue[qi];
    if (t) audio.currentTime = Math.max((which === "start" ? t.start || 0 : (t.end || trimLimit()) - 3), 0);
  });
  $(id).addEventListener("keydown", (e) => {  // arrows for fine adjustment
    const step = e.shiftKey ? 1 : 0.1;
    const t = queue[qi];
    if (!t || !["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    const current = which === "start" ? t.start || 0 : t.end ?? trimLimit();
    setTrim(which, current + (e.key === "ArrowRight" ? step : -step));
    e.preventDefault();
  });
}

$("#p-set-start").addEventListener("click", () => setTrim("start", audio.currentTime));
$("#p-set-end").addEventListener("click", () => {
  setTrim("end", audio.currentTime);
  audio.pause();  // this is where the song ends: stay here, do not play past the mark
});
$("#p-from-start").addEventListener("click", () => {
  const t = queue[qi];
  if (!t) return;
  // the marks count in the video's timeline, and a cut track is played from its kept original,
  // so the mark is the position to seek to either way
  audio.currentTime = t.start || 0;
  audio.play().catch(() => {});
});
$("#p-trim-clear").addEventListener("click", () => {
  const t = queue[qi];
  if (!t) return;
  t.start = t.end = null;
  renderTrim();
});
$("#p-trim-save").addEventListener("click", (e) => {
  const t = queue[qi];
  if (!t) return;
  [t.savedStart, t.savedEnd] = [t.start, t.end];  // from here on these marks are the saved ones
  submit("edit", { id: t.album, edits: { tracks: [{ video_id: t.video_id, trim_start: t.start == null ? "" : String(t.start), trim_end: t.end == null ? "" : String(t.end) }] } }, e.currentTarget);
});

// -- theme: auto (follow the system) → dark → light, remembered in this browser ---------------

const THEMES = { auto: ["◐", "automatic"], dark: ["☾", "dark"], light: ["☀", "light"] };

function applyTheme(theme) {
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  $("#theme").textContent = THEMES[theme][0];
  $("#theme").title = `Theme: ${THEMES[theme][1]} (click to change)`;
}

function savedTheme() {
  try { return THEMES[localStorage.getItem("ytalbum-theme")] ? localStorage.getItem("ytalbum-theme") : "auto"; } catch { return "auto"; }
}

let theme = savedTheme();
$("#theme").addEventListener("click", () => {
  const order = ["auto", "dark", "light"];
  theme = order[(order.indexOf(theme) + 1) % order.length];
  try { localStorage.setItem("ytalbum-theme", theme); } catch { /* private mode: just this session */ }
  applyTheme(theme);
});
applyTheme(theme);

// -- wiring ------------------------------------------------------------------------------

$("#open").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const q = $("#q").value.trim();
  if (!q) return;
  // A URL is previewed first, because seeing the names before the files are written is the whole
  // point; shift skips it and downloads straight away for someone who does not want to look.
  if (skipPreview && /^https?:/.test(q)) {
    skipPreview = false;
    return void submit("fetch", { urls: [q] }, ev.submitter || $("#open button"));
  }
  const id = await submit("open", { q }, ev.submitter || $("#open button"));
  if (id) {
    waitingFor = id;
    fill($("#results"), h("p", { class: "muted" }, /^https?:/.test(q) ? "Reading from YouTube…" : `Searching for “${q}”…`));
    $("#results").hidden = false;
  }
});
// a form submit carries no modifier state, so it is caught where the modifier is: on the way in
let skipPreview = false;
for (const event of ["click", "keydown"]) {
  $("#open").addEventListener(event, (e) => { skipPreview = e.shiftKey === true; }, true);
}
// plain click: cheap check (one request per album); with shift: read every album fully
$("#update").addEventListener("click", (e) => submit("update", { deep: e.shiftKey }, e.currentTarget));
$("#repair").addEventListener("click", (e) => {
  // repair renames folders and files across the whole library, so it asks first. The wording is
  // README's paragraph about it, because a user pressing this deserves to know it is offline and
  // that nothing is downloaded.
  const message = "Repair the library?\n\n"
    + "Once through every album, offline: performer-only artist names, guest credits moved into "
    + "the title, the album's own name removed from its track titles, one spelling per artist, "
    + "duplicate tracks removed.\n\n"
    + "Folders and files are renamed and tags rewritten. Nothing is downloaded, nothing is deleted, "
    + "and values you edited yourself are kept.\n\n"
    + "The log names every album it changes.";
  if (confirm(message)) submit("repair", {}, e.currentTarget);
});

if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/sw.js").catch(() => {});
poll();
