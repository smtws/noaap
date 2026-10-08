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

**25 tests: 20 mine (CLI/API), 5 the reviewer's (browser).**
Needs per row is in the row's own text; D1–D5 all need my server and are listed for the reviewer.

## Results

Appended as they run.
