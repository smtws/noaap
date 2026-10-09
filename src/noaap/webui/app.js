import { CLAIM_LABEL, LENGTH, completeRows, unsavedEdits, alignNotice, applyStamps, asTime, audioRequest, canSeed, claimOffer, draftNotice, draftText,
         editorRows, effectiveId, fixConfirm, fmt,
         fold, foldMap, foldedBoth, hits, hitsIn, lengthBand, lengthFix, lineAt, lineStart, lyricsPanelState, maps, markedTrim, movedRow,
         identifyLines, identifyState, inSlices, offersState, offersToChoose, talkBlock,
         nearMiss, nudged, numberByDisc, oneVideo, ourLength, pollFailureIsOffline, pollPlan,
         POLL_CEILING_MS, publishConfirm, publishState, refLabel, refLength,
         EXCEPTION_LABELS, STATE_SWITCHES, binLabel, browserLabel, candidateLine, clearedSource,
         copyLabels, dialogFields, heldBack, removeConfirm, saysExceptions,
         repairState, sourceLabel, sourceOpen, sourceRows, syncEntry, trackRows, trimGuard, awaitingChoice,
         resetKind, roundMark, watchesAfter, watchesWithout,
         scrollForActive, scrollToLine, searchTerms, seedConfirm, shifted, sourceChange, stampOf, takeInState,
         tapped, tenth,
         timingFields, timingNotice,
         toFileClock, trimOffset, trimTarget, watchTrouble, withoutTalk, wordsAfterClaim }
  from "./logic.mjs";

// noaap web UI. No framework, no build step. All server text goes in via textContent.
"use strict";

const $ = (sel) => document.querySelector(sel);
const PROV = { mb: "MB", yt_music: "YT Music", yt_title: "title", playlist: "playlist", user: "you",
               file_tags: "tags", folder_name: "folder", file_name: "file name" };
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

async function api(path, body, timeoutMs = 0) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Noaap": "1" },
    body: JSON.stringify(body),
  };
  // a ceiling only where one was asked for, and it is an abort rather than an error: the caller
  // decides whether a timeout means the server is gone (§9, slice 139)
  if (timeoutMs > 0 && typeof AbortSignal?.timeout === "function") opts.signal = AbortSignal.timeout(timeoutMs);
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
  let wrote = false;
  for (const [id, info] of mine) {
    const job = state.jobs.find((j) => j.id === id);
    if (!job || ["queued", "running"].includes(job.state)) continue;
    mine.delete(id);
    if (info.button?.isConnected) setWorking(info.button, false);
    if (job.lane !== "read") wrote = true;
    const last = (job.log || []).filter((l) => !l.startsWith("  ")).at(-1) || "";
    const text = { done: "✓", failed: "✗", blocked: "⏸", cancelled: "⏹" }[job.state] + ` ${info.label}` + (last ? ` — ${last}` : "");
    toast(text, job.state);
  }
  // **a job of ours that changed the library is a refresh, whether or not a poll saw it running**
  // (§9, slice 87). The panel used to be rebuilt only on a busy → idle transition, and a short job —
  // cutting one track takes about a second — can finish before the first poll after the submit, so the
  // transition never happens: measured, the page then kept the marks and the file of *before* the save,
  // played the cut file with the window applied, and that is the user's double skip.
  if (wrote) refreshAlbumPanel();
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
// rebuild would throw that text away before the user could look at it (§9, slice 36). The lane comes from
// the server with every job, so this cannot drift from the list the server actually uses.
let ranWrite = false;
// **one state request at a time** (§9, slice 139, R-499 item 3). The timer fired every 700 ms while
// a job ran, and on the user's NAS library one answer took 107 s — so each tick opened another
// request behind the last and the page queued dozens of identical walks, with anything else (a
// cover, a track) waiting its turn. A tick that finds one in flight simply waits for the next.
let polling = false;

async function poll() {
  const plan = pollPlan({ polling, busy: state.busy, waiting: Boolean(waitingFor), offline });
  if (!plan.ask) {
    schedulePoll(plan.next);
    return;
  }
  polling = true;
  try {
    const prevBusy = state.busy;
    // the ceiling is far above the bound the server states for its own answer, so a slow library
    // is waited for rather than declared dead (R-499 item 3)
    state = await api("/api/state", undefined, POLL_CEILING_MS);
    if (state.jobs?.some((j) => ["queued", "running"].includes(j.state) && j.lane !== "read")) ranWrite = true;
    if (state.tracks_version && state.tracks_version !== trackIndex.version) loadTracks();
    renderLibrary();
    warmSearchKeysSoon();   // fold the library's words while nothing is being typed (slice 155)
    renderJobs();
    renderSettings();
    renderBin();
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
    // **a slow answer is not a server that went down** (R-499 item 3). It is waited for: the page
    // opens no second request while one is in flight, and the ceiling above is far beyond the
    // staleness bound the server states — so reaching here means a refused connection or a wait
    // past that ceiling, and both of those really are "gone".
    setOffline(pollFailureIsOffline());
  } finally {
    polling = false;
  }
  schedulePoll(pollPlan({ busy: state.busy, waiting: Boolean(waitingFor), offline }).next);
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
      warmSearchKeysSoon();   // the track titles only arrive now, and they are most of the folding
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
  const terms = libFilter ? searchTerms(libFilter) : [];
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

// **what the filter matches against, folded once** (§9, slice 151; R-523). The user: the search
// field took seconds to accept a letter. Every `shownAlbums()` folded all 1,523 album lines and all
// 20,261 track titles, in both spellings, and `renderLibrary` called it six times per keystroke.
// Folding is per character with a `normalize` and two regexes, so one letter cost about ten million
// of those steps. Here each album's words are folded once and kept until the library changes.
let searchKeys = { for: null, albums: new Map() };

const keysStamp = () => `${state.tracks_version || ""}|${trackIndex.version || ""}`;

function keysFor(a) {
  const stamp = keysStamp();
  if (searchKeys.for !== stamp) searchKeys = { for: stamp, albums: new Map() };
  let keys = searchKeys.albums.get(a.id);
  if (!keys) {
    keys = {
      own: foldedBoth(`${a.albumartist} ${a.album} ${a.year || ""}`),
      rows: (trackIndex.albums[a.id] || []).map((r) => ({
        row: r, folded: foldedBoth(`${r[TRACK.artist]} ${r[TRACK.title]}`) })),
    };
    searchKeys.albums.set(a.id, keys);
  }
  return keys;
}

// **a slice of work, then back to the browser** (§9, slice 155; R-527). The user: *"an input that
// doesn't show what the user types immediately is perceived as laggy; functional reaction may take
// time, display input has to be an instant."* Folding the library's words is 243 ms measured on
// their 1,548 albums, and rebuilding the whole grid 96 ms — and whatever the main thread is doing,
// the letters a person is typing cannot appear until it stops. So this work is done 8 ms at a time
// with the thread handed back in between, and abandoned outright when a newer keystroke arrives.
const SLICE_MS = 8;
const yieldNow = () => globalThis.scheduler?.yield?.() ?? new Promise((r) => setTimeout(r, 0));
const whenIdle = (fn) => (globalThis.requestIdleCallback
  ? requestIdleCallback(fn, { timeout: 500 })
  : setTimeout(fn, 50));

/** Fold what the filter will match against, in slices. `mine()` says whether this run still counts. */
function warmKeys(mine) {
  const stamp = keysStamp();
  return inSlices(state.albums || [], (a) => keysFor(a),
                  { ms: SLICE_MS, wanted: () => mine() && keysStamp() === stamp, pause: yieldNow });
}

// and the same words are folded ahead of time, while nothing else is happening, so that the first
// letter typed after a library change does not pay for the whole library either
let warming = "";

function warmSearchKeysSoon() {
  const stamp = keysStamp();
  if (!stamp || warming === stamp || !(state.albums || []).length) return;
  warming = stamp;
  whenIdle(async () => {
    const mine = () => warming === stamp && keysStamp() === stamp;
    if (!(await warmKeys(mine)) || !libFilter) return;
    // **and the answer that was waiting for this is worked out now** (§9, slice 155). Without
    // this, a filter typed before the track index arrived kept whatever the grid had until the
    // next poll — measured as 30 albums where 38 match, for up to eight seconds.
    renderLibrary();
    if (!$("#album").hidden) markAlbumFields();
  });
}

function matchingRows(a, terms) {
  return keysFor(a).rows.filter((k) => hitsIn(terms, k.folded)).map((k) => k.row);
}

// One answer per render, not six: `renderLibrary`, `renderPlayMatches` and the signature all used
// to ask separately, and each asking walked the library again (R-523).
let shownFor = null;
let shownWas = [];

function shownAlbums() {
  const stamp = `${state.tracks_version || ""}|${trackIndex.version || ""}|${artistFilter}`
    + `|${libFilter}|${lengthOnly}|${needsYouOnly}|${state.albums.length}`;
  if (shownFor === stamp) return shownWas;
  const picked = pickAlbums();
  // **nothing here ever folds** (§9, slice 155; R-527). `pickAlbums` answers `null` when a word it
  // needs has not been folded yet, rather than folding 1,548 albums where it stands — which is a
  // quarter of a second in whatever task asked, including a poll's. The answer we had stands until
  // the folding catches up, and it was asked for.
  if (picked === null) return shownWas;
  shownFor = stamp;
  shownWas = picked;
  return shownWas;
}

function pickAlbums() {
  let byArtist = artistFilter ? state.albums.filter((a) => a.albumartist === artistFilter) : state.albums;
  if (lengthOnly) byArtist = byArtist.filter((a) => a.length);
  if (needsYouOnly) byArtist = byArtist.filter((a) => a.needs_you || a.copies);
  if (!libFilter) return byArtist.map((a) => ({ ...a, matches: null }));
  if (searchKeys.for !== keysStamp()) {
    warmSearchKeysSoon();
    return null;        // folded against an older library; the answer we had is the better one
  }
  const terms = searchTerms(libFilter);
  const out = [];
  for (const a of byArtist) {
    const keys = searchKeys.albums.get(a.id);
    if (!keys) {
      warmSearchKeysSoon();
      return null;
    }
    const own = hitsIn(terms, keys.own);
    const songs = own ? [] : keys.rows.filter((k) => hitsIn(terms, k.folded))
                                      .map((k) => `${k.row[TRACK.artist]} — ${k.row[TRACK.title]}`);
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

// The near-miss check hands two verdicts back to a person (§9, slice 46). Before this there was no way to
// find them: 42 tracks across 246 albums, and nothing listed them.
let needsYouOnly = false;

function renderNeedsYouFilter() {
  const scope = artistFilter ? state.albums.filter((a) => a.albumartist === artistFilter) : state.albums;
  const waiting = scope.reduce((n, a) => n + (a.needs_you || 0) + (a.copies || 0), 0);
  const button = $("#needs-you-filter");
  if (!waiting && !needsYouOnly) {
    button.hidden = true;
    return;
  }
  button.hidden = false;
  button.textContent = needsYouOnly ? "show all albums" : `${waiting} need${waiting === 1 ? "s" : ""} you`;
  button.setAttribute("aria-pressed", String(needsYouOnly));
}

$("#needs-you-filter").addEventListener("click", () => {
  needsYouOnly = !needsYouOnly;
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
  const albums = shownAlbums();
  const shown = albums.length;
  const all = (artistFilter ? state.albums.filter((a) => a.albumartist === artistFilter) : state.albums).length;
  const songMatches = albums.reduce((n, a) => n + (a.matches?.length || 0), 0);
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
  // **the signature says what the grid shows, without serialising it** (R-523). This was
  // `JSON.stringify(shownAlbums())`: the whole filtered library turned into a string on every
  // keystroke, only to be compared and thrown away. The ids and the number of matching songs say
  // the same thing about whether the cards would differ, and `state.version` covers the rest.
  const signature = [state.tracks_version, trackIndex.version, artistFilter, libFilter, lengthOnly,
                     needsYouOnly, shown, songMatches,
                     albums.map((a) => a.id).join("\u0000")].join("\u0001");
  if (signature !== gridShows) paintGrid(grid, albums, signature);
  $("#empty").hidden = shown > 0;
  renderPlayMatches();
  renderLengthFilter();
  renderNeedsYouFilter();
  renderRail();
}

// **the cards are built in slices too** (§9, slice 155; R-527). Going back to the whole library
// rebuilt 1,548 cards in one task — 96 ms measured, and 96 ms in which no typed letter can appear.
// A paint that a newer one supersedes is abandoned, and `gridShows` is only set once the cards are
// actually in, so an abandoned paint never claims the grid shows something it does not.
let gridRun = 0;

async function paintGrid(grid, albums, signature) {
  const mine = ++gridRun;
  // rebuilding throws away the focused card, which would break arrow-key navigation
  const focused = document.activeElement?.closest?.("#grid .card")?.dataset.id;
  const made = document.createDocumentFragment();
  const whole = await inSlices(albums, (a) => made.append(card(a)),
                               { ms: SLICE_MS, wanted: () => gridRun === mine, pause: yieldNow });
  if (!whole) return;
  gridShows = signature;
  grid.replaceChildren(made);
  // preventScroll: a rebuild must not drag the viewport to the focused card - it would
  // pull an open album editor out of view whenever a download changes something
  if (focused) grid.querySelector(`.card[data-id="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
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

// **Covers load when their card comes near the screen** (§9, slice 90). `loading="lazy"` was not
// enough: Chrome fetched nearly all of them on a refresh — measured on a 250-album library, **221
// requests and 52 MB**, and the first press of play waited behind that flood for more than ten
// seconds. The margin is generous on purpose: scrolling should not wait for a request.
const coverWatch = "IntersectionObserver" in window
  ? new IntersectionObserver((entries, self) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        show(entry.target);
        self.unobserve(entry.target);
      }
    }, { rootMargin: "400px" })
  : null;

function show(img) {
  if (!img.dataset.cover) return img;
  img.src = img.dataset.cover;
  delete img.dataset.cover;
  return img;
}

function watched(img) {
  if (coverWatch) coverWatch.observe(img);
  else show(img);   // no observer in this browser: the old behaviour, every cover at once
  return img;
}

function card(a) {
  const cover = a.cover
    ? watched(h("img", { class: "cover", alt: "", loading: "lazy",
                         // a card is 10.5rem wide, so it asks for a thumbnail rather than the
                         // cover itself — 65 KiB each and about 82 MB for the whole grid (slice 140)
                         "data-cover": `/api/cover?id=${encodeURIComponent(a.id)}&t=${a.done}&thumb=1` }))
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
        lengthBadge(a),
        a.needs_you ? h("span", { class: "badge warn", title:
          `${a.needs_you} track(s) where lrclib has words and nothing could decide whether they are this recording's`
          }, `\u266a ${a.needs_you} need${a.needs_you === 1 ? "s" : ""} you`) : null,
        a.copies ? h("span", { class: "badge warn", title:
          `${a.copies} track(s) where another copy of the song is here and nothing could rank it against the one in use`
          }, `\u21c4 ${a.copies}`) : null),
      songs));
}

// Play what the filter found: the matching songs of each album, or all of an album that
// matched by name — the same thing the cards show.
function playMatches() {
  const terms = searchTerms(libFilter);
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

// **typing is answered by the browser, filtering happens just after** (§9, slice 151; R-523).
// The field is the user's to type in; the grid can be a sixth of a second behind. Without this the
// keystroke itself waited for the filter, so a fast typist queued one full render per letter.
const FILTER_IDLE_MS = 150;
let filterSoon = null;
let filterRun = 0;

function filterAfterIdle(value) {
  const wanted = value.trim();
  if (wanted === libFilter && filterSoon === null) return;
  filterRun++;                    // whatever an older keystroke started, stop counting it
  clearTimeout(filterSoon);
  filterSoon = setTimeout(() => {
    filterSoon = null;
    // and not even in the frame after the idle: `requestIdleCallback` waits for one the browser
    // has nothing better to do with, which is never the frame that has a letter to paint
    whenIdle(() => applyFilter(wanted));
  }, FILTER_IDLE_MS);
}

/** Put the typed filter into effect, in slices, abandoning it if another letter arrives. */
async function applyFilter(wanted) {
  const mine = ++filterRun;
  const stillMine = () => filterRun === mine;
  // the library can change while this is folding (the track index arrives on its own), and then
  // the words have to be folded again — three tries, so a library being written to cannot spin
  for (let tries = 0; tries < 3; tries++) {
    const stamp = keysStamp();
    const warm = await warmKeys(stillMine);
    if (!stillMine()) return;               // a newer keystroke; that run will do this
    if (!warm || keysStamp() !== stamp) continue;
    if (wanted === libFilter) return;
    libFilter = wanted;
    renderLibrary();
    if (!$("#album").hidden) markAlbumFields();
    return;
  }
}

/** The filter now, for Enter and Escape: an answer, not typing (§9, slice 151). */
function filterNow(wanted) {
  clearTimeout(filterSoon);
  filterSoon = null;
  filterRun++;
  if (wanted === libFilter) return;
  libFilter = wanted;
  renderLibrary();
  if (!$("#album").hidden) markAlbumFields();
}

$("#libfilter").addEventListener("input", (e) => filterAfterIdle(e.target.value));
// Enter jumps into the results; Escape clears the filter before anything else closes
$("#libfilter").addEventListener("keydown", (e) => {
  // Enter and Escape are answers, not typing: they take effect at once, ahead of the idle wait
  if (e.key === "Enter") {
    filterNow(e.target.value.trim());
    $("#grid").querySelector(".card")?.focus();
  } else if (e.key === "Escape" && (libFilter || e.target.value)) {
    e.stopPropagation();
    e.target.value = "";
    filterNow("");
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
// Per-browser conveniences, nothing the library depends on: which album was open, which theme was
// picked. They were also read under ytalbum's names for one release; nothing reads those now
// (R-420), and the worst a browser that still holds one loses is a remembered tile. Every access is
// guarded: private mode makes these throw.
const LAST = "noaap-last";
const THEME = "noaap-theme";
function remembered(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
let lastAlbumId = remembered(LAST);

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
  try { localStorage.setItem(LAST, id); } catch { /* private mode */ }
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

// The editor's own fields, with the value each was drawn with: `defaultValue` is the `value`
// attribute `h()` set, so a field differs from it exactly when the user has typed into it.
function editorFields() {
  const form = $("#albumform");
  if (!form) return [];
  return [...form.querySelectorAll("input[name]")]
    .filter((el) => ["text", "number", "search", ""].includes(el.type))
    .map((el) => ({ el, row: el.closest("tr")?.dataset.id || "", name: el.name, value: el.value, shown: el.defaultValue }));
}

async function refreshAlbumPanel() {
  if (!currentAlbum) return;
  const before = new Map(currentAlbum.tracks.map((t) => [t.video_id, rowKey(t)]));
  const opened = currentAlbum.source_id;
  try {
    currentAlbum = await api(`/api/album?id=${encodeURIComponent(currentAlbum.source_id)}`);
  } catch { return; /* album moved or gone */ }
  // **taken after the answer, not before it** (P116, D2): the user may type while it is on its way
  const kept = currentAlbum.source_id === opened ? unsavedEdits(editorFields()) : [];
  syncQueueWith(currentAlbum);
  renderAlbum();
  for (const tr of document.querySelectorAll("#album tbody tr")) {
    const t = currentAlbum.tracks.find((x) => x.video_id === tr.dataset.id);
    if (t && before.get(t.video_id) !== rowKey(t)) tr.classList.add("changed");
  }
  if (!kept.length) return;
  // **a finished job redraws the album; it does not take back what was typed** (P116, D2): the
  // reviewer typed a title, pressed "Fetch lyrics" on the same album, and 25 s later the field read
  // the old title again. The typed values go back into their fields — still unsaved, so still
  // different from what they were drawn with — and the page says the album moved underneath.
  const fresh = editorFields();
  let lost = 0;
  for (const k of kept) {
    const f = fresh.find((x) => x.row === k.row && x.name === k.name);
    if (f) f.el.value = k.value; else lost += 1;
  }
  toast(`This album changed while you were editing it. Your unsaved changes are kept — save them, or reopen the album to drop them.${lost ? ` ${lost} belonged to a track that is no longer here.` : ""}`, "failed");
}

/** Teach the player what a write job just did to these files (§9, slice 87).
 *
 * **What it does not do is touch `audio.src`.** The element is in the middle of something the user
 * started; a save is not a request to restart it, and replacing the source under a pending `play()` is
 * where *"The play() request was interrupted by a new load request"* comes from (R-295). So the page
 * updates what it *knows* — which file to ask for next time, and what the saved marks are — and the
 * sound carries on. The next start of that track loads the right file, and the trim window is applied
 * only to the file actually loaded, so what is playing now stays coherent either way.
 *
 * A track whose marks the user has moved but not saved keeps them: their work is not overwritten by
 * what the server currently holds.
 */
function syncQueueWith(plan) {
  for (const entry of queue) {
    if (entry.album !== plan.source_id) continue;
    const fresh = plan.tracks.find((t) => t.video_id === entry.video_id);
    if (!fresh) continue;
    Object.assign(entry, syncEntry(entry, fresh));
  }
  renderTrim();
}

// -- offering things to MusicBrainz (\u00a79.43) -------------------------------------------------
//
// Both of these open one of *their* pages. noaap holds no MusicBrainz credentials and submits
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
// way back to what noaap found. Nothing is offered where nothing was derived (DESIGN.md §9, slice 29).
function resetMark(plan, track, name, label = null) {
  const owner = track || plan;
  const derived = owner.auto?.[name];
  const kind = resetKind(owner.provenance?.[name], derived);
  if (kind !== "button") {
    return owner.provenance?.[name] === "user"
      ? h("span", { class: "badge user", title: "Yours. noaap derived nothing for this field, so there is nothing to go back to." }, PROV.user)
      : provBadge(owner.provenance?.[name]);
  }
  const what = label || name;
  return h("button", { class: "badge user reset", type: "button",
    title: `Yours${label ? ` (${label})` : ""}. Click to go back to what noaap found: “${derived}” — it is then noaap's again, and an update or repair may change it.`,
    onclick: (e) => resetField(plan, track, name, e.currentTarget, `${what} → “${derived}”`) }, `${PROV.user} \u21ba`);
}

async function resetField(plan, track, name, button, what) {
  if (name === "source") {
    // going back is a source change like any other, and costs the same (§9, slice 34)
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

// -- where a track's audio comes from (§9, slice 34) ---------------------------------------------
//
// The playlist's video stays the track's identity — its place, its name, its match. This only
// says which recording to take the audio from, for the case the playlist holds the film cut and
// the song exists on its own. Everything the old file carried goes with the switch, so the
// confirm says which marks it is about to clear before anything is fetched.
const openSource = new Set();

function sourceMark(p, t) {
  const own = t.source_override;
  const waiting = awaitingChoice(t).length;
  return h("button", { class: "quiet small src-pick" + (own ? " on" : "") + (waiting ? " waiting" : ""), type: "button",
    title: waiting ? `Another copy of this song is here and nothing could choose between them \u2014 open to compare and decide`
      : own ? `Audio from ${refLabel(own)}, not the source's ${refLabel(t.video_id)} \u2014 click to change it or go back`
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
  const here = (t.candidates || []).find((c) => c.ref === id);
  const note = h("div", { class: "muted source-note" });
  const field = h("input", { type: "text", name: "source", value: t.source_override || "",
    placeholder: "YouTube URL or video id", "aria-label": `audio source of ${t.title}`, size: 34,
    onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); e.currentTarget.parentElement.querySelector("button.use").click(); } } });
  return h("div", { class: "source-panel" },
    h("div", { class: "muted" }, `${t.artist} \u2014 ${t.title} \u00b7 audio from `,
      here && here.url ? h("a", { href: here.url, target: "_blank", rel: "noopener" }, refLabel(id))
                       : h("span", { title: id }, refLabel(id)),
      t.source_override ? " \u2014 yours, not the playlist's video " : " \u2014 the playlist's own video",
      t.source_override ? resetMark(p, t, "source", "audio source") : null),
    h("div", { class: "panel-actions" }, field,
      h("button", { class: "small use", type: "button", onclick: (e) => useSource(e.currentTarget, p, t, field.value, note) }, "Use this video"),
      h("button", { class: "quiet small", type: "button", onclick: (e) => closeSource(e.currentTarget, t) }, "Cancel")),
    note,
    candidateList(p, t),
    h("div", { class: "muted" }, "The track keeps its place, its name and your words \u2014 only the audio is fetched again, "
      + "from the copy you take or the video you name here. Its length becomes the one the \u23f1 chip is measured against."));
}

// Everywhere this track's audio can be had from (§9, slice 50). Today they are all YouTube
// videos, and the list is short; it is here because choosing between them is what comes next.
function candidateList(p, t) {
  const all = t.candidates || [];
  const refused = new Set(t.refused_candidates || []);
  if (all.length < 2 && !refused.size) return null;
  const id = effectiveId(t);
  return h("div", { class: "candidates" },
    h("div", { class: "muted" }, "known sources for this track:"),
    // **labelled as a set, not one at a time** (§9, slice 77): two copies of one song in two
    // libraries of the same shape had the same two-part label, and only the numbers told them apart
    ...((labels) => all.map((c) => h("div", { class: "candidate" + (c.ref === id ? " on" : "") },
      c.url ? h("a", { href: c.url, target: "_blank", rel: "noopener" }, labels.get(c.ref) || refLabel(c.ref))
            : h("span", { title: c.ref }, labels.get(c.ref) || refLabel(c.ref)),
      h("span", { class: "muted" }, candidateLine(c, c.ref === id, refused.has(c.ref))),
      c.ref === id || refused.has(c.ref) ? null
        : h("button", { class: "small", type: "button",
            title: "Use this copy for this track \u2014 it is fetched again, from here",
            onclick: (e) => takeCandidate(e.currentTarget, p, t, c.ref) }, "take this one"),
      c.ref === id || refused.has(c.ref) ? null
        : h("button", { class: "quiet small", type: "button",
            title: "Never offer this one for this track again",
            onclick: (e) => refuseCandidate(e.currentTarget, p, t, c.ref) }, "not this one"))))(
      copyLabels(all.map((c) => c.ref))));
}

async function takeCandidate(button, p, t, ref) {
  const change = sourceChange(t, ref === t.video_id ? null : ref, t.candidates?.find((c) => c.ref === ref));
  if (!change || !confirm(change.lines.join("\n"))) return;
  await submit("edit", { id: p.source_id, edits: { tracks: [{ video_id: t.video_id, take: ref }] } }, button);
  await refreshAlbumPanel();
}

async function refuseCandidate(button, p, t, ref) {
  if (!confirm(`Never offer ${ref} as this track's audio again?\n\nIt stays listed, marked refused. `
      + "Nothing is downloaded or deleted.")) return;
  await submit("edit", { id: p.source_id, edits: { tracks: [{ video_id: t.video_id, refuse: ref }] } }, button);
  await refreshAlbumPanel();
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

// A near-miss the check handed back: the words exist, nothing was taken, and only a person can
// settle it (§9, slice 46). The row says so, because the panel is two clicks away and 42 of these were
// sitting in this library with nothing pointing at them.
const NEEDS_YOU = (t) => !HAS_WORDS(t)
  && ["unclear", "shown"].includes((t.lyrics_fit || {}).decided);

function lyricsMark(p, t) {
  if (t.state !== "done") return null; // no file yet, so nothing to put words beside
  if (NEEDS_YOU(t)) {
    return h("button", { class: "quiet small lyr needs-you", type: "button",
      title: "lrclib has words for this title and nothing could decide whether they are this "
        + "recording's — click to see both numbers and choose",
      onclick: (e) => toggleLyrics(e.currentTarget, p, t) }, "\u266a ?");
  }
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
  // the server has, and only a Save puts it anywhere (§9, slice 37)
  if (draft) Object.assign(d, { text: draft.text, words_by: draft.by, draft: draft.notice,
                                timed: draft.timed });
  return h("tr", { class: "lyrics", "data-id": t.video_id }, h("td", { colspan: "8" }, lyricsPanel(p, t, d, editing)));
}

// Reading and writing are the same panel: the .lrc beside the track is the original either way,
// and saving here does exactly what the ownership contract does for a file edited on disk.
function lyricsPanel(p, t, d, editing) {
  const { where, actions } = lyricsPanelState(d);
  const head = h("div", { class: "muted" }, `${t.artist} — ${t.title} · ${where}`,
    d.words_by ? h("span", { class: d.owner === "source" ? "badge" : "badge warn",
      title: d.owner === "source"
        ? `These words came with the recording: ${d.words_by}\u2019s own captions. They are not yours, they are never offered to anybody, and editing a line does not change that.`
        : `These words were drafted by ${d.words_by} and are a machine's guess.` },
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
      canDraft(t) && maySendAudio(d, "draft") ? h("button", { class: "quiet small", type: "button",
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
// thing the user can do that noaap would not do for them.
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

// The words being edited are what playback follows while the editor is open (§9, slice 39), so the list
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
    rows: editorRows(d.text),
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
    // the box follows what is typed at once (§9, slice 96); the preview can wait for a pause
    area.rows = editorRows(area.value, area.rows);
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
  // **a block the draft says may not be the song, and one press to be rid of it** (§9, slice 159;
  // R-535). Nothing is dropped by itself: the lines stay in the editor with the marker above them
  // until somebody says so.
  const talk = d.timed ? talkBlock(d.timed) : null;
  const talkRow = talk
    ? h("div", { class: "timing-note talk-note" },
        h("span", {}, `\u26a0 ${talk.note} `),
        h("button", { class: "quiet small", type: "button",
          onclick: (e) => {
            area.value = withoutTalk(area.value, d.timed);
            e.currentTarget.closest(".talk-note").hidden = true;
            area.dispatchEvent(new Event("input", { bubbles: true }));
          } }, talk.action))
    : null;
  // **the one thing that can clear a draft mark** (§9, slice 69): a statement, not an edit. Without it
  // the editor sent `words_by` back on every save and a draft stayed a draft for ever, which made the
  // refusal's own advice impossible to follow.
  const claim = claimOffer(d);
  const mine = claim.offer
    ? h("input", { type: "checkbox", class: "claim-words", id: `claim-${t.video_id}` })
    : null;
  const claimRow = claim.offer
    ? h("label", { class: "claim-row", for: `claim-${t.video_id}`, title: claim.title },
        mine, h("span", {}, claim.label))
    : null;
  const by = h("input", { type: "text", class: "shift-by", value: "-0.5", size: 5, "aria-label": "seconds to move every stamp by",
    onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); shiftStamps(area, by); } } });
  const nudge = (delta, label) => h("button", { class: "quiet small", type: "button",
    title: `Move this line's stamp by ${label} s and play it from there (Alt+${delta < 0 ? "←" : "→"}${Math.abs(delta) > 0.1 ? " with Shift" : ""})`,
    onclick: () => nudgeStamp(p, t, area, delta) }, label);
  return h("div", { class: "editor-with-preview" }, area, claimRow,
    h("div", { class: "panel-actions stamp-tools" },
      h("button", { class: "quiet small", type: "button",
        title: "Write the moment you are hearing on this line, in the file's own clock (Ctrl+Enter).\nPlay the track first.\nThe cursor stays on the line, so the nudges beside this button can fine-tune the stamp;\nthe down arrow moves on.",
        onclick: () => tapStamp(p, t, area) }, "⏱ stamp this line"),
      h("button", { class: "quiet small", type: "button", title: "Play from this line's stamp (Alt+Enter)",
        onclick: () => playLine(p, t, area) }, "▶"),
      nudge(-0.5, "−0.5"), nudge(-0.1, "−0.1"), nudge(0.1, "+0.1"), nudge(0.5, "+0.5"),
      canAlign() && maySendAudio(d, "align") ? h("button", { class: "quiet small", type: "button",
        title: "Ask the configured timing provider to place these words on this file's clock.\n"
          + "It writes nothing: the stamps appear here and you decide whether to save them.\n"
          + "Without a GPU this takes a couple of minutes for a four-minute track.",
        onclick: (e) => alignWords(e.currentTarget, p, t, area, proposal, timing) }, "⚖ align these words") : null,
      // the other way round (§9, slice 83): write down what the recording says, then find these lines
      // in it. Slower and it costs a transcription, but a line that was never sung stays unplaced —
      // which forced alignment cannot tell you, because it places everything it is given.
      canListen() && maySendAudio(d, "listen") ? h("button", { class: "quiet small", type: "button",
        title: "Write down what the recording actually says, then place these lines where their words\n"
          + "were heard. A line whose words are not in the recording gets no stamp and says so.\n"
          + "Slower than aligning, and with a paid provider it costs a transcription.",
        onclick: (e) => alignWords(e.currentTarget, p, t, area, proposal, timing, "listen") }, "👂 listen for these words") : null,
      h("span", { class: "muted stamp-clock" })),
    previewHead, preview,
    proposal,
    talkRow,
    h("div", { class: "panel-actions" },
      h("span", { class: "muted" }, "shift every stamp by"), by, h("span", { class: "muted" }, "s"),
      h("button", { class: "quiet small", type: "button",
        title: "Move every timestamped line by that many seconds. Nothing is saved until you press Save.",
        onclick: () => shiftStamps(area, by) }, "shift all")),
    h("div", { class: "lyrics-actions" },
      h("button", { class: "small", type: "button",
        onclick: (e) => saveLyrics(e.currentTarget, p, t, area.value, timing.by,
                                   wordsAfterClaim(timing.words, mine?.checked), timing.checked) }, "Save"),
      h("button", { class: "quiet small", type: "button", onclick: (e) => editLyrics(e.currentTarget, p, t, false) }, "Cancel"),
      d.text ? h("button", { class: "quiet small danger", type: "button",
        title: "Remove the .lrc beside this track. Its tag goes with it, and a later “look up all again” may fetch LRCLIB's words.",
        onclick: (e) => saveLyrics(e.currentTarget, p, t, "") }, "Delete") : null,
      h("span", { class: "muted" }, "A line like [01:23.4] Words becomes clickable and follows the song.")));
}

// -- stamping the words to the file's clock (§9, slice 35) ------------------------------------------
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
  showLine(area, got.caret);
}

// **Put the caret on a line and keep that line in sight** (§9, slice 94). Every control here sets the
// selection itself, and a programmatic selection does not scroll a textarea — so the one control that
// moves the caret to another line, the stamp, walked the line being worked on out of the box.
function showLine(area, caret) {
  area.focus();
  area.setSelectionRange(caret, caret);
  const lineHeight = parseFloat(getComputedStyle(area).lineHeight) || 0;
  const to = scrollToLine({ line: lineAt(area.value, caret) + 1, lineHeight,
                            clientHeight: area.clientHeight, scrollTop: area.scrollTop,
                            lines: area.value.split("\n").length });
  if (to != null) area.scrollTop = to;
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
  showLine(area, lineStart(area.value, i));
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
// button at all and the page is what it was before any of this existed (§9, slice 36). Each capability asks
// its own slot (§9, slice 40): aligning and drafting can be two different providers, and everything that
// follows from *which* — the name in the confirm, whether the audio leaves, the price — follows the
// slot, never "the provider".
const canAlign = () => (state.settings?.timing?.capabilities || []).includes("align");
const canTranscribe = () => (state.settings?.timing?.capabilities || []).includes("transcribe");
// listening is offered by whoever transcribes with word times — the drafting slot, not the aligning
// one, because that is the work it does and how it is paid for (§9, slice 83)
const canListen = () => (state.settings?.timing?.capabilities || []).includes("listen");
// a draft is only for a track with nothing to lose: no words, or only the note that LRCLIB has none
const canDraft = (t) => canTranscribe() && !HAS_WORDS(t);
const providerFor = (what) =>
  state.settings?.timing?.[what === "align" ? "align_provider" : "draft_provider"]
  || state.settings?.timing?.provider || "the provider";
const sendsAudio = (what) => Boolean(state.settings?.timing?.sends_audio?.[what]);
// **what the server will refuse is not offered** (§9, slice 75). An album whose audio one person paid
// a creator for goes to a timing provider only when that provider runs on this machine, and the
// server answers per album — so the page asks the album and not only the installation. An older
// server that does not send the field is taken at its word, exactly as before.
const maySendAudio = (d, what) => (d && d.may_send_audio) ? d.may_send_audio[what] !== false : true;

// Asked once per provider per session, before the first request that leaves the machine. Not a
// setting to be forgotten: the user is told what is about to happen, in the moment it happens.
const told = new Set();

function mayLeave(what, capability) {
  const name = providerFor(capability);
  if (!sendsAudio(capability) || told.has(name)) return true;
  if (!confirm([`The audio of this track is sent to ${name} to ${what}.`, "",
    "It leaves this machine and this network. Local providers (`local`, `http`) never do that.",
    `${name} charges for it — the settings panel shows their list price — and noaap never retries,`,
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
    { text: draftText(timed), by: job.result.by || "", notice: draftNotice(timed), timed }));
}

async function alignWords(button, p, t, area, notice, timing, method = "align") {
  const listening = method === "listen";
  if (!mayLeave(listening ? "write down what it hears and find these words in it"
                          : "place these words on its clock", method)) return;
  const id = await submit("align", { id: p.source_id, video_id: t.video_id, text: area.value, method }, button);
  if (id == null) return;
  notice.hidden = false;
  notice.textContent = listening
    ? "⏳ listening to the whole track, then looking for these words in it — minutes"
    : "⏳ asking the timing provider — minutes, on a machine without a GPU";
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
  // evidence and not only the verdict (§9, slice 44)
  timing.checked = timed.parameters || null;
  notice.textContent = `⚠ ${alignNotice(timed, got)}`;
  showLine(area, 0);   // the words have all moved; the top is where a person reads them from
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

/** Move the playhead to `at` seconds, now if the element can, else as soon as it knows the media. */
function seekWhenReady(at) {
  const go = () => { audio.currentTime = at; };
  if (audio.readyState >= 1) go();
  else audio.addEventListener("loadedmetadata", go, { once: true });
}

async function seekLyric(p, t, at) {
  // **the track that is loaded is seeked, never reloaded** (§9, slice 88): the element is identified by
  // the track it holds, and starting the album again over a click on a line is how a jump turns into a
  // restart. Only a *different* track is started, and then the target goes into the start itself.
  if (!isPlaying(p.source_id, t.video_id)) {
    const i = p.tracks.filter((x) => x.state === "done").findIndex((x) => x.video_id === t.video_id);
    if (i < 0) return toast("This track has not been downloaded yet", "blocked");
    await playAlbum(p.source_id, i, { at: at + trimOffset({ ...t, savedStart: t.trim_start,
                                                            playingOriginal: t.original_kept !== false }) });
    return;
  }
  const playing = queue[qi];
  // the lyrics were matched against the file as it is on disk; when that file was cut, the
  // player is holding the original, so the trim has to be added back to reach the same spot —
  // the trim the file was *cut* to, not a mark someone is still placing (§9, slice 35)
  const target = at + trimOffset(playing);
  seekWhenReady(target);
  audio.play().catch(() => {});
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
        coverBlock(p),
        h("div", {}, h("h2", {},
          h("span", { class: "link", role: "button", tabindex: "0", title: `Show all albums by ${p.albumartist}`,
            onclick: () => { const name = p.albumartist; closeAlbum(); showArtist(name); },
            onkeydown: (e) => { if (e.key === "Enter") { const name = p.albumartist; closeAlbum(); showArtist(name); } } }, p.albumartist),
          ` — ${p.album}`, p.year ? h("span", { class: "muted" }, ` (${p.year})`) : null),
          h("div", { class: "muted" }, `${p.kind.replace("_", " ")} · ${p.tracks.length} tracks · ${p.folder}`))),
      seedButton(p),
      h("button", { class: "quiet", type: "button", onclick: () => closeAlbum() }, "Close")),
    h("form", { id: "albumform", onsubmit: saveAlbum },
      h("div", { class: "fields" }, field("Album artist", "albumartist", p.albumartist), field("Album", "album", p.album), field("Year", "year", p.year, "number"),
        // **the release this album is** (§9, slice 137). A paste of the MusicBrainz link does it, and
        // an empty field hands the choice back to the search.
        h("label", { class: "wide" },
          h("span", {}, "MusicBrainz release ", resetMark(p, null, "mbid")),
          h("input", { type: "text", name: "mbid", value: p.mbid || "", size: 38,
            placeholder: "paste the musicbrainz.org/release/… link",
            title: p.provenance.mbid === "user"
              ? "This release is yours: every update takes its names, numbers and discs from it and never searches for another. Clear the field to hand it back."
              : "Paste a release link to say which release this album is. Until you do, every update searches for one." }))),
      p.provenance.mbid === "user"
        ? h("div", { class: "muted order-mark" }, "Release pinned by you ",
          h("a", { href: `https://musicbrainz.org/release/${p.mbid}`, target: "_blank", rel: "noopener" }, p.mbid))
        : null,
      offersPanel(p),
      p.provenance.order === "user"
        ? h("div", { class: "muted order-mark" }, "Track order is yours ",
          h("button", { class: "badge user reset", type: "button",
            title: "The order you set is kept through every update. Click to hand it back: nothing is renumbered now, but the next update may put the album in the source's order again.",
            onclick: (e) => resetField(p, null, "order", e.currentTarget, "the track order") }, `${PROV.user} \u21ba`))
        : null,
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", { title: "position in the album" }, "#"), h("th", {}, "Artist"), h("th", {}, "Title"), h("th", { title: "each disc is numbered from 1" }, "disc"), h("th", { title: "cut the front / play until — for label idents and previews" }, "trim"), h("th", {}, "from"), h("th", {}, ""))), h("tbody", {}, rows)),
      skipped.length ? h("details", {}, h("summary", { class: "muted" }, `${skipped.length} skipped`), h("ul", {}, skipped)) : null,
      exceptionsFor(p),
      h("div", { class: "actions" },
        h("button", { type: "submit" }, "Save changes (rename + retag + trim)"),
        h("button", { class: "quiet", type: "button", onclick: (e) => submit("fetch", { urls: [p.source_url] }, e.currentTarget) }, "Re-check source"),
        h("button", { class: "quiet", type: "button",
          title: "Look up the lyrics of every track that has none yet (LRCLIB), as a .lrc file beside it and in its tags.\nShift+click looks up all of them again — lyrics you wrote yourself are kept either way.",
          onclick: (e) => submit("lyrics", { id: p.source_id, refetch: e.shiftKey }, e.currentTarget) }, "Fetch lyrics"),
        identifyButton(p),
        // **only for an album whose release a person chose** (§9, slice 162): a release a pass
        // found is not evidence of what the album is missing
        p.provenance?.mbid === "user" && p.mbid
          ? h("button", { class: "quiet", type: "button", disabled: Boolean(state.busy_write),
              title: "Look for the tracks of the pinned release this album has no file for",
              onclick: (e) => checkComplete(p, e.currentTarget) }, "Complete from the release…")
          : null,
        h("button", { class: "danger", type: "button", onclick: (e) => deleteAlbum(p, e.currentTarget) }, "Delete album"),
        gone.length ? h("button", { class: "danger", type: "button", onclick: (e) => pruneAlbum(p, gone, e.currentTarget) }, `Remove ${gone.length} track${gone.length > 1 ? "s" : ""} no longer in the playlist`) : null,
        sourceOpening(p))));
  panel.hidden = false;
  markAlbumFields();
  reopenLyrics();
}

// **What this one album is excepted from** (§9, slice 100). The settings say what every album should
// have and every pass brings an album to them; this is the only thing kept per album, it can only take
// something away, and nothing but this writes one.
function exceptionsFor(p) {
  const wanted = state.settings?.state || {};
  const mine = p.exceptions || {};
  const said = saysExceptions(mine);
  const stopped = heldBack(wanted, mine);
  const box = h("details", { class: "exceptions" },
    h("summary", { class: "muted" },
      said ? `This album is excepted: ${said}` : "This album follows the library's settings"),
    h("div", { class: "muted" },
      "An exception holds for this album alone, through every pass. It can only leave something out — "
      + "what the settings do not ask for cannot be asked for here."),
    ...Object.entries(EXCEPTION_LABELS).map(([key, label]) =>
      h("label", { class: "setting" },
        h("span", {}, h("strong", {}, label),
          h("small", { class: "muted" }, stopped.includes(key) || (key === "names" && stopped.includes("rename_adopted"))
            || (key === "tags" && stopped.includes("retag_adopted"))
            ? "in force — the settings ask for this and this album is left out"
            : "the settings do not ask for this at the moment, so it changes nothing yet")),
        h("input", { type: "checkbox", checked: Boolean(mine[key]),
                     onchange: (e) => setException(p, key, e.currentTarget.checked, e.currentTarget) }))));
  return box;
}

async function setException(p, key, on, control) {
  control.disabled = true;
  try {
    const id = await submit("except", { id: p.source_id, key, on });
    if (id != null) await jobSettled(id);
    await refreshAlbumPanel();
  } catch (e) {
    toast(e.message, "failed");
  } finally {
    control.disabled = false;
  }
}

// -- reordering by dragging -------------------------------------------------------------
//
// Pointer events, not HTML5 drag-and-drop, because the latter does not exist on touch. The row
// is moved in the table as the pointer passes other rows, so what you see is the arrangement
// you will get; the position column is renumbered on every move, per disc, and a row dropped
// among another disc's rows takes that disc (DESIGN.md §9, slice 32). Nothing is saved until the
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
  const terms = libFilter ? searchTerms(libFilter) : [];
  for (const el of $("#album").querySelectorAll('input[type="text"]')) {
    const maps_ = maps(el.value);
    el.classList.toggle("hit", terms.some((term) => maps_.some((m) => m.folded.includes(term))));
  }
}

function deleteTrack(plan, track, button) {
  const message = `Delete “${track.artist} – ${track.title}”?\n\nThe file is moved to the recycle bin — nothing is deleted outright — and the\nremaining tracks are renumbered. The Recycle bin in the header puts it back.\nIf the video is still in the playlist, a later update fetches it again.`;
  if (confirm(message)) submit("delete_track", { id: plan.source_id, video_id: track.video_id }, button);
}

function deleteAlbum(plan, button) {
  const n = plan.tracks.length;
  const message = `Delete the album “${plan.albumartist} — ${plan.album}”?\n\n${n} track(s) and the cover move to the recycle bin; the album data in\n${plan.folder}\nis removed.\n\nFiles you put there yourself are kept. The Recycle bin in the header puts the audio back.`;
  if (confirm(message)) {
    submit("delete_album", { id: plan.source_id }, button).then(() => closeAlbum(null));
  }
}

function pruneAlbum(p, gone, button) {
  const list = gone.map((t) => `  ${t.number}. ${t.artist} – ${t.title}`).join("\n");
  if (confirm(`Move these to the recycle bin? They are no longer in the YouTube playlist:\n\n${list}\n\nNothing is deleted outright; the Recycle bin in the header puts them back.`)) submit("prune", { id: p.source_id }, button);
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
  // page (§9, slice 43). One place for one fact: a second badge would have printed the same two numbers
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
  try {
    const edits = {
      album: form.album.value, albumartist: form.albumartist.value, year: form.year.value,
      mbid: form.mbid.value,
      // **only the track rows** (§9, slice 89): a panel is a row too, with none of these fields
      tracks: trackRows(form.querySelectorAll("tbody tr")).map((tr) => ({
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
    // and the values being saved are the values from now on: the refresh after this save must
    // not mistake them for unsaved typing (P116, D2)
    for (const f of editorFields()) f.el.defaultValue = f.value;
    submit("edit", { id: currentAlbum.source_id, edits }, ev.submitter);
  } catch (e) {
    // **a save that cannot be built says so** (§9, slice 89). This one threw inside the handler, so the
    // browser logged it to a console nobody had open and the page looked as if nothing had been pressed.
    console.error("saveAlbum", e);
    toast(`This album could not be saved: ${e.message}`, "failed");
  }
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
// because it comes from the same code path with `dry` set (DESIGN.md §9, slice 28).
function previewView(p, close, known) {
  const gone = p.tracks.filter((t) => t.in_source === false);
  const rows = p.tracks.map((t) => h("tr", { class: t.in_source === false ? "muted" : "" },
    h("td", { class: "num" }, t.number), h("td", {}, t.artist), h("td", {}, t.title),
    h("td", { class: "src" }, provBadge(t.provenance.artist), provBadge(t.provenance.title),
      t.source_override ? h("span", { class: "badge user", title: `its audio comes from ${t.source_override}, which you chose, not the playlist's ${t.video_id}` }, `audio \u2190 ${t.source_override}`) : null,
      t.in_source === false ? h("span", { class: "badge", title: "no longer in the source playlist; a fetch keeps the file, “Remove gone tracks” moves it to the recycle bin" }, "gone") : null)));
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

// Files a plan names that are not there. Almost always one thing: the library was moved and noaap
// has not been told. Deliberately **not** the same as "the folder this album came from is gone" —
// those tracks are complete, and saying otherwise would call a working library broken.
// **Repairing the library, from the settings view and in two steps** (§9, slice 91). The user:
// *"repair library could go into settings, its function could be described better in there as
// 'repairing' could mean a lot of stuff and could also be dangerous."* So it says what it does, in
// full, and it is a check before it is an apply — the check writes nothing and lists every file it
// would touch (§9, slice 85).
let lastCheck = null;   // { version, lines } of the last check that was read

// **Asking the sources what changed, from the settings view** (§9, slice 91). The user did not want
// this in the header either, and there was nowhere in a header to say what it does.
function updateSection() {
  return h("div", { class: "setting update" },
    h("strong", {}, "Check the sources for new tracks"),
    h("div", { class: "muted" },
      "Asks every album's source what it holds now. An album that has not changed costs one request. "
      + "New tracks are downloaded, tracks that are gone from the source are marked ", h("em", {}, "gone"),
      " and kept — nothing is deleted — and the albums it touched are looked up at MusicBrainz and "
      + "LRCLIB unless you have switched that off. An album from a private source is never looked up."),
    h("div", { class: "actions" },
      h("button", { class: "quiet", type: "button",
        onclick: (e) => submit("update", {}, e.currentTarget) }, "Run"),
      h("button", { class: "quiet", type: "button",
        title: "Read every album in full instead of asking whether it changed — slower, and more requests",
        onclick: (e) => submit("update", { deep: true }, e.currentTarget) }, "Read every album in full")));
}

// The releases a lookup weighed and could not fit (§9, slice 145). 111 of the first 517 albums of
// the user's library are a release MusicBrainz has, refused over a handful of song names — and the
// pass said `0/0 tracks matched` and nothing about the nine releases it had just weighed. One click
// pins one; the normal look → apply follows.
function offersPanel(p) {
  const said = offersState({ offered: p.offered, mbid: p.mbid,
                             pinned: p.provenance?.mbid === "user" });
  if (!said) return null;
  return h("div", { class: "offers" },
    h("div", { class: "muted" }, said.note),
    h("div", { class: "actions" },
      h("button", { type: "button", onclick: () => chooseRelease(p) },
        `Which release is this? (${p.offered.length})`)),
    h("ul", {}, ...said.rows.map((row) =>
      h("li", {},
        h("a", { href: `https://musicbrainz.org/release/${row.id}`, target: "_blank",
          rel: "noopener" }, row.label),
        " ",
        row.current
          ? h("span", { class: "badge user" }, "pinned")
          : h("button", { class: "quiet small", type: "button",
              title: "Say this is the release, then look it up again",
              onclick: (e) => pinOffer(p, row.id, e.currentTarget) }, "this one")))));
}

// **the question asked as a question** (§9, slice 157; R-519 item 3). A list of ids and a row of
// buttons is not how a person decides between nine pressings of one single. A real `<dialog>` with
// a radio group is: the browser gives it the focus trap, Escape, Tab between the choices, arrow
// keys inside the group and the screen-reader announcement of "3 of 9", none of which hand-written
// markup gets right. The best fit is preselected, so Enter is the answer most of the time.
function chooseRelease(p, offered = null) {
  const said = offersToChoose({ offered: offered || p.offered, mbid: p.mbid });
  if (!said) return;
  const name = `offer-${Date.now()}`;
  const rows = said.rows.map((row) => {
    const radio = h("input", { type: "radio", name, value: row.id,
                               checked: row.id === said.choose });
    return h("label", { class: "offer-row" }, radio,
      h("span", {},
        h("strong", {}, row.title),
        row.note ? h("span", { class: "muted" }, ` — ${row.note}`) : null,
        h("div", { class: "muted small" }, row.facts.join(" · ")),
        h("div", { class: "muted small" }, row.fit, " · ",
          h("a", { href: `https://musicbrainz.org/release/${row.id}`, target: "_blank",
            rel: "noopener", onclick: (e) => e.stopPropagation() }, "on musicbrainz.org"))));
  });
  const dialog = h("dialog", { class: "source-dialog offers-dialog",
                               "aria-label": "Which release is this album?" },
    h("div", { class: "panel-head" }, h("h3", {}, `${p.albumartist} — ${p.album}`)),
    h("div", { class: "muted" },
      `MusicBrainz weighed ${p.offered.length} release(s) and none of them fitted on its own. `
      + "Choosing one says this album is that release: it is then yours, and every update takes "
      + "its names, numbers and discs from it."),
    h("form", { method: "dialog", id: "offerform",
                onsubmit: (e) => { e.preventDefault(); takeTheRelease(p, dialog, name); } },
      h("fieldset", { class: "offer-list" },
        h("legend", { class: "muted small" }, "the releases it weighed, the nearest first"),
        ...rows),
      h("div", { class: "actions" },
        h("button", { type: "submit" }, "This is the release"),
        h("button", { class: "quiet", type: "button",
                      onclick: () => dialog.close() }, "Cancel"))));
  document.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove());
  dialog.showModal();
  dialog.querySelector(`input[name="${name}"]:checked`)?.focus();
}

async function takeTheRelease(p, dialog, name) {
  const chosen = dialog.querySelector(`input[name="${name}"]:checked`)?.value;
  if (!chosen) return;
  dialog.close();
  const job = await submit("edit", { id: p.source_id, edits: { mbid: chosen } });
  if (job == null) return;
  await jobSettled(job, 1200);
  lastIdentify = null;
  await poll();
  await refreshAlbumPanel();
  // **and the look is run, which is what was wanted** (R-519 item 3). Pinning on its own left the
  // user to find the Identify button again and press it; the point of answering the question is to
  // see what the answer does.
  const panel = $("#album");
  if (!panel.hidden) await checkIdentify(p, null);
}

async function pinOffer(p, id, button) {
  if (!confirm(`Pin this album to release ${id}?\n\nIt is then yours: every update takes its names, `
               + "numbers and discs from it and never searches for another.")) return;
  const job = await submit("edit", { id: p.source_id, edits: { mbid: id } }, button);
  if (job == null) return;
  await jobSettled(job, 1200);
  lastIdentify = null;                 // the album has changed: look again
  await poll();
  await refreshAlbumPanel();
}

// Where this album came from: a link only where the source is one a browser can open, and named
// after the provider that minted it (§9, slice 143). For the 1,312 albums adopted from folders this
// was a link to a path on the NAS labelled "open on YouTube".
// **the cover, and a way to choose one** (§9, slice 156; R-519 item 2). A picture the user hands
// over is theirs: it is written as it is and no pass replaces it, which is what `cover_fetched`
// being empty means in the plan. Either a file from their machine or an address they paste.
function coverBlock(p) {
  const done = p.tracks.filter((t) => t.state === "done").length;
  const picture = h("img", { class: "album-cover",
    src: `/api/cover?id=${encodeURIComponent(p.source_id)}&t=${done}&v=${coverVersion}`,
    alt: "", title: "Play album", onclick: () => playAlbum(p.source_id, 0),
    onerror: (e) => { e.currentTarget.hidden = true; } });
  const file = h("input", { type: "file", id: "coverfile", class: "hidden-file",
    accept: "image/jpeg,image/png,image/webp,image/gif",
    onchange: (e) => sendCoverFile(p, e.currentTarget) });
  return h("div", { class: "album-cover-box" }, picture,
    h("div", { class: "cover-choose" },
      file,
      h("button", { class: "quiet small", type: "button",
        onclick: () => file.click() }, "Choose a cover…"),
      h("button", { class: "quiet small", type: "button",
        onclick: () => sendCoverUrl(p) }, "…or paste a link")));
}

// bumped after a cover is written, so the browser fetches the new picture rather than its cache
let coverVersion = 0;

async function sendCoverFile(p, input) {
  const chosen = input.files?.[0];
  input.value = "";
  if (!chosen) return;
  if (chosen.size > 20 * 1024 * 1024) return toast("That picture is larger than 20 MB", "blocked");
  const bytes = new Uint8Array(await chosen.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 8192) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  }
  await sendCover(p, { data: btoa(binary) }, `“${chosen.name}”`);
}

async function sendCoverUrl(p) {
  const said = prompt("The address of a picture (http:// or https://):", "");
  const url = (said || "").trim();
  if (!url) return;
  if (!/^https?:\/\//i.test(url)) return toast("A cover address has to be http:// or https://", "blocked");
  await sendCover(p, { url }, url);
}

async function sendCover(p, what, named) {
  const id = await submit("cover", { id: p.source_id, ...what });
  if (id == null) return;
  const job = await jobSettled(id, 2400);
  if (!job || job.state !== "done") return;      // submit() and the job log have said why
  coverVersion++;
  toast(`Cover set from ${named}`, "ok");
  await poll();
  await refreshAlbumPanel();
}

function sourceOpening(p) {
  const said = sourceOpen(p);
  if (!said) return null;
  return said.href
    ? h("a", { href: said.href, target: "_blank", rel: "noopener" }, said.label)
    : h("span", { class: "muted", title: p.source_url }, said.text);
}

// -- "Complete from the release" (§9, slice 162) ------------------------------------------------
//
// Look first: the check searches for each missing track and writes nothing. The dialog then lists
// what it found, ticked where there is a hit, and fetches only what stays ticked.

async function checkComplete(p, button) {
  const id = await submit("complete", { id: p.source_id, dry_run: true }, button);
  if (id == null) return;
  const job = await jobSettled(id, 4800);
  if (!job || job.state !== "done" || !job.result) return;      // submit() and the log have said why
  const result = job.result;
  if (result.status !== "dry") return toast(result.message || "nothing to look for", "failed");
  const rows = completeRows(result.offered || []);
  const notes = (result.changes || []).filter((line) => !/^\d+-\d{2} /.test(line));
  const boxes = rows.map((row) => h("label", { class: "offer-row" },
    h("input", { type: "checkbox", value: row.name, checked: row.can, disabled: !row.can }),
    h("span", {}, h("strong", {}, row.title), h("div", { class: "muted small" }, row.why))));
  const dialog = h("dialog", { class: "source-dialog offers-dialog", "aria-label": "Complete this album" },
    h("div", { class: "panel-head" }, h("h3", {}, `${p.albumartist} — ${p.album}`)),
    ...notes.map((line) => h("div", { class: "muted" }, line)),
    rows.length ? null : h("div", {}, "This album holds every track of its release."),
    h("form", { method: "dialog", onsubmit: (e) => { e.preventDefault(); fetchComplete(p, dialog); } },
      rows.length ? h("fieldset", { class: "offer-list" },
        h("legend", { class: "muted small" }, "the release's tracks this album has no file for"), ...boxes) : null,
      h("div", { class: "actions" },
        rows.some((r) => r.can) ? h("button", { type: "submit" }, "Fetch the ticked tracks") : null,
        h("button", { class: "quiet", type: "button", onclick: () => dialog.close() }, "Close"))));
  document.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove());
  dialog.showModal();
}

async function fetchComplete(p, dialog) {
  const only = [...dialog.querySelectorAll("input[type=checkbox]:checked")].map((b) => b.value);
  if (!only.length) return;
  dialog.close();
  await submit("complete", { id: p.source_id, only });
}

// -- "Identify with MusicBrainz" (§9, slice 142) ----------------------------------------------
//
// Two steps, like a repair: look first, apply what was listed. The lookup rewrites titles, numbers
// and discs, so a person who cannot see that first is being asked to trust it blind.

let lastIdentify = null;   // { id, version, lines, matched, offered } of the album last looked up

function identifyButton(p) {
  const mine = lastIdentify && lastIdentify.id === p.source_id ? lastIdentify : null;
  const said = identifyState({ check: mine, version: state.tracks_version,
                               pinned: p.provenance?.mbid === "user",
                               running: Boolean(state.busy_write) });
  const box = h("span", { class: "identify" });
  const label = mine && said.canApply ? "Apply what MusicBrainz says" : "Identify with MusicBrainz";
  fill(box,
    h("button", { class: "quiet", type: "button",
      disabled: !(said.canApply || said.canCheck), title: said.note,
      onclick: (e) => (mine && said.canApply ? applyIdentify : checkIdentify)(p, e.currentTarget) },
      label),
    // **the candidates the check weighed, straight from the check** (§9, slice 158; R-530). A
    // check writes nothing, so they cannot come from the plan on disk — and the panel used to
    // answer a refused lookup with "MusicBrainz has no release that matches this album".
    said.canChoose
      ? h("button", { class: "quiet", type: "button",
          onclick: () => chooseRelease(p, mine.offered) },
          `Which release is this? (${mine.offered.length})`)
      : null,
    mine ? h("span", { class: "muted" }, " ", said.note) : null);
  return box;
}

async function checkIdentify(p, button) {
  const version = state.tracks_version;
  const id = await submit("identify", { id: p.source_id, dry_run: true }, button);
  if (id == null) return;
  const job = await jobSettled(id, 1200);
  if (!job || job.state !== "done") return;      // submit() and the job log have said why
  const lines = identifyLines(job);
  const outcomes = Array.isArray(job.result) ? job.result : [];
  lastIdentify = { id: p.source_id, version, lines,
                   offered: outcomes.flatMap((o) => (Array.isArray(o?.offered) ? o.offered : [])),
                   matched: (job.log || []).some((l) => l.includes("release matched")
                                                     || l.includes("release pinned by you")) };
  await refreshAlbumPanel();
}

async function applyIdentify(p, button) {
  if (!lastIdentify || lastIdentify.id !== p.source_id) return;
  if (!confirm("Apply what MusicBrainz says about this album?\n\n"
               + lastIdentify.lines.slice(0, 12).join("\n")
               + (lastIdentify.lines.length > 12
                  ? `\n… and ${lastIdentify.lines.length - 12} more lines` : "")
               + "\n\nThis is what the check listed. Nothing you typed yourself is changed.")) return;
  const id = await submit("identify", { id: p.source_id }, button);
  if (id == null) return;
  await jobSettled(id, 4800);
  lastIdentify = null;                           // the album has changed: look again
  await poll();
  await refreshAlbumPanel();
}

function repairSection() {
  const box = h("div", { class: "setting repair" });
  fillRepair(box);
  return box;
}

function fillRepair(box) {
  const said = repairState({ check: lastCheck, version: state.tracks_version,
                             running: Boolean(state.busy_write) });
  fill(box,
    h("strong", {}, "Repair the library"),
    h("div", { class: "muted" },
      "Once through every album, offline. It renames files and folders to noaap's scheme, rewrites the "
      + "tags from the plan, applies trim points that are not in the file yet, cuts a file again if its "
      + "clock starts before zero, fills in the measured length of each track, tidies artist names "
      + "(performer only, guests moved into the title, one spelling per artist, the album's own name "
      + "removed from its track titles) and drops a track listed twice."),
    h("div", { class: "muted" },
      "It never downloads anything and never asks anybody about your music. Nothing is deleted: where a "
      + "file is replaced, the one that was there goes to the recycle bin."),
    h("div", { class: "muted" }, said.note),
    h("div", { class: "actions" },
      h("button", { class: "quiet", type: "button", disabled: !said.canCheck,
        onclick: (e) => checkRepair(box, e.currentTarget) }, "Check"),
      h("button", { class: "quiet", type: "button", disabled: !said.canApply,
        onclick: (e) => applyRepair(box, e.currentTarget) }, "Apply")),
    lastCheck ? h("pre", { class: "check" }, lastCheck.lines.join("\n") || "nothing to do") : null);
}

async function checkRepair(box, button) {
  const version = state.tracks_version;
  const id = await submit("repair", { dry_run: true }, button);
  if (id == null) return;
  const job = await jobSettled(id, 1200);
  if (!job || job.state !== "done") return;          // submit() and the job log have said why
  // the log is what it would do, line by line; the result is the albums it would touch, which is what
  // says whether there is anything to apply at all
  lastCheck = { version, albums: (job.result || []).length,
                lines: (job.log || []).filter((line) => !line.startsWith("===")) };
  fillRepair(box);
}

async function applyRepair(box, button) {
  if (!lastCheck) return;
  if (!confirm("Apply this repair?\n\n" + lastCheck.lines.slice(0, 12).join("\n")
               + (lastCheck.lines.length > 12 ? `\n… and ${lastCheck.lines.length - 12} more lines` : "")
               + "\n\nThis is what the check listed. Nothing is downloaded and nothing is deleted.")) return;
  const id = await submit("repair", {}, button);
  if (id == null) return;
  await jobSettled(id, 4800);
  lastCheck = null;                                   // the library has changed: check again
  await poll();                                       // …and it is no longer the writer, which the note says
  fillRepair(box);
  await refreshAlbumPanel();
}

function missingSection(missing) {
  if (!missing || !missing.albums) return null;
  return h("div", { class: "setting" },
    h("strong", { class: "bad" }, `${missing.tracks} track(s) are not where their plan says`),
    h("div", { class: "muted" },
      `in ${missing.albums} album(s): `, missing.where.join(", "),
      missing.albums > missing.where.length ? " \u2026" : "",
      h("br"),
      "If you moved the library, point noaap at it \u2014 the folder above, or `noaap config --library PATH`."));
}

// **Another folder taken into the library** (§9, slice 92). The user asked how to configure
// *"another local or nas folder for merge"*, and the answer was a command line. It runs the way the
// repair does: Check writes nothing and lists every line the pass would print, Apply does exactly
// what that check listed. The folder named here is read; the library is what changes.
let lastTakeIn = null;   // { folder, mode, lines } of the last check that was read

const TAKE_IN_MODES = [
  ["merge", "merge \u2014 compare it with this library, keep the better copy"],
  ["adopt", "adopt \u2014 take it in where it stands, one plan per album"],
  ["take-in", "take in \u2014 a whole collection, to one state: adopt, look up, cover, word, tag"],
];

// **What a take-in does tonight** (§9, slice 101). Each is on unless switched off, and none of them is
// recorded against the album: what an album should have is a setting, and the next check or repair
// brings every album to it. They are here so that eleven thousand tracks can be taken in quickly.
const TAKE_IN_SWITCHES = [
  ["musicbrainz", "Look the albums up at MusicBrainz", "names, years, covers and tracklists"],
  ["lyrics", "Look the words up at LRCLIB", "a `.lrc` beside each track"],
  ["cover_beside", "Write a cover beside each album", "`cover.jpg` in the album folder"],
  ["cover_embedded", "Embed the cover in the files", "a rewrite of every file"],
  ["lyrics_embedded", "Embed the words in the files", "the `LYRICS` tag beside the `.lrc`"],
  ["tags", "Write noaap's tags into the files",
   "fields this program does not model \u2014 your comment, your replaygain \u2014 are left alone"],
];

function takeInSection() {
  const folder = h("input", { type: "text", class: "take-in-folder", autocomplete: "off", spellcheck: "false",
                              placeholder: "/mnt/nas/Music", "aria-label": "the folder to take in" });
  const mode = h("select", { class: "take-in-mode" },
    TAKE_IN_MODES.map(([value, text]) => h("option", { value }, text)));
  // **what is offered is what the library is set to** (R-410, ruling 3): the switch above says
  // renaming an adopted album is off by default, and this list used to open on "rename" anyway.
  const renaming = Boolean(state.settings?.state?.rename_adopted);
  const names = h("select", { class: "take-in-names" },
    h("option", { value: "scheme", selected: renaming }, "rename into noaap's scheme"),
    h("option", { value: "keep", selected: !renaming }, "keep the collection's own names"));
  const switches = new Map(TAKE_IN_SWITCHES.map(([key]) =>
    [key, h("input", { type: "checkbox", checked: true })]));
  const keep = h("input", { type: "text", class: "take-in-keep", autocomplete: "off", spellcheck: "false",
                            placeholder: "/home/you/noaap-originals",
                            "aria-label": "where to keep the untouched originals" });
  const choices = h("div", { class: "take-in-choices", hidden: true },
    h("div", { class: "muted" },
      "What this run does. None of it is recorded against the albums \u2014 what an album should have "
      + "is a setting above, and the next check or repair brings every album to it."),
    h("label", { class: "setting" },
      h("span", {}, h("strong", {}, "Names"), h("small", { class: "muted" }, "the files and the folders")),
      names),
    ...TAKE_IN_SWITCHES.map(([key, title, help]) => h("label", { class: "setting" },
      h("span", {}, h("strong", {}, title), h("small", { class: "muted" }, help)), switches.get(key))),
    h("label", { class: "setting" },
      h("span", {}, h("strong", {}, "Keep the originals in"),
        h("small", { class: "muted" },
          "a folder outside the collection: each file is copied there whole before its first write, "
          + "which is the only way back byte for byte. Costs as much disk as the collection.")),
      keep));
  const note = h("div", { class: "muted take-in-note" });
  const check = h("button", { class: "quiet", type: "button" }, "Check");
  const apply = h("button", { class: "quiet", type: "button" }, "Apply");
  const listing = h("pre", { class: "check" });
  const update = () => {
    const said = takeInState({ folder: folder.value, mode: mode.value, check: lastTakeIn,
                               running: Boolean(state.busy_write) });
    note.textContent = said.note;
    check.disabled = !said.canCheck;
    apply.disabled = !said.canApply;
    // the lines belong to the folder they were read for: another folder in the field is another question
    listing.hidden = !said.matches;
    listing.textContent = said.matches ? (lastTakeIn.lines.join("\n") || "nothing to do") : "";
  };
  folder.addEventListener("input", update);
  mode.addEventListener("change", () => { choices.hidden = mode.value !== "take-in"; update(); });
  // Enter in a path field would submit the settings form, which is not what anybody means by it
  folder.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); check.click(); } });
  // what the two buttons send: the folder, the way of taking it in, and — for a take-in — its switches
  const asked = () => (mode.value !== "take-in" ? {} : {
    names: names.value, keep_originals: keep.value.trim(),
    ...Object.fromEntries([...switches].map(([key, box]) => [key, box.checked])),
  });
  check.addEventListener("click", () => checkTakeIn(folder.value.trim(), mode.value, check, update, asked()));
  apply.addEventListener("click", () => applyTakeIn(apply, update, asked()));
  update();
  return h("div", { class: "setting take-in" },
    h("strong", {}, "Take in a folder"),
    h("div", { class: "muted" },
      "A folder somewhere else on this machine, or a share you have mounted \u2014 name it in full. "
      + "What happens to it depends on which of the two ways you choose:"),
    h("div", { class: "muted" },
      h("em", {}, "merge"), " compares that folder with this library track by track and copies in only what "
      + "is better; a file it replaces goes to the recycle bin, so nothing is deleted. ",
      h("em", {}, "adopt"), " writes one plan file per album in that folder and nothing else \u2014 no file is "
      + "renamed, moved or retagged. That makes the collection readable where it stands, as its own root: "
      + "point the library at it afterwards, watch it, or merge from it."),
    h("label", { class: "setting" },
      h("span", {}, h("strong", {}, "Folder"), h("small", { class: "muted" }, "a local path, or a mounted share")),
      folder),
    h("label", { class: "setting" },
      h("span", {}, h("strong", {}, "How"), h("small", { class: "muted" }, "the same ways the command line offers")),
      mode),
    choices,
    note,
    h("div", { class: "actions" }, check, apply),
    listing);
}

async function checkTakeIn(folder, mode, button, update, asked = {}) {
  const id = await submit("take_in", { folder, mode, dry_run: true, ...asked }, button);
  if (id == null) return;
  const job = await jobSettled(id, 2400);
  if (!job || job.state !== "done") return;          // submit() and the job log have said why
  lastTakeIn = { folder, mode, lines: (job.log || []).filter((line) => !line.startsWith("===")) };
  update();
}

async function applyTakeIn(button, update, asked = {}) {
  if (!lastTakeIn) return;
  const { folder, mode, lines } = lastTakeIn;
  if (!confirm(`Take ${folder} into the library (${mode})?\n\n`
               + lines.slice(0, 12).join("\n")
               + (lines.length > 12 ? `\n\u2026 and ${lines.length - 12} more lines` : "")
               + "\n\nThis is what the check listed. Nothing is deleted"
               + (mode === "merge" ? " and that folder is not written to." : "."))) return;
  const id = await submit("take_in", { folder, mode, ...asked }, button);
  if (id == null) return;
  await jobSettled(id, 4800);
  lastTakeIn = null;                                  // the library has changed: check again
  await poll();                                       // …and it is no longer the writer, which the note says
  update();
  await refreshAlbumPanel();
}

// The two shapes a watched folder can have, which are the config's own (§9, slice 92). **This app does
// not watch anything** — a separate process does, so that this one can keep stopping itself when idle —
// and what a row *reports* is read from what that process wrote down, which is the only way this page
// can be honest about it.
const WATCH_SHAPES = [
  ["intake", "things dropped here are taken in"],
  ["library", "this library, watching itself"],
];

// What noaap moved aside instead of deleting (§9, slice 49). It never empties itself, so the only
// way anything leaves is from here or `noaap recycle empty` — which is the point of having it.
//
// **Its own view, from the header** (§9, slice 91). It used to sit at the bottom of the settings panel,
// where the user did not think it belonged: settings are what the program should do, and the bin is a
// place with their audio in it.
function openRecycle() {
  const panel = $("#recycle");
  const box = h("div", { class: "recycle" }, h("div", { class: "muted" }, "loading…"));
  fill(panel, h("div", { class: "panel-head" }, h("h2", {}, "Recycle bin"),
                h("button", { class: "quiet", type: "button", onclick: () => { panel.hidden = true; } }, "Close")),
       box);
  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
  loadRecycle(box);
}

function renderBin() {
  const button = $("#bin");
  const said = binLabel(state.recycled);
  button.hidden = said.hidden && $("#recycle").hidden;   // stays reachable while the view is open
  button.textContent = said.text;
  button.title = said.title;
}

async function loadRecycle(box) {
  let rows = [];
  try {
    rows = (await (await fetch("/api/recycle")).json()).entries || [];
  } catch { /* the panel is still useful without it */ }
  const size = rows.reduce((n, r) => n + (r.bytes || 0), 0);
  fill(box,
    h("div", { class: "muted" }, rows.length
      ? `${rows.length} thing(s) noaap moved aside instead of deleting, ${(size / 1e6).toFixed(1)} MB. `
        + "Nothing here is ever removed on its own."
      : "Empty. When noaap deletes or prunes a track, the audio comes here first."),
    ...rows.map((r) => h("div", { class: "recycle-row" },
      h("span", {}, `${r.artist} — ${r.title}`),
      h("span", { class: "muted" }, `${r.album} · ${r.reason} · ${(r.bytes / 1e6).toFixed(1)} MB`),
      r.track ? h("button", { class: "quiet small", type: "button",
        onclick: (e) => restoreEntry(r, e.currentTarget, box) }, "Put it back") : null)),
    rows.length ? h("button", { class: "quiet small danger-text", type: "button",
      onclick: (e) => emptyRecycle(rows, e.currentTarget, box) }, "Empty the bin") : null);
}

async function restoreEntry(row, button, box) {
  const id = await submit("restore", { entry: row.id }, button);
  if (id != null) await jobSettled(id);
  await loadRecycle(box);
  await refreshAlbumPanel();
}

async function emptyRecycle(rows, button, box) {
  const size = rows.reduce((n, r) => n + (r.bytes || 0), 0);
  if (!confirm(`Empty the recycle bin?\n\n${rows.length} thing(s), ${(size / 1e6).toFixed(1)} MB.\n`
      + "This is the one place where noaap really does delete audio, and it cannot be undone.")) return;
  const id = await submit("empty_recycle", {}, button);
  if (id != null) await jobSettled(id);
  await loadRecycle(box);
}

function renderSettings() {} // the panel is built when opened, so polling never overwrites what you type

// What each provider row says under its label: what that choice means for the audio, and — for the
// ones that charge — their list price with the date it was read (§9, slice 37). One row per capability
// (§9, slice 40), because the two can be different providers and each has its own answer.
function timingHelp(st, what) {
  const base = what === "align"
    ? "who may place timestamps on the words you have: nobody, a model on this machine (the "
      + "noaap[timing] extra), another machine running `noaap timing-serve`, or a paid service"
    : "who may write down the words of a track that has none — a guess, offered only where there is "
      + "nothing to lose";
  // whether a second method checks every alignment is worth a sentence: it changes what the user
  // gets (fewer stamps, and only agreed ones) and how long they wait (§9, slice 38)
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

// A row of the settings that belongs to one timing field, marked with the field it is, so that
// showTimingFields can show exactly the ones the chosen providers use.
function detailRow(field, label, help, input) {
  return h("label", { class: "setting", "data-timing": field },
    h("span", {}, h("strong", {}, label), h("small", { class: "muted" }, help)), input);
}

// **Only the fields the chosen providers use** (§9, slice 92). The user: *"timing provider details only
// need to be visible when they are needed (provider selected)"* — the panel asked for an endpoint with
// `local` chosen and for two API keys on a machine with no account anywhere. The rows are hidden, never
// removed: what was typed is still there when the provider comes back, and a hidden row is not sent, so
// what the config already holds is left alone.
function timingDetails(st) {
  const keySet = (v) => st.timing?.keys?.[v];
  return h("div", { class: "timing-detail" },
    detailRow("endpoint", "Timing endpoint",
      "the machine running `noaap timing-serve` \u2014 the audio never leaves your network",
      h("input", { type: "text", name: "timing_endpoint", value: st.timing?.endpoint || "",
                   placeholder: "http://host:8770" })),
    detailRow("device", "Where the local model runs",
      "`auto` uses the graphics card when there is one with room; `cpu` never does, and is slower",
      h("select", { name: "timing_device" }, ["auto", "cpu", "cuda"].map((d) =>
        h("option", { value: d, selected: d === (st.timing?.device || "auto") }, d)))),
    detailRow("verify", "Check every alignment twice",
      "a second method here confirms each line: about half again as long, and lines the two disagree "
      + "about come back without a stamp",
      h("select", { name: "timing_verify" }, [["auto", "auto \u2014 when both methods are installed"],
                                              ["true", "always"], ["false", "never"]].map(([v, text]) =>
        h("option", { value: v, selected: v === String(st.timing?.verify ?? "auto") }, text)))),
    ...(st.timing?.vendors || []).map((v) => detailRow(`key:${v}`, `${v} API key`,
      keySet(v) ? "a key is set; type a new one to replace it, or a single space to remove it"
        : `needed for the ${v} provider. It stays on this machine and is never shown again.`,
      h("input", { type: "password", name: `timing_${v}_key`, value: "", autocomplete: "off",
                   placeholder: keySet(v) ? "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022 (set)" : "" }))));
}

function showTimingFields(form) {
  const said = timingFields({ align: form.timing_align_provider?.value,
                              draft: form.timing_draft_provider?.value });
  for (const el of form.querySelectorAll("[data-timing]")) {
    const field = el.getAttribute("data-timing");
    el.hidden = field.startsWith("key:") ? !said.showsKey(field.slice(4)) : !said.shows(field);
  }
  const box = form.querySelector(".timing-detail");
  if (box) box.hidden = !(said.fields.length || said.keys.length);
}

// **Where else music comes from, as a list** (§9, slice 95). The user, of slice 92's section: *"sources
// as cards or rows that can be added and removed and have their own config dialog once you add or edit
// them, so you have all sources together visible and do not have each source's settings mess exposed
// right away."* So the settings show **which** sources are set up, one row each, and the fields live in
// a dialog that holds one provider and nothing of anybody else.
//
// The rows come from what the server answers, and the server builds that from the registry and the
// provider's own field names: the page does not know which sources exist, and one that needs a session
// appears here the day it registers itself.
const SOURCE_FIELD = {
  cookies_from_browser: (name) => [`${name} session from a browser`,
    `the browser you are logged in to ${name} with. Its cookies are read on this machine while a `
    + "download runs, and are never written down or sent anywhere."],
  cookies_file: () => ["\u2026 or a cookies file",
    "the full path of a cookies.txt you exported. The path is all that is kept here \u2014 the file is read "
    + "only while a download runs, and its contents never reach this page."],
  audio_from_video: () => ["Take the audio out of a video post",
    "a post whose audio is only in a video: the audio stream is taken as it is, never re-encoded"],
  captions: () => ["Keep the post's captions as lyrics",
    "when a post carries captions, they are kept as the track's words instead of asking LRCLIB"],
};

function sourcesSection() {
  const box = h("div", { class: "setting sources" });
  fillSources(box);
  return box;
}

const FOLDER = "a watched folder";   // what the add-list calls one, and there may be any number

function fillSources(box) {
  const st = state.settings || {};
  const said = sourceRows(st.sources || {}, st.watching || []);
  // a folder is always on offer; a provider only while it is not set up
  const offers = [...said.unset.map((name) => [name, sourceLabel(name)]), ["folder", FOLDER]];
  const chosen = h("select", { class: "add-source", "aria-label": "a source to set up" },
    offers.map(([value, label]) => h("option", { value }, label)));
  const watcher = (st.watching || []).length
    ? ((st.watching || []).some((w) => w.looked)
        ? "The watcher is a separate service \u2014 `noaap watch`, which you start yourself; it has been "
          + "here, and each folder says when."
        : "The watcher is a separate service \u2014 `noaap watch`, which you start yourself. No folder has "
          + "been looked at yet, so it is probably not running.")
    : "";
  fill(box,
    // the "Sources" heading is the section's own; this box says what a source is
    h("div", { class: "muted" },
      "Where music comes from besides YouTube: a provider that keeps something behind a login, and "
      + "every folder that is watched \u2014 what is dropped into one is taken in without anyone typing "
      + "a command. ", watcher,
      " Taking a folder in once, rather than watching it, is under Library below."),
    said.none
      ? h("div", { class: "muted" }, "None is set up yet.")
      : h("div", { class: "source-rows" }, said.rows.map((row) => h("div", { class: "source-row" },
          h("strong", {}, row.label),
          h("span", { class: "muted" }, row.summary),
          h("span", { class: "row-actions" },
            h("button", { class: "quiet small", type: "button",
              onclick: () => (row.kind === "folder"
                ? openFolderDialog(row.index, box) : openSourceDialog(row.name, box)) }, "Edit"),
            h("button", { class: "quiet small danger-text", type: "button",
              onclick: (e) => (row.kind === "folder"
                ? removeFolder(row.index, box, e.currentTarget)
                : removeSource(row.name, box, e.currentTarget)) }, "Remove"))))),
    h("div", { class: "add-row" }, chosen,
      h("button", { class: "quiet small", type: "button",
        onclick: () => (chosen.value === "folder"
          ? openFolderDialog(null, box) : openSourceDialog(chosen.value, box)) }, "Add a source")));
}

// **A watched folder is a source** (§9, slice 98), so it is set up the way the others are: one dialog
// holding one folder, with the rules the config applies — both shapes, an absolute path, its own name,
// and never the library or a folder nested with another watch.
function openFolderDialog(index, box) {
  const st = state.settings || {};
  const watches = st.watching || [];
  const now = index == null ? { name: "", folder: "", shape: "intake" } : watches[index] || {};
  const name = h("input", { type: "text", value: now.name || "", autocomplete: "off",
                            placeholder: "a name" });
  const folder = h("input", { type: "text", value: now.folder || "", autocomplete: "off",
                              spellcheck: "false", placeholder: "/mnt/nas/incoming" });
  const shape = h("select", {}, WATCH_SHAPES.map(([value, text]) =>
    h("option", { value, selected: value === (now.shape || "intake") }, text)));
  const note = h("div", { class: "muted dialog-note" });
  const row = (title, help, input) => h("label", { class: "setting" },
    h("span", {}, h("strong", {}, title), h("small", { class: "muted" }, help)), input);
  const said = () => {
    const trouble = watchTrouble(watchesAfter(watches, index, { name: name.value, folder: folder.value,
                                                               shape: shape.value }), st.library || "");
    note.textContent = trouble[0] || "";
    return trouble;
  };
  for (const el of [name, folder]) el.addEventListener("input", said);
  shape.addEventListener("change", said);
  const dialog = h("dialog", { class: "source-dialog" },
    h("div", { class: "panel-head" }, h("h3", {}, index == null ? "A watched folder" : (now.name || "A watched folder"))),
    h("div", { class: "muted" },
      "What arrives in this folder is taken in without anyone typing a command. The watcher is a "
      + "separate service \u2014 `noaap watch` \u2014 which you start yourself; this only writes down what "
      + "it should look at."),
    row("Name", "the watcher writes down what it did under it", name),
    row("Folder", "in full, and not the library or a folder inside it", folder),
    row("What it is", "the two shapes the config allows", shape),
    note,
    h("div", { class: "actions" },
      h("button", { type: "button",
        onclick: (e) => saveFolder(index, { name, folder, shape }, dialog, box, note, e.currentTarget) }, "Save"),
      h("button", { class: "quiet", type: "button", onclick: () => dialog.close() }, "Cancel")));
  document.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove());
  dialog.showModal();
  name.focus();
}

async function saveFolder(index, fields, dialog, box, note, button) {
  const st = state.settings || {};
  const watches = watchesAfter(st.watching || [], index,
    { name: fields.name.value, folder: fields.folder.value, shape: fields.shape.value });
  const trouble = watchTrouble(watches, st.library || "");
  if (trouble.length) { note.textContent = trouble[0]; return; }
  setWorking(button, true);
  try {
    state.settings = await api("/api/settings", { watches });
    toast("\u2713 Watched folders saved", "done");
    dialog.close();
    fillSources(box);
  } catch (e) {
    note.textContent = e.message;
  } finally {
    setWorking(button, false);
  }
}

async function removeFolder(index, box, button) {
  const st = state.settings || {};
  const gone = (st.watching || [])[index] || {};
  if (!confirm(`Stop watching ${gone.name || gone.folder}?\n\n`
               + `${gone.folder}\n\nThe folder and everything in it is left exactly as it is — noaap `
               + "only stops looking at it. You can add it again at any time.")) return;
  setWorking(button, true);
  try {
    state.settings = await api("/api/settings", { watches: watchesWithout(st.watching || [], index) });
    toast("\u2713 Not watched any more", "done");
    fillSources(box);
  } catch (e) {
    toast(e.message, "failed");
  } finally {
    setWorking(button, false);
  }
}

// One provider's fields, in a dialog of its own: the sentences are slice 92's, the fields are this
// provider's, and a key or a path of another provider cannot be reached from here.
function openSourceDialog(name, box) {
  const st = state.settings || {};
  const fields = st.sources?.[name] || {};
  const shown = dialogFields(name, st.sources || {});
  if (!shown.length) return toast(`${sourceLabel(name)} has nothing to configure`, "blocked");
  const label = sourceLabel(name);
  const inputs = new Map();
  const row = (field) => {
    const [title, help] = SOURCE_FIELD[field](label);
    const input = sourceInput(name, field, fields[field], ["", ...(st.browsers || [])]);
    inputs.set(field, input);
    return h("label", { class: "setting" },
      h("span", {}, h("strong", {}, title), h("small", { class: "muted" }, help)), input);
  };
  const note = h("div", { class: "muted dialog-note" });
  const dialog = h("dialog", { class: "source-dialog" },
    h("div", { class: "panel-head" }, h("h3", {}, label)),
    h("div", { class: "muted" },
      `What ${label} keeps behind a login needs your own session, which noaap reads on this machine and `
      + "sends to nobody. A cookies file is kept as a path; its contents never reach this page."),
    ...shown.map(row),
    note,
    h("div", { class: "actions" },
      h("button", { type: "button", onclick: (e) => saveSource(name, inputs, dialog, box, note, e.currentTarget) }, "Save"),
      h("button", { class: "quiet", type: "button", onclick: () => dialog.close() }, "Cancel")));
  document.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove());
  dialog.showModal();
  dialog.querySelector("input, select")?.focus();
}

function sourceInput(name, field, value, browsers) {
  if (field === "cookies_from_browser") {
    const known = browsers.includes(value) ? browsers : [...browsers, value];
    return h("select", { name: `${name}_${field}` }, known.map((b) =>
      h("option", { value: b, selected: b === (value || "") }, b ? browserLabel(b) : "none")));
  }
  if (field === "cookies_file") {
    return h("input", { type: "text", name: `${name}_${field}`, value: value || "", autocomplete: "off",
                        spellcheck: "false", placeholder: `/home/you/${name}-cookies.txt` });
  }
  return h("input", { type: "checkbox", name: `${name}_${field}`, checked: Boolean(value) });
}

async function saveSource(name, inputs, dialog, box, note, button) {
  const body = {};
  for (const [field, el] of inputs) {
    body[`${name}_${field}`] = el.type === "checkbox" ? el.checked : el.value.trim();
  }
  setWorking(button, true);
  try {
    state.settings = await api("/api/settings", body);
    toast(`\u2713 ${sourceLabel(name)} saved`, "done");
    dialog.close();
    fillSources(box);
  } catch (e) {
    note.textContent = e.message;     // said in the dialog, where the field that is wrong still is
  } finally {
    setWorking(button, false);
  }
}

async function removeSource(name, box, button) {
  const st = state.settings || {};
  if (!confirm(removeConfirm(name, st.sources || {}))) return;
  setWorking(button, true);
  try {
    state.settings = await api("/api/settings", clearedSource(name, st.sources || {}));
    toast(`\u2713 ${sourceLabel(name)}'s settings cleared`, "done");
    fillSources(box);
  } catch (e) {
    toast(e.message, "failed");
  } finally {
    setWorking(button, false);
  }
}

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
        h("select", { name: "cookies_from_browser" }, browsers.map((b) => h("option", { value: b, selected: b === (st.cookies_from_browser || "") }, b ? browserLabel(b) : "none")))),
      row("Token helper", "proof-of-origin tokens for streams YouTube withholds; server = started on demand",
        h("select", { name: "pot_mode" }, ["server", "script", "off"].map((m) => h("option", { value: m, selected: m === st.pot_mode }, m)))),
      row("Token server stops after", "minutes without YouTube activity",
        h("input", { type: "number", name: "pot_idle_minutes", min: 1, max: 120, value: st.pot_idle_minutes })),
      row("Parallel YouTube requests", "1–4; more is faster but trips YouTube's bot check sooner",
        h("input", { type: "number", name: "concurrency", min: 1, max: 4, value: st.concurrency })),
      h("h3", {}, "Timing"),
      row("Placing words on the clock", timingHelp(st, "align"),
        h("select", { name: "timing_align_provider", onchange: (e) => showTimingFields(e.currentTarget.form) },
          providersFor(st, "align").map((m) =>
            h("option", { value: m, selected: m === providerFor("align") }, m)))),
      row("Drafting words for a track that has none", timingHelp(st, "transcribe"),
        h("select", { name: "timing_draft_provider", onchange: (e) => showTimingFields(e.currentTarget.form) },
          providersFor(st, "transcribe").map((m) =>
            h("option", { value: m, selected: m === providerFor("transcribe") }, m)))),
      timingDetails(st),
      h("h3", {}, "Sources"),
      sourcesSection(),
      h("h3", {}, "What every album should have"),
      h("p", { class: "muted setting" },
        "The state this library is in. A pass brings the albums it touches to these, so switching one "
        + "on today reaches every album the next time you check or repair — you do not visit them. "
        + "One album can be excepted in its own view."),
      STATE_SWITCHES.map(([key, title, help]) =>
        row(title, help, h("input", { type: "checkbox", name: `state_${key}`,
                                      checked: Boolean(st.state?.[key]) }))),
      h("h3", {}, "Library"),
      row("Remove empty folders",
          "Off, a pass clears only the folders it emptied itself. On, it also takes away empty "
          + "folders it finds under the library — including ones you left there — and the check "
          + "names each before anything goes.",
          h("input", { type: "checkbox", name: "remove_empty_folders",
                       checked: Boolean(st.remove_empty_folders) })),
      updateSection(),
      repairSection(),
      takeInSection(),
      missingSection(st.missing),
      h("dl", { class: "info" }, Object.entries(st.info).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
      h("div", { class: "actions" }, h("button", { type: "submit" }, "Save settings"))));
  showTimingFields($("#settingsform"));
  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

// a row is sent only while it is shown, so the selected providers decide what this save is about
const shows = (form, field) => {
  const el = form.querySelector(`[data-timing="${field}"]`);
  return Boolean(el) && !el.hidden;
};

function shownTiming(f) {
  const out = {};
  if (shows(f, "endpoint")) out.timing_endpoint = f.timing_endpoint.value.trim();
  if (shows(f, "device")) out.timing_device = f.timing_device.value;
  // the config keeps three states here, and "false" is a string: send the boolean, or "auto" for none
  if (shows(f, "verify")) {
    out.timing_verify = f.timing_verify.value === "auto" ? "auto" : f.timing_verify.value === "true";
  }
  return out;
}

async function saveSettings(ev) {
  ev.preventDefault();
  const f = ev.target;
  const button = ev.submitter;
  setWorking(button, true);
  try {
    state.settings = await api("/api/settings", {
      library: f.library.value, cookies_from_browser: f.cookies_from_browser.value,
      pot_mode: f.pot_mode.value, pot_idle_minutes: Number(f.pot_idle_minutes.value), concurrency: Number(f.concurrency.value),
      // the two slots are what the page writes now; `timing_provider` stays in the config as the
      // fallback for whatever was there before, and is not touched from here (§9, slice 40)
      timing_align_provider: f.timing_align_provider.value,
      timing_draft_provider: f.timing_draft_provider.value,
      // a field the chosen providers do not use is not shown and not sent, which leaves whatever the
      // config holds for it alone (§9, slice 92)
      ...shownTiming(f),
      // only sent when something was typed: an empty field means "leave the key as it is"
      ...Object.fromEntries((state.settings.timing?.vendors || [])
        .filter((v) => shows(f, `key:${v}`) && f[`timing_${v}_key`]?.value)
        .map((v) => [`timing_${v}_key`, f[`timing_${v}_key`].value.trim()])),
      // what every album should have (§9, slice 100): the state the library is in
      ...Object.fromEntries(STATE_SWITCHES.map(([key]) => [key, f[`state_${key}`].checked])),
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
$("#bin").addEventListener("click", openRecycle);
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

async function playAlbum(albumId, start = 0, { at = null } = {}) {
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
    original_kept: t.original_kept !== false,   // `o=1` answers with the cut file without one
    mb_length: t.mb_length, lyrics_length: t.lyrics_length, file_length: t.file_length,
    savedStart: t.trim_start, savedEnd: t.trim_end,  // what is on disk, to tell editing from listening
  }));
  if (!queue.length) return toast("Nothing downloaded yet in this album", "blocked");
  playIndex(Math.max(0, start), at);
}

function playIndex(i, at = null) {
  if (i < 0 || i >= queue.length) return;
  qi = i;
  const t = queue[i];
  // A cut file no longer contains what trim_start counts from, so the player would skip the
  // head twice (measured: 8s of a trimmed track were unreachable). It plays the untouched
  // original instead and previews the trim itself, which keeps every number on one clock.
  const asked = audioRequest(t);
  // **the queue entry remembers what was loaded** (§9, slice 87): the trim window may only be applied
  // to the original, and after a save this page can still be holding the older answer about the file.
  t.playingOriginal = asked.original;
  const src = `/api/audio?id=${encodeURIComponent(t.album)}&v=${encodeURIComponent(t.video_id)}${asked.query}`;
  // **not a second load of what is already loaded** (R-295): setting `src` again starts a new load and
  // aborts a play that has not settled, which is where "The play() request was interrupted by a new
  // load request" comes from. The same file, asked for twice, is one load.
  const reloading = audio.src !== new URL(src, location.href).href;
  if (reloading) audio.src = src;
  // **where to start is the caller's to say** (§9, slice 88). Nothing here moves the playhead on its
  // own: a lyric line that asks for 1:30 must not be answered with a silent jump to the beginning,
  // which is what an unconditional reset did.
  if (at != null) seekWhenReady(at);
  else if (!reloading && audio.currentTime > 0 && !t.trimmed) audio.currentTime = 0;
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
let lastTick = null;
let justSought = false;
// a seek is a jump even when it lands next to where we were: the element says so itself
audio.addEventListener("seeking", () => { justSought = true; });
audio.addEventListener("timeupdate", () => {
  const t = queue[qi];
  if (t) {  // preview the trim while listening: skip the head, stop at the end
    const unsaved = t.end !== t.savedEnd || t.start !== t.savedStart;
    const said = trimGuard({ current: audio.currentTime, start: t.start, end: t.end,
                             previous: lastTick, jumped: justSought || null, dragging, unsaved,
                             // the file on disk is already cut to these points, so its own clock is
                             // the window and the plan's numbers belong to the original (§9, slice 86)
                             // the window belongs to the original, which is what the player loads for
                             // a cut track; a cut file already carries it (§9, slice 86, slice 87)
                             onTheOriginal: t.playingOriginal !== false });
    justSought = false;
    if (said.seekTo !== undefined) audio.currentTime = said.seekTo;
    if (said.pause) audio.pause();
    // while the end is still being placed, stop on it. Running into the next track would
    // take the unsaved trim with it: the buttons then edit and save the wrong song.
    if (said.next) { if (qi + 1 < queue.length) playIndex(qi + 1); else audio.pause(); }
  }
  lastTick = audio.currentTime;
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
    // scrollIntoView would take the page with it and pull the editor out of view, so the list is
    // scrolled by hand — **measured against the list's own content** (§9, slice 68). `offsetTop` was
    // used here and is measured from the nearest positioned ancestor, which inside a table is the `td`:
    // in the editor's preview the box starts 645 px below it, so every step scrolled 28 lines too far
    // and the line being sung was never in view. A rect difference cannot be fooled by that.
    const box = row.querySelector(".lines");
    const top = active.getBoundingClientRect().top - box.getBoundingClientRect().top
                - box.clientTop + box.scrollTop;
    const want = scrollForActive({ boxHeight: box.clientHeight, boxScroll: box.scrollTop,
                                   contentHeight: box.scrollHeight, lineTop: top,
                                   lineHeight: active.offsetHeight });
    if (want !== null) box.scrollTop = want;
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
  const saved = remembered(THEME);
  return saved && THEMES[saved] ? saved : "auto";
}

let theme = savedTheme();
$("#theme").addEventListener("click", () => {
  const order = ["auto", "dark", "light"];
  theme = order[(order.indexOf(theme) + 1) % order.length];
  try { localStorage.setItem(THEME, theme); } catch { /* private mode: just this session */ }
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
if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/sw.js").catch(() => {});
poll();
