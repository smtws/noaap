# Tests to run through the night

Per feature: the promise (SHOULD), the thing it must never do (SHOULDN'T) and the accident
(WHAT-IF). Each row says how it runs, what it needs, and its **oracle** — what is measured, and
where it matters measured by something other than noaap (`sha1`, `ffprobe`, `strace`, the browser's
own accessibility tree), because a program's own report of what it did is not evidence that it did
it.

**On disposable material only.** A fresh library seeded under `~/Musik/night-2026-10-09/` from
`~/Music/legacy`; my own server on my own port with my own XDG dirs. Never the user's library, the
NAS collection, their config or their service. MusicBrainz, LRCLIB and the Cover Art Archive at
their normal rate; **no Deepgram, no Patreon, no YouTube downloads** beyond the one test of each
that says so.

| id | feature | how | SHOULD | SHOULDN'T | WHAT-IF | oracle | whose |
|---|---|---|---|---|---|---|---|
| **A1** | `take-in --apply` | CLI | bring every album to one state: noaap's names, tags, cover beside and embedded | never write outside the library root, never lose a track, never touch a file it did not list | killed half way (SIGTERM), then run again | before/after `find -printf '%y %s %p'` of the parent of the root — nothing outside changes; track count per album from the audio files themselves (ffprobe), not from the plan; after a kill, the second run finishes and the file count equals the first run's | me |
| **A2** | `take-in` without `--apply` | CLI | say what it would do and write nothing | never create, rename, retag or delete anything | run it twice; run it with the library read-only (chmod a-w) | a byte-for-byte listing (size + mtime_ns + sha1 of every file) identical before and after; read-only run must not raise, and must still print its plan | me |
| **A3** | `adopt --undo` | CLI | put the folder back as it was found | never leave a plan behind, never leave a renamed file | undo twice; undo after a file was edited by hand | the sha1 listing equals the pre-adopt one; a hand-edited file is left as the user left it (noaap must not restore over it) or is named in the output | me |
| **A4** | `merge --undecided` | CLI | list the copies it cannot rank and change nothing | never copy, never delete, never decide silently | the other library vanishes mid-run (unmount) | both trees' sha1 listings unchanged; the vanishing is reported, not a traceback | me |
| **B1** | `update` | CLI | re-check every album against its source and write what it says it writes | never rewrite a field the user typed (provenance `user`), never drop lyrics | no network at all (DNS blackholed); a plan edited under it mid-run | the `--dry-run` change list equals what the real run reports having written; every `provenance: user` field byte-identical afterwards; offline run exits non-zero with a sentence, no traceback, and writes nothing | me |
| **B2** | `repair --dry-run` then `repair --apply` | CLI | the apply does exactly what the check listed | never rename two tracks onto one name, never move a folder onto an existing one | disk full during the apply (loopback fs at 1 MB) | the two lists compared as sets; on a full disk, no half-written file (every audio file still decodes and its sha1 is either the old or the new one, never neither) | me |
| **B3** | `lyrics` | CLI | fetch words only for tracks that have none, write `.lrc` beside the file and the tag | never overwrite an existing `.lrc`, never write a draft as if it were real words | LRCLIB returns 500s; LRCLIB times out | existing `.lrc` sha1 unchanged; `lyrics_words_by` empty for anything it did not draft; on failure the album is reported and the run continues | me |
| **B4** | `delete` and `recycle restore` | CLI | a deleted album is recoverable until the bin is emptied | never delete outside the library, never empty the bin without being told | restore when the original path is occupied again | the restored tree's sha1 listing equals the pre-delete one; an occupied path is refused with a sentence, not overwritten | me |
| **B5** | `prune` | CLI | remove only the tracks the source dropped | never remove a track the user added by hand | the source is unreachable | file-level diff: exactly the dropped ids gone; unreachable source means nothing is removed | me |
| **C1** | `serve` + `/api/state` | API | answer from memory within its stated staleness bound | never walk the library on a read (slice 152) | the library is made slow (every stat +5 ms via a FUSE/overlay or a cold NFS), 200 requests | count of `stat`/`glob` syscalls during the requests via `strace -f -e trace=newfstatat,getdents64` — zero attributable to the request threads; p99 response time under load | me |
| **C2** | `POST /api/cover` | API | set a cover from a file or an http(s) address, as the user's | never accept a non-image, never fetch a `file://` or a private address, never replace it later | a 40 MB upload; a URL that returns HTML; a URL that redirects to `file://` | the written `cover.*` byte-equals what was sent (after squaring); `cover_fetched` empty in the plan; an `update` afterwards leaves it untouched | me |
| **C3** | `POST /api/identify` (check then apply) | API | list what it would change, then write exactly that | never apply without a check, never count its own progress as changes (slice 154) | the album is edited between check and apply (the stale guard) | the two change lists compared as sets; a stale check must refuse with the staleness note | me |
| **C4** | the write header | API | refuse a POST without `X-Noaap` or without a JSON content type | never perform a write for a cross-site-able request | every one of the 24 write actions, called without the header | all 24 answer 403 and the library's sha1 listing is unchanged | me |
| **C5** | `/api/audio` and `/api/cover` path handling | API | serve only files inside the library | never serve a path that climbs out (`..`, absolute, symlink) | `id=../../etc/passwd`, a symlink inside an album pointing at `/etc/passwd` | the response is 400/404 and never the file's bytes | me |
| **D1** | the library grid | browser | show every album, filter as you type | never block the input (the letter paints first, R-527) | typing fast with the panel open and the grid scrolled | worst keystroke-to-paint and longest task via CDP, as in I-391 | peer |
| **D2** | the album panel | browser | save what was typed, and only that | never discard an edit on a poll, never show a field as the user's that is not | edit, then let a background pass write the same album | the saved plan's `provenance` for each edited field is `user`; the other fields untouched | peer |
| **D3** | the release dialog (slice 157) | browser | list the candidates, preselect the nearest, pin on choosing and run the look | never pin on Cancel, never pin two | Escape; Enter with nothing selected; choosing twice quickly | the plan's `mbid` and its provenance after each path | peer |
| **D4** | the lyrics editor | browser | save the words and the stamps as typed | never save a draft silently as the user's words | paste 2,000 lines; save with the stamps shifted | the `.lrc` beside the track byte-for-byte against what was in the editor | peer |
| **D5** | keyboard and screen reader | browser | every control reachable by Tab and operable by Enter/Space | never trap focus outside a dialog, never leave a control unlabelled | Tab through the whole page; the release dialog's radio group with arrows | the accessibility tree via CDP: every focusable node has a name and a role | peer |
| **E1** | `watch` + `watch-service` | CLI | take in what arrives, once settled | never take in a file still being written, never leave the arrival behind | copy a 100 MB file in slowly; drop a non-audio file; drop a folder with a `..` name | the taken-in file's sha1 equals the source's; a partial file is waited for, not imported | me |
| **E2** | `timing-serve` | CLI | align for another machine over HTTP | never accept an audio path outside what the request sends | a request naming `/etc/passwd`; a 0-byte body | the response is an error and nothing on disk is read outside the temp dir (strace) | me |
| **E3** | `config` | CLI | show and set settings, and say what it wrote | never write the user's real config, never print a secret | set an unknown key; set a key to an invalid type | my config file is the only one modified (sha1 of the user's unchanged); no key value in stdout for `*_key` settings | me |
| **E4** | `service install/uninstall` | CLI | write a unit that names this venv and nothing else | never touch the user's units | install twice; uninstall when not installed | `~/.config/systemd/user` diff limited to my own unit names | me |
| **F1** | a draft (local provider) | CLI/API | produce words for a track that has none, labelled a guess | never loop (slice 160), never save itself, never send audio anywhere | a 15-minute mostly instrumental track; a silent file | no line repeated more than 12×; no outbound connection during the run (strace on connect) | me |
| **F2** | the provider boundary | CLI | a private source is never looked up anywhere | never send a title, a duration or audio for an album marked private | a Patreon-sourced album through `update`, `lyrics` and a draft | `strace -f -e trace=connect` for the whole run: no connection while that album is processed | me |
| **G1** | `plan` then `download` | CLI | plan an album, edit the plan, download from it | never download what the edited plan does not ask for | `--verify` on a plan whose files moved | the downloaded set equals the plan's ids exactly (filenames vs plan, ffprobe for playability) | me |
| **G2** | `fetch` | CLI | plan and fetch one small source, `--dry-run` first | never fetch more than the source lists; never write outside the album folder | a URL that 404s; `--dump-collection` to a path that cannot be written | `--dry-run` list equals what the real fetch produced; one YouTube fetch only, stated | me |
| **G3** | `search` | CLI | list an artist's albums and pick none | never fetch anything without a pick | a name with no results; `--all` with `--dry-run` | nothing written anywhere (sha1 listing); the result count reported | me |
| **G4** | `migrate` | CLI | run it where there is nothing of ytalbum's to take | never claim to have migrated what it did not find | `--apply` with no ytalbum state present | exit code and output say 'nothing to take'; no file created | me |
| **G5** | `app install/uninstall/status` | CLI | write a desktop entry of my own naming my port | never touch the user's desktop entry or browser profile | install twice; `--remove-profile` when there is no profile | `~/.local/share/applications` diff limited to my own entry name | me |
| **G6** | the read endpoints | API | each of the 13 answers with its documented shape | never 500 on a missing id, never leak a path outside the library | unknown album id, unknown job id, `id=` empty, a 10 kB id | status codes and JSON keys per endpoint; no absolute path outside the library in any body | me |
| **G7** | the lyrics write actions | API | save, take plain, publish, check, per track | never overwrite words marked the user's without being told; never publish a draft | save with the album deleted under it; publish with no network | the `.lrc` and the plan's `lyrics_words_by` after each; LRCLIB not called for a draft | me |
| **G8** | the library write actions | API | fetch, open, update, repair, take_in, prune, delete, restore, empty | never run two writers at once; never write outside the library | two write actions submitted together; cancel mid-run | the job lane refuses the second with a sentence; sha1 listing of the parent unchanged | me |
| **G9** | the library-shape settings | API | set each, run a pass, read the effect | never apply a setting the user did not save; never change a plan's provenance to `user` | an invalid value for each (type and range) | for each setting: one observable on disk (a cover file, an embedded picture, a comment frame, a folder removed) measured independently of noaap's own report | me |
| **G10** | the timing settings | API | set each, read it back, and see it in a job's parameters | never send audio to a provider the settings did not name | an unknown provider name; a device the machine does not have | the job's `parameters` echo the setting; `strace` on connect for 'nothing left the machine' | me |
| **G11** | `align` and `draft` with the local provider | API | align given words, draft where none | never save either by itself; never run on a private album | align words that are not in the audio; draft a silent file | the plan is unchanged until a save; the draft is labelled a guess | me |

**36 tests: 20 mine (CLI/API), 5 the reviewer's (browser).**
Needs per row is in the row's own text; D1–D5 all need my server and are listed for the reviewer.

## Results

Run on the night of 2026-10-08/09 against disposable copies under `~/Musik/night-2026-10-09/`
(seed: 5 albums, 19 mp3 from `~/Music/legacy`; seed-fetched: one stated YouTube fetch, 1 opus).
*Ran* needs positive evidence the action happened (R-551) — a line the command prints about this
very action, or the job's own finished result. *Passed* is a run with no finding from the oracle
(nothing written outside the library, every audio file still decodes, no file vanished unasked).

| phase | rows | ran | passed | findings |
|---|---|---|---|---|
| pairwise (19 factors, 1,116 pairs) | 28 | 21 — the other 7: fetch/align/draft ×2 not in the matrix by design, 1 delete_track on opus (harness counted mp3 only, fixed) | 21 | 0 |
| 1-wise: 10 deciding settings × both values × 8 writers × 2 sources | 320 | 320 | 320 | 0 |
| 3-wise covering array over the 10 deciding booleans (16) × 8 writers × 2 sources | 256 | 256 | 256 | 0 |
| full: 2^10 × 8 writers × 2 sources, 3 workers | 16,384 | **incomplete**: ≥ 12.3k counted at 06:43, host down 08:07, raw results lost (tmpfs) | all counted | 0 row findings; **I-407**: the server keeps every finished job (`Jobs._jobs`, `web.py`), RSS 165 → 339 MB over ~4,100 jobs per worker |
| single: fetch (one stated YouTube URL) | 1 | 1 — "1 plan, 1 opus" | 1 | 0 |
| single: local draft (large-v3, 32 s opus) | 1 | 1 — job done, "drafted 9 lines", 10.2 s | 1 | 0 |
| single: local align (wav2vec2 + htdemucs, checked by large-v3) | 1 | 1 — job done, "placed 9/9 lines", 22.1 s; library unchanged (writes nothing) | 1 | 0 |

Findings outside the matrix, from rows run by hand the same night: **I-404** — `delete --track`
refuses the id the plan shows (stored ids are relative, loaded ids absolute); the reviewer's
**D2** (an unsaved edit is lost on refresh) and `/api/cover` answering 404 instead of 204. All
four are P116.

Settings without a visible effect in the 1-wise rows (drop_comments, tidy_adopted_tags,
drop_wm_frames) act only under `retag_adopted = true`, where the retag changes every file's hash
anyway; probed directly, `drop_comments = true` left 0 of 18 files with a comment, `false` 18 of 18.

Throughput (I-403): 1 worker 1,039 rows/h; 2 → 1,872; 3 → 2,783; 4 → ≥ 3,071; ~0.5 GB per worker,
swap unchanged, load ≤ 16.3 on 22 cores.

The single draft/align rows were rerun on 2026-10-09 after the restarts, from
`~/Musik/night-2026-10-09/harness/` (on disk, R-559); the matrix harness itself was lost with the
scratchpad.

## Coverage

Every one of the **221 features** the catalog lists is either covered by a test above or named here with the reason it is not. **205 covered**, **16 excused**, **0 unexplained** — checked by a script that fails loudly on a feature that is neither (lost with the scratchpad, see Results).

Grouping is deliberate where one run proves several: `take-in --apply` exercises fifteen of its own flags in one pass, and C4 calls all 24 write actions without the write header.

### Not covered tonight, and why

- `setting:cookies_file` — needs the user's own cookie jar; `config` writing it is covered by E3
- `setting:cookies_from_browser` — needs the user's own browser profile
- `setting:js_runtime` — covered by G11 only as 'accepted'; its effect needs the PO-token path
- `setting:js_runtime_path` — same
- `setting:patreon_cookies_file` — needs the user's own Patreon session
- `setting:patreon_cookies_from_browser` — needs the user's own browser profile
- `setting:pot_idle` — same
- `setting:pot_mode` — the PO-token helper needs a node runtime and a live YouTube challenge
- `setting:pot_port` — same
- `setting:pot_provider_home` — same
- `setting:soundcloud_cookies_file` — needs an account
- `setting:soundcloud_cookies_from_browser` — needs an account
- `setting:timing_deepgram_key` — a secret; E3 proves no `*_key` value is ever printed
- `setting:timing_elevenlabs_key` — a secret; same as above
- `setting:watches` — the value is covered by E1; editing it in the UI is the reviewer's D-set
- `source:soundcloud` — needs an account cookie the user has not set
- `timing:deepgram` — R-543 forbids Deepgram calls tonight; its request shape is covered by the stored replies in P112
- `timing:elevenlabs` — needs a paid account and sends audio away; the boundary is covered by F2

## Night 2 — the page, feature by feature (P120, 2026-10-11)

Run against v1.51.1 from `~/.local/noaap-release` on port 8911, with a scratch XDG config. The library
was a copy of 20 albums from the share under `~/Musik/night-2026-10-11/lib`. Second copies were used
for the library switch (`lib2`) and the take-ins (`intake-*`). The browser was my own headless Chrome
over CDP with a throwaway profile, and every action was a real input event (`Input.dispatchMouseEvent`
/ `dispatchKeyEvent` / `insertText`, the file chooser via `Page.setInterceptFileChooserDialog`), never
`element.click()`. Harness, `results.jsonl` (every attempt, 129 records) and the jpegs are in
`~/Musik/night-2026-10-11/harness/` (R-559). The server was started with request lines at debug level,
so each row carries the server's own log lines. Screenshots exist only where the view shows My Dark
Lullabies Vol. 20, or the settings, bin or jobs (R-574 rule 4). Every other row proves by DOM
assertion + log line.

**107 rows (the last record per row in `results.jsonl`): 95 pass, 11 finding rows — nine findings, I-426 – I-433 — and 1 not reachable.**
- **Not reachable:** draft / align / ⚖ check need a model load. `noaap.service` stayed active the
  whole night (pid 1505534, RSS 859 MB, MemAvailable 18.3–19.0 GB). Under R-556 / R-574 rule 3 nothing
  was loaded; the service was never stopped by me.
- **Excused by ruling:**
  - `publish_lyrics` was walked to its confirm and cancelled: 0 POSTs (R-574 rule 1).
  - Add to MusicBrainz was walked to the seed form, and the form's submit was intercepted, so nothing
    reached musicbrainz.org.
- **YouTube:** the rows that needed it (search, preview, fetch, complete, use another video, take a
  copy, audio choice) all ran without cookies and without a bot check.

**Corrections to this table's own first pass:**
- Two trim rows first passed on the plan's values alone. The files themselves show the trim was
  never undone (I-432), so both are findings.
- Four harness faults were found and the rows rerun: a thumbnail loaded before the row's log mark,
  a seek before the new track had a duration, a click into a collapsed `<details>`, and the
  `[[watch]]` key. Only the last record per row is in the table.

The 16 excused rows of night 1, re-examined for the page:
- **Now walked as "set from the page, echoed, written to the config, set back"** (12 rows):
  `cookies_from_browser`, `cookies_file` (both via the sources dialog for Patreon and SoundCloud,
  with a dummy path), `pot_mode`, `pot_idle`, `timing_elevenlabs_key` (set → "(set)", never in
  `/api/state` or the page, removed by a space) and `watches` (add / edit / remove; a folder inside
  the library is refused in the dialog).
- **Still excused, no control on the page:** `pot_port`, `pot_provider_home`, `patreon_post_cap`.
- **Still excused, using them needs an account or money:** the SoundCloud/Patreon sessions, and the
  `elevenlabs` / `deepgram` calls.
- **`arrived`:** only the watcher posts it.

| row | clickpath | observed | proof | verdict |
|---|---|---|---|---|
| C1 /api/state + /api/tracks | open the page | 17 cards, /api/state has 17 albums | 1 log line(s) | pass |
| libfilter | click #libfilter, type 'lullab' | ['My Dark Lullabies — Vol. 20 - Glasses Empty']; libpath '1 of 17 albums' | jpeg libfilter-mdl.jpg | pass |
| play-matches + p-play | filter 'lullab', click '▶ Play n tracks', then ⏸ | button '▶ Play 12 tracks'; playing 'Der Teufel hat den Schnaps gemacht . . .'; paused after #p-play: True | 2 log line(s), jpeg player-mdl.jpg | pass |
| libfilter Escape | Escape in #libfilter | 17 cards after Escape | DOM | pass |
| length-filter | click '⏱ n with odd lengths', click again | '⏱ 1 with odd lengths' → ['Feuerschwanz — Sex Is Muss']; again → 17 | DOM | pass |
| needs-you-filter | click 'n need you', click again | '8 need you' → ['Das Elfte Gebot', 'Fasan Alarm', 'Mosqueteros', 'Sex Is Muss', 'Vol. 20 - Glasses Empty'] | 1 log line(s) | pass |
| theme | click ◐ ×3, ◐ once more, reload | cycle ['dark/Theme: dark (click to change)', 'light/Theme: light (click to change)', 'auto/Theme: automatic (click to change)']; … | 23 log line(s) | pass |
| artist view + artist-all | click artist 'Feuerschwanz' on a card, then 'Show all artists' | title 'Feuerschwanz', 2 cards, actions shown True; 'Show all' → 17 | DOM | pass |
| keyboard: / Enter Escape | /, type, Enter, Enter, Escape | '/' focus libfilter; Enter → card ''; Enter opened None; Escape closed True | DOM | **finding** I-426 |
| to-top | wheel down 1500 px, click ↑ | ↑ shown after wheel True; scrollY 0 after click True | DOM | pass |
| rail (initials) | click 'W' in the rail | rail 'ACDFGIJKLMNWZ'; W → focus 'Warkings — Armageddon' | DOM | pass |
| grid key P / card ▶ | focus the MDL card, press P | P on the MDL card → playing 'Schandmaul · Vol. 20 - Glasses Empty / Der Teufel hat den Schnaps gemacht . . .' | 4 log line(s) | pass |
| poll cadence (idle) | leave the page idle 34 s | idle /api/state gaps [8.01, 8.01, 8.01] | 4 log line(s) | pass |
| C2 /api/thumb (card cover) | reload the page | 14 card thumbnails loaded after reload | 14 log line(s) | pass |
| p-next / p-prev / p-pos | MDL card + P, ⏭, ⏮, click the middle of the position bar | next 'Der Teufel hat den Schnaps gemacht . . .'→'Sternhagelvoll', prev → 'Der Teufel hat den Schnaps gemacht . . .', click at 50%… | 8 log line(s) | pass |
| keyboard: Enter in the filter | type a name, Enter after 0.05/0.3/1.5 s (×3 names) | Enter ≤0.3 s after typing: focus on a card 0/6; after 1.5 s: all on a card | 9 log line(s) | **finding** I-426 |
| keyboard: Enter on a card / Escape | focus a card, Enter, Escape | Enter opened True; Escape closed True; focus back on 'Nightwish — Ever Dream' | 1 log line(s) | pass |
| player layout: position bar width | play MDL Vol. 20 tracks 1–5 at 1400 px | #p-pos widths [44, 109, 140, 120, 127] px in a 1385 px player | 44 log line(s), jpeg player-bar-mdl.jpg | **finding** I-427 |
| album open | click the MDL Vol. 20 tile | 'My Dark Lullabies — Vol. 20 - Glasses Empty (2025)', 12 rows, cover 720px | 2 log line(s), jpeg album-mdl.jpg | pass |
| Play from here (row ▶) | MDL: ▶ on the 'Blau wie das Meer' row | player 'Blau wie das Meer Version 2017', row marked playing 'Blau wie das Meer Version 2017' | 4 log line(s) | pass |
| Play album (album cover) | MDL: click the album cover | cover click → 'Der Teufel hat den Schnaps gemacht . . .' (track 1 'Der Teufel hat den Schnaps gemacht . . .') | 4 log line(s) | pass |
| album head artist link | MDL: click the artist name in the heading | album closed True, artist view 'My Dark Lullabies' | DOM | pass |
| album Close | open Ever Dream, click Close | closed True | 2 log line(s) | pass |
| edit album field (year) + Save | Zeraphine: Year → 1999, 'Save changes' | year 2004 → 1999, provenance user; job done | 1 log line(s) | pass |
| reset field (album ↺) | Zeraphine: click 'you ↺' beside Year | year now 2004 (derived 2004), provenance None | 1 log line(s) | pass |
| unsaved field kept across refresh | Zeraphine: type into Album (unsaved), tick + untick an exception | after an except job + panel refresh the album field reads 'UNSAVED TYPING'; exceptions {'names': True} → {'names': True} | 1 log line(s) | pass |
| edit track title + Save | Ever Dream: track 2 title + ' (P120)', Save changes | 'Phantom of the Opera' → 'Phantom of the Opera (P120)' (user), file Nightwish - Ever Dream - 02 - Phantom of the Opera.mp3 | 1 log line(s) | pass |
| reset field (track ↺) | Ever Dream: 'you ↺' on track 2 | title back to 'Phantom of the Opera' (None) | 1 log line(s) | pass |
| track order: drag + Save | Ever Dream: drag ⋮⋮ of row 1 below row 3, Save changes | ['Ever Dream', 'Phantom of the Opera', 'The Wayfarer'] → dom ['Phantom of the Opera', 'The Wayfarer', 'Ever Dream'] → saved ['Pha… | 1 log line(s) | pass |
| track order ↺ (reset) | Ever Dream: 'Track order is yours you ↺' | order provenance now None | 1 log line(s) | pass |
| track order: Alt+↓ | Ever Dream: focus row 1's number, Alt+↓ | ['Phantom of the Opera', 'The Wayfarer', 'Ever Dream'] → ['The Wayfarer', 'Phantom of the Opera', 'Ever Dream'], numbers ['1', '2… | 3 log line(s) | pass |
| trim handle drag | Ever Dream track 1: 'start here', drag the start handle 6 px right, reset | start handle x 528 → 534 after a 6 px drag; note 'now 0:27.9 · keeping 3:48.2 · LRCLIB 4:11 · −22.8s' | DOM | pass |
| exceptions (except on/off) | Zeraphine: open the exceptions box, tick 'Write no tags…', reopen, untick | tick 2nd → {'tags': True} ('This album is excepted: Write no tags into this album's files'); the box was COLLAPSED after the job;… | 2 log line(s) | **finding** I-428 |
| cover: Choose a cover… (file) | Grace Jones: 'Choose a cover…' → file chooser → a jpg | /api/cover 204 → 200; cover.jpg beside True; job done | 6 log line(s) | pass |
| Add to MusicBrainz (mbseed) | Grace Jones: 'Add to MusicBrainz', OK | confirm 'MusicBrainz's release editor is about to open, with 1 track already filled in:'; form to https://musicbrainz.org/release… | 1 log line(s) | pass |
| cover: …or paste a link (url) | DOMINUM Cannibal Corpses: '…or paste a link', a coverartarchive URL | /api/cover 204 → 200; job done | 1 log line(s) | pass |
| identify (check, apply) | J.B.O No Business: 'Identify with MusicBrainz', then 'Apply…' if offered | check said: 'Identify with MusicBrainz MusicBrainz has no release that matches this album.' (nothing to apply) | 1 log line(s) | pass |
| which release? (offers dialog) | Dunkler Ort: 'Which release is this? (3)' → 'This is the release' | 'Which release is this? (3)': 3 radios, preselected afa6d90e-6cc9-4ac6-8343-eacc9c1322a2; plan mbid afa6d90e-6cc9-4ac6-8343-eacc9… | 2 log line(s) | pass |
| pin offer ('this one') | Fate Of Norns: 'this one' on the first offered release, OK | 'this one' on 60b46304-7783-3e40-acab-41ef7ad4ed3d → plan mbid 60b46304-7783-3e40-acab-41ef7ad4ed3d (user); confirm True | 1 log line(s) | pass |
| Fetch lyrics (album) | Magie und Melodie: 'Fetch lyrics' | .lrc files 0 → 0; job done; 'lyrics: 5 none' | 1 log line(s) | pass |
| Re-check source (fetch) | Ever Dream (folder album): 'Re-check source' | POST /api/fetch {"urls":["/home/tordt/Musik/night-2026-10-11/lib/Nightwish/Ever Dream"]} → 400, toast ['no URLs']; no job (24 → 2… | 1 log line(s) | **finding** I-429 |
| candidates: not this one | same track: ⇄, 'not this one', OK | 'Mission Eskalation': ['Das Elfte Gebot/Feuerschwanz - Das Elfte Gebot - 06 - Mission Eskalation.opus default, not ranked · opus … | 1 log line(s) | pass |
| candidates: take this one | Das Elfte Gebot: ⇄ on the waiting track 'Amen & Attack', 'take this one', OK | job 23 'Save Feuerschwanz — Das Elfte Gebot' done: Amen & Attack: audio now from Zdidvv2ZYfs (was /home/tordt/Musik/night-2026-10… | 1 log line(s) | pass |
| complete (check + fetch ticked) | Heilig (pinned): 'Complete from the release…', keep one ticked, 'Fetch the ticked tracks' | 2 slots, 1 fetchable; ticked ['1-06']; tracks 14 → 15; '15/15 tracks done' | 2 log line(s) | pass |
| use another video (source) | Fasan Alarm track 1: ⇄, paste a playlist URL → refused; paste track 2's id, 'Use this vid… | a playlist URL → note 'That is not a single video. Paste the watch link of one video, or its '; then wZytBzEi3YI → source_overrid… | 1 log line(s) | pass |
| audio source ↺ (back) | Fasan Alarm track 1: ⇄, 'you ↺' on the audio source, OK | ↺ on the audio source → override None; confirm '“Kupfergold – Fasan Alarm” Back to the source's own 6vGl2re' | 1 log line(s) | pass |
| trim per channel (⇉) | Fasan Alarm: start '0:01', ⇉, OK; then empty, ⇉, OK | channel 'Kupfergold': 10/10 tracks cut at 1 s; removed again → 0 left; confirm 'Apply to every track from “Kupfergold” in the lib… | 2 log line(s) | pass |
| audio choice (no audio — choose) | Pray: 'no audio — choose', OK | audio_choice combined, state done, ext m4a; '10/10 tracks done' | 1 log line(s) | pass |
| length chip → MusicBrainz (confirm only) | Sex Is Muss: click the red length chip, Cancel | confirm 'MusicBrainz has a different length for “Ketzerei”: their recording: 3:35.5 your f', cancelled → 1 tab(s) | 8 log line(s) | pass |
| prune (tracks no longer in the playlist) | Mosqueteros: 'Remove 1 track no longer in the playlist', OK | 'Mosqueteros' pruned: in plan False, file there False; bin entries 1 | 1 log line(s) | pass |
| delete track (✕) | Zeraphine: ✕ on the last track, OK | 'Sometimes' deleted: left ["I'm Numb (Version)", "New Year's Day"] | 1 log line(s) | pass |
| delete album | Grace Jones: 'Delete album', OK | view closed True, tile there False, plan there False | 1 log line(s) | pass |
| lyrics editor Cancel | MDL: Edit, type a line, Cancel | editor closed True; .lrc untouched True | 3 log line(s) | pass |
| lyrics line click plays | Armageddon: ♪ on the timed track, click line 6 | 'Armageddon': 39 clickable lines; line 6 (at 36.9) → playing at 36.9s | 4 log line(s) | pass |
| stamp tools (Ctrl+Enter, nudge, ▶, shift all) | Armageddon timed track: Edit, play, caret on line 3, Ctrl+Enter, +0.1, ▶, shift all, Canc… | line 3: '[00:21.42] Hopeless as the hammer falls' → tap '[00:36.9] Hopeless as the hammer falls' → +0.1 '[00:37.0] Hopeless as th… | 3 log line(s) | pass |
| write words + Save | Magie und Melodie 'Es beginnt': ♪, Write, type 2 timed lines, Save | .lrc now '[00:01.00] P120 first line\n[00:05.00] P120 second line\n'; badges 'yours' | 1 log line(s) | pass |
| publish to lrclib (to the confirm only) | Magie und Melodie: '↑ publish to lrclib', Cancel | confirm 'These words are about to be published to LRCLIB, for everyone. Die Legende von Nord — Es beginnt album: Magie u' → Cance… | DOM | pass |
| lyrics Delete | Magie und Melodie: Edit, Delete | .lrc after Delete: None | 1 log line(s) | pass |
| Look up again (lyrics_track) | MDL: ♪ on a track with lrclib words, 'Look up again' | 'Sternhagelvoll': job done: 'Sternhagelvoll: synced lyrics (lrclib #23128780)' | 1 log line(s), jpeg lyrics-lookup-mdl.jpg | pass |
| take the words as plain text | a '♪ ?' track whose near entry is another cut: 'take the words as plain text', OK | Glasses Empty 'Blau wie das Meer Version 2017': confirm True; .lrc 1839 chars, untimed True | 1 log line(s) | pass |
| lyrics panel (read) | MDL: ♪ on 'Sternhagelvoll' | 'Sternhagelvoll': 43 lines, 43 timed; actions ['Edit', 'Look up again', 'Not these words'] | 1 log line(s), jpeg lyrics-mdl.jpg | pass |
| Not these words (reject) | MDL 'Sternhagelvoll': 'Not these words' | lrclib #23128780 → rejected [23128780], now #17386784 | 1 log line(s) | pass |
| search by name (open) | type 'Warkings' in the top field, Enter | 'Found': groups ['Albums5select all', 'Singles, EPs and other releases19select all', 'Other playlists found by search10select all… | 26 log line(s) | pass |
| Download selected (fetch picks) | tick the smallest album not in the library, 'Download selected' | picked 'Live is Life' (1 tracks); 'nothing selected'; job Fetch youtube.com/playlist?list=OLAK5uy_lKh2PWd1OcvTGdmWKoziSCalEG done… | 1 log line(s) | pass |
| URL preview (open with a URL) | paste MDL Vol. 20's playlist URL, Enter | 'My Dark Lullabies — Vol. 20 - Glasses Empty (2025)', 'already in the library', 12 rows, buttons ['Close', 'Download what is miss… | 1 log line(s), jpeg preview-mdl.jpg | pass |
| Download what is missing (preview) | in the preview: 'Download what is missing' | job 'Update My Dark Lullabies — Vol. 20 - Glasses Empty' done: '12/12 tracks done' | 1 log line(s) | pass |
| Shift+Enter (fetch without preview) | paste the URL, Shift+Enter | no preview shown (True); job 'Update My Dark Lullabies — Vol. 20 - Glasses Empty' kind fetch done | 1 log line(s) | pass |
| artist: Check for new albums | artist view Kupfergold, 'Check for new albums' | 'Found': 18 picks, 1 in library | 1 log line(s) | pass |
| artist: Update these albums (+Shift deep) | Kupfergold view: 'Update these albums', then Shift+click | click → 'Update Kupfergold' done; Shift+click → 'Update Kupfergold (full)' done | 2 log line(s) | pass |
| jobs panel: a held click on 'log' | deep Feuerschwanz update ×10, press 'log' for 120 ms at staggered moments | 'log' pressed for 120 ms on a running job: opened 8/10 ([True, True, True, False, True, True, True, True, False, True]) | 216 log line(s) | **finding** I-430 |
| jobs panel: log + Cancel | deep Feuerschwanz update, 'log' on its row, 'Cancel' | log pane 79 chars ('reading Feuerschwanz/Das Elfte Gebot …'); 'Stopping…' True; job 'cancelled': 'cancelled — everything finished… | 6 log line(s), jpeg jobs.jpg | pass |
| recycle bin (open) | header 'Recycle bin' | header button 'Recycle bin (4)'; 4 rows = /api/recycle 4: ['Grace Jones — Slave to the Rythm Unbekannt · deleted · 8.0 MB Put it … | 2 log line(s), jpeg bin.jpg | pass |
| recycle bin: Put it back (restore) | bin: 'Put it back' on Zeraphine 'Sometimes' | plan ["I'm Numb (Version)", "New Year's Day", 'Sometimes']; files ["Zeraphine - New Year's Day CDM - 01 - I'm Numb (Version).mp3"… | 1 log line(s) | pass |
| recycle bin: Empty the bin | bin: 'Empty the bin', OK | 3 → 0 entries; confirm 'Empty the recycle bin? 3 thing(s), 11.1 MB. This is the one place whe'; panel 'Empty. When noaap deletes … | 1 log line(s) | pass |
| recycle bin: Close | bin: 'Close' | panel hidden True; header button hidden now that the bin is empty: False | DOM | pass |
| settings: open (⚙) | header ⚙ | 18 visible fields ['library', 'cookies_from_browser', 'pot_mode', 'pot_idle_minutes', 'concurrency', 'timing_align_provider', 'ti… | 1 log line(s), jpeg settings.jpg | pass |
| settings: concurrency, pot_mode, pot_idle | ⚙: concurrency 2, token helper 'script', 7 min, Save; then back | saved → (concurrency, pot_mode, idle min, cfg concurrency, cfg pot_mode, cfg pot_idle s) = (2, 'script', 7, 2, 'script', 420); se… | 2 log line(s) | pass |
| settings: YouTube login (cookies_from_browser) | ⚙: YouTube login 'Firefox', Save; then 'none', Save | firefox → echoed firefox, cfg firefox; none → None, cfg has key False | 2 log line(s) | pass |
| settings: the 10 state switches | ⚙: click all 10 'What every album should have' boxes, Save; again, Save | 10 switches ['musicbrainz', 'lyrics', 'cover_beside', 'cover_embedded', 'lyrics_embedded', 'rename_adopted', 'retag_adopted', 'ti… | 2 log line(s) | pass |
| settings: Remove empty folders | ⚙: tick 'Remove empty folders', Save, reopen | box False → True, Save: the POST body lacks remove_empty_folders; /api/settings says False, config None; reopened the box shows F… | 1 log line(s) | **finding** I-431 (6) |
| settings: timing providers + key | ⚙: align provider none/local/http/elevenlabs, type a fake key, Save; reopen, ' ', none, S… | rows per provider {'none': [], 'local': ['device', 'verify'], 'http': ['endpoint'], 'elevenlabs': ['key:elevenlabs']}; key set → … | 2 log line(s) | **finding** I-431 (7) |
| settings: library folder (switch) | ⚙: Library folder → …/night-2026-10-11/lib2, Save | grid ['Nick Cave & Kylie Minogue — unknown'], libpath '/home/tordt/Musik/night-2026-10-11/lib2', cfg /home/tordt/Musik/night-2026… | 1 log line(s) | pass |
| settings: library folder (back) | ⚙: Library folder → …/night-2026-10-11/lib, Save | 19 albums back, cfg /home/tordt/Musik/night-2026-10-11/lib | 1 log line(s) | pass |
| settings: close (Escape, ⚙) | ⚙ open, Escape; ⚙, ⚙ | Escape closes True; ⚙ opens True; ⚙ again closes True | DOM | pass |
| settings: key removed by a space | ⚙: provider elevenlabs, key field ' ', Save; provider none, Save | key set True (placeholder '•••••••• (set)'); provider elevenlabs + ' ' + Save → key False; then none → align none, cfg key presen… | 2 log line(s) | pass |
| settings: sources (Patreon, SoundCloud) | ⚙ Sources: pick Patreon, 'Add a source', cookies file + boxes, Save; 'Remove'; the same f… | [('patreon', ['patreon_cookies_from_browser', 'patreon_cookies_file', 'patreon_audio_from_video', 'patreon_captions'], True, None… | 4 log line(s) | pass |
| settings: watched folders | ⚙ Sources: 'a watched folder', Add; a folder inside the library (refused), then …/intake,… | inside the library → note 'p120 drop: an intake folder may not hold the library, and the library may not hold it — a library that… | 3 log line(s) | pass |
| update: Run | ⚙ 'Check the sources for new tracks': Run | 'Update the library' done: '7 of 19 albums were unchanged' | 1 log line(s) | pass |
| update: Read every album in full | ⚙: 'Read every album in full' | 'Update the library (full)' done; 'unchanged — nothing to do' lines 0; '5/5 tracks done' | 1 log line(s) | pass |
| repair: Check | ⚙ 'Repair the library': Check | listing 3 lines: ' 01 would be put back untrimmed 02 would be put back untrimmed 1 album(s) would be tidied up'; Apply enabled Tr… | 1 log line(s) | pass |
| repair: Apply | ⚙ repair: Apply, OK; Check again | confirm 'Apply this repair? 01 would be put back untrimmed 02 wo'; apply done: '1 album(s) tidied up'; check again: ' 01 would be… | 2 log line(s) | pass |
| take in: adopt (Check, Apply) | ⚙: …/intake-adopt, adopt, Check, Apply, OK | check '1 album folder(s) under intake-adopt 1 album(s), 3 track(s) one plan per album is written, and nothing else: no file is re… | 2 log line(s) | pass |
| take in: take in (options, Check, Apply) | ⚙: …/intake-take, 'take in', keep originals in …/originals, LRCLIB off, Check, Apply, OK | check '1 album folder(s) under /home/tordt/Musik/night-2026-10-11/intake-take their own names (from you); musicbrainz, cover_besi… | 2 log line(s) | pass |
| disc + trim fields + Save | Ever Dream: trim '0:05' on a track, Save; then the field emptied, Save | job 9 'trimmed: 01 … Ever Dream'; job 10 (trim removed) 'trim failed: 03 Nightwish - Ever Dream' — plan trim_start None but the m… | 17 log line(s) | **finding** I-432 |
| player trim (start/end/from start/save/reset) | Ever Dream: 'start here' 12.5 s, 'end here' 40.4 s, 'save trim', 'reset', 'save trim' | job 11 'trimmed: 01 … Phantom of the Opera'; job 12 'trim failed: 01 Nightwish - Phantom of the Opera' — plan trims None, trimmed… | 17 log line(s) | **finding** I-432 |
| settings: tracks not where their plan says | rename one Ever Dream file on disk, ⚙ repair Check, reopen ⚙ | after moving 'Nightwish - Ever Dream - 03 - The Wayfarer.mp3' aside and a repair Check: '1 track(s) are not where their plan says… | 8 log line(s), jpeg settings-missing.jpg | pass |
| take in: merge (Check, Apply) | ⚙ 'Take in a folder': …/intake-merge2 (adopted), merge, Check, Apply, OK | check: 3 tracks here, 146 there, 0 pairs, 3 the library does not have → 'nothing would be added'; apply: '0 replaced, 0 filled' —… | 2 log line(s) | pass |
| take in: merge — new albums | same run | the check says '3 track(s) in 1 album(s) are not in this library at all — `--new` fetches those albums'; the page's take-in secti… | 2 log line(s) | **finding** I-433 |
| server gone mid-session, and back | stop the test server by its port's pid; wait; start it again | SIGTERM to pid 1541542 → notice after 7.8s: 'The noaap server is not reachable. Start it with noaap serve (or install it: noaap s… | 3 log line(s), jpeg offline.jpg | pass |
| poll cadence (busy) | ⚙ 'Read every album in full', 15 s of the server log, then Cancel | 20 gaps over 15 s of a deep update: median 0.71, min 0.71, max 0.71 | 82 log line(s) | pass |
| draft / align / ⚖ check (model rows) | conditions only | noaap.service is active (pid 1505534, RSS 859 MB); MemAvailable 18291 MB — R-556/R-574 rule 3: no model load while it runs | DOM | not reachable |
| identify check on In Extremo (P121 on the page) | Kompass zur Sonne: 'Identify with MusicBrainz' | check log 7 lines, Saigon seat moves []; panel 'Identify with MusicBrainz MusicBrainz agrees with what this album already says — … | 1 log line(s) | pass |
| identify check (with changes) | Oh Luna mein: 'Identify with MusicBrainz' | panel 'Apply what MusicBrainz says Apply writes exactly what is listed: 3 change(s).' | 1 log line(s) | pass |
| identify apply | Oh Luna mein: 'Apply what MusicBrainz says', OK | confirm True: 'Apply what MusicBrainz says about this album? 13 title: 20 Schritte Freiheit (Teil 2) → 20 Schritte Freiheit, Teil… | 1 log line(s) | pass |
| grid arrow keys | focus the first card, →, ↓, End, Home | → 'Apocalyptica — Bittersweet', ↓ index 7 (6 per row), End 19/19, Home 0 | 2 log line(s) | pass |
| library filter by song title | type 'Sternhagelvoll', '▶ Play 1 track' | matchline '♪ In Extremo — Sternhagelvoll', '▶ Play 1 track', playing 'Sternhagelvoll' | 4 log line(s), jpeg filter-song-mdl.jpg | pass |
| results panel: Close / Escape | search 'Kupfergold', Close; again, Escape | Close hides True; Escape hides True | 2 log line(s) | pass |

**Findings** (P122 order, R-576):
- **I-432** — `trim.holds` knows only m4a/mp4/opus. A trim on an mp3/flac track can never be
  changed or removed, while the job reports done.
- **I-426** — Enter in the filter within 0.3 s of typing loses the focus.
- **I-427** — the player's position bar is squeezed to 44–140 px.
- **I-428** — the exceptions box collapses after every tick.
- **I-429** — "Re-check source" on a folder album answers 400 "no URLs".
- **I-430** — clicks on the jobs panel are lost when a 0.7 s poll lands mid-press (8/10 at 120 ms).
- **I-431 (6)** — "Remove empty folders" is never sent by the settings save.
- **I-431 (7)** — a key removal is dropped when the provider changes in the same save.
- **I-433** — the page's merge has no way to the server's `new`.
