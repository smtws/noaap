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
- Numbers so far: none yet.

## Next steps, in order

1. Commit the page + README (this checkpoint).
2. Verify the request counters exist on the MB and LRCLIB clients.
3. Run the measurement: dry, then one album with lookups, then the full pass, then the restore.
4. R-335 item 5 (the page's take-in dialog, adopt in place, merge `--new`).
5. Report I-221: commits, counts, the command's name and its dry-run output, what is untried.
