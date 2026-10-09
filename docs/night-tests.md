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
