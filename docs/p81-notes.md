# P81 — taking the user's collection in: where it stands

Working notes, kept current at every checkpoint (R-341). Message ledger: last out **I-220**, last in
**R-341**; the next report of mine is **I-221**.

The package is R-335, amended by R-336 (a switch per operation), R-337 (precedence — **superseded**),
R-338 (the settings are the state; an album may only be excepted), R-339 (measure on a local copy of
`~/Music/legacy`, not on the NAS) and R-340 (extrapolate at 30 MB/s).

## Done, committed, green

| commit | what |
|---|---|
| `a217ad0` | **precautions** — `src/noaap/precautions.py`: snapshot (one line per file: path, size, mtime, the tags a pass could overwrite verbatim, a fingerprint of every key it could not, a packet digest), safe write (copy beside → prove the recording survived → atomic replace), kept originals (whole, once, before the rename), restore (names + tags + mtimes back, audio verified, fields of theirs that went missing named), `room_for`/`says_room`. 12 cases in `tests/test_precautions.py`. |
| `c022844` | **the rule** — `src/noaap/treatment.py`: the settings are the state; an album's exceptions can only subtract; `for_album`, `held_back`, `says_exceptions`, `with_exception`, `renames`, `retags`. New settings `cover_beside`, `cover_embedded`, `lyrics_embedded`, `rename_adopted`, `retag_adopted`; the plan gains `exceptions`. 8 cases in `tests/test_treatment.py`. |
| `b5c28f3` | **every pass honours it** — the treatment reaches `download.run`, `would_do` and `relocate`; one `Service._run` so no pass can forget; careful writes for adopted albums. **Found a real fault**: retagging an adopted album wiped fields noaap does not model (a 2006 `comment`) — now `keep_unknown` for anything adopted. 6 cases in `tests/test_state_of_the_library.py`. |
| `2bbf6cb` | **the pass** — `src/noaap/intake.py` + `noaap take-in`: adopt → lookups → treatment, artist by artist, resumable (state file beside the snapshot), dry run per track with totals per kind, `--restore`. 7 cases in `tests/test_intake.py`. DESIGN §9 slices 99–101. |

## Done, not yet committed (in the tree, tests green locally)

- **The page**: `STATE_SWITCHES` and the exceptions rule in `logic.mjs`; the settings' new section
  *What every album should have*; the album view's `details.exceptions` panel writing through a new
  `/api/except` door → `Service.set_exception`. Node cases at **220**.
- **README**: *Taking a whole collection in* (the three precautions as a table, what none of them
  covers), the state-of-the-library paragraph, the command table row, five config keys.

## Not done yet, per item of R-335

1. the pass — **done**, except: `--dry-run` does not yet count covers separately from renames/retags
   (it reports 0 covers because the fixture has none); lookups are counted only when they happen.
2. snapshot + restore — **done**, byte-for-byte only via `--keep-originals` (a tag round-trip is not
   byte-identical: measured, same size, different bytes).
3. safe writes — **done**.
4. lookups counted and reported — **done** for MusicBrainz and LRCLIB (`musicbrainz_requests`,
   `lrclib_requests`), but the counters read `client.requests`, which may not exist on either client:
   **verify before reporting numbers**.
5. the page: take-in dialog gains the switches, adopt accepts the library's own folder, merge gains
   "also albums this library does not have" — **not started**.
6. docs — README done; a section on what the snapshot does not cover is in the README table.
7. the measurement — **in progress**, see below.

## The measurement (R-339, R-340)

- Copy: `~/Music/legacy` (41 GB, 2153 files, 2000 audio) → `~/Musik/noaap-takein`. **Done** (the copy
  ran ~14 min). `~/Music/legacy` is never written. The copy is QA scratch and the reviewer's to remove.
- Driver: `<scratchpad>/measure.py` — runs a phase in-process and reads `/proc/self/io`
  (`read_bytes`/`write_bytes`, i.e. what a NAS would see) around it, with the wall time and the
  request counts.
- Phases to run: dry → real (`--apply --keep-originals`) → restore, then compare the restored copy
  with the pristine `~/Music/legacy`.
- Open question I decided to settle with data: all switches on means ~2000 MusicBrainz + ~2000 LRCLIB
  requests for a *training* run. Plan: price one album with lookups on, then decide whether to run the
  whole copy with them or to extrapolate; **say which was done in I-221**.
### Numbers so far (local copy, 132 albums / 2000 tracks / 41 GB)

| phase | seconds | read from device | written |
|---|---|---|---|
| dry run, as first built | 171.9 | 43.77 GB | 0 |
| dry run, after the fix below | **8.8** | **1.63 GB** | 0 |
| real pass, everything on, originals kept | 277.4 | 86.1 GB | 87.7 GB |

Both said the same thing: 132 albums, 2000 tracks, 440 files would be renamed, 1896 retagged.

**The fix, found by the measurement**: reading a folder measured a *packet digest* per file — an
ffmpeg remux, so it read every byte of the collection. That number is what `merge` needs to rank two
copies of one recording; an adoption never asks it. `sources_folder.measure(path, digest=False)` and
`FolderSource.digests` now leave it out, and `intake` turns it off. 19.5× faster, 27× less read.

At 30 MB/s (R-340), for the user's 11,000 tracks: the dry run is **~4.5 minutes** instead of
**~2.2 hours**.

**The real pass found a bug that would have hit the whole collection**: `safely()` wrote to a copy
named `.track.mp3.noaap-new`, and `tag.kind` decides the format **by the suffix** — so every mp3 was
opened as an Opus and **1662 of 2000 files were not tagged** (*"read b'ID3', expected b'OggS'"*). The
renames went through; nothing was damaged, because the write fails before the atomic replace and the
originals were kept. Fixed (the copy keeps the suffix) with a case over all four formats. **The
numbers above are therefore for a pass that renamed 2000 files and tagged only the 338 Opus ones**;
the pass is being re-measured after the restore.

**The restore found two more**, measured on the same 2000 files (102 s, 44.96 GB read, 37.94 GB
written, 0 files whose audio had changed, 0 fields of theirs lost):

- It gave up on **52 files** it could not find, although their originals were sitting in the kept
  store — a pass that renames a file *and* moves its folder defeats both the path and the digest
  search. The kept copy needs no search at all, and is used now.
- The digest search was gated on an equal size, which a retagged file never has. Ungating it
  uncovered the opposite trap on a fixture: two files can hold the **same recording** (a track on an
  album and on a best-of), and claiming one for the other renames somebody else's file away. It now
  asks the same-sized files first and never claims a file the snapshot records under its own name.

Checked against the pristine `~/Music/legacy` after that restore: of the 2093 names present in both,
**2093 are byte-identical**. The rest of the difference is the pass's own files (132 plans) and the
albums whose folder it renamed — the restore puts files back under their recorded path and **names
what it left alone** rather than deleting anything.

Still to measure: the re-run with all three fixes (running), and the price of the lookups.

### What the real pass costs, and three ways to cut it

The pass as built pays for the strongest promise at every layer. Over 2000 tracks / 41 GB that is,
roughly: the snapshot reads every byte (a packet digest per file), `--keep-originals` writes every
byte again, and each careful write copies a file and digests **both** copies. Measured totals are
being collected; the arithmetic says ~200 GB read and ~80 GB written for this collection, which at
30 MB/s is hours over a network.

Three ways to cut it, each giving something up — **for the reviewer and the user to choose**:

1. **Reuse the snapshot's digest in the careful write.** The pass has just computed the source's
   packet digest; `safely(..., expect=<digest>)` would digest only the temporary file. Halves the
   verification, gives up nothing. *(My recommendation; not implemented yet.)*
2. **Verify cheaply where the original is kept.** With `--keep-originals` a bad write is already
   recoverable byte for byte, so the write could check that the file opens and its length and stream
   parameters are unchanged (header reads) instead of digesting. Removes most of the remaining
   verification I/O; gives up "the packets are provably identical" at the moment of writing.
3. **A snapshot without digests** (`--fast-snapshot`). Records path, size, mtime and tags only:
   reading ~1.6 GB instead of 43.8. The restore can still put names and tags back; it can no longer
   *prove* the audio is the audio that was written down.

## Next steps, in order

1. Commit the page + README (this checkpoint).
2. Verify the request counters exist on the MB and LRCLIB clients.
3. Run the measurement: dry, then one album with lookups, then the full pass, then the restore.
4. R-335 item 5 (the page's take-in dialog, adopt in place, merge `--new`).
5. Report I-221: commits, counts, the command's name and its dry-run output, what is untried.
