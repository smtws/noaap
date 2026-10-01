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
| `7ce15ba` | **the page and the three faults the collection found** — the Take-in dialog's switches, `merge --new`, `Service.take_in_all`; the temporary copy keeps its suffix; the restore uses the kept original before it searches, and its search is not gated on the size. README: *Taking a whole collection in*. |

## Per item of R-335

1. the pass — **done**. The dry run says renames, retags and covers per kind; the real pass counts
   the same things (it counted nothing until this checkpoint).
2. snapshot + restore — **done**, byte-for-byte only via `--keep-originals` (a tag round-trip is not
   byte-identical: measured, same size, different bytes). The search is ranked (see below).
3. safe writes — **done**, and proved on mp3 as well as Opus.
4. lookups counted and reported — **done** and verified: `MusicBrainz.requests` (`mb.py:159`) and
   `Lrclib.requests` (`lyrics.py:192`) both exist and count only requests that really went out.
5. the page: take-in dialog with the switches, adopt in place, `merge --new` — **done** (`7ce15ba`).
6. docs — **done**: the take-in section, the three precautions as a table, what the snapshot does not
   cover, the take-in mode in the page, five config keys.
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
| real pass, everything on, originals kept — **1662 files silently not tagged** | 277.4 | 86.13 GB | 87.67 GB |
| restore of that state | 102.0 | 44.96 GB | 37.94 GB |
| **real pass again, with all three fixes** | **614.5** | **87.42 GB** | **87.80 GB** |

The second pass logged **0 errors** where the first logged 1662, and took 2.2× as long — the missing
time is the work that had been failing. Both dry runs said the same thing: 132 albums, 2000 tracks,
440 files would be renamed, 1896 retagged.

What the 87 GB read and the 88 GB written are made of, per pass: the snapshot digests every file
(41 GB read), `--keep-originals` copies every file it touches (41 GB written), and each careful write
copies the file and digests both copies (another 41 GB written, and 41 GB read for the temporary copy
— the source itself comes back out of the page cache). A share has less cache than this laptop, so the
extrapolation below is a floor, not a promise.

### At 30 MB/s, for the user's 11,000 tracks (R-340)

5.5× this copy. Bytes over a share cross the wire in both directions, so the hours are read + written.

| phase | local | bytes for 11,000 tracks | at 30 MB/s |
|---|---|---|---|
| dry run (as built, digests) | 15.8 min | 241 GB read | **2.2 h** |
| dry run (digests off) | 48 s | 9 GB read | **5 min** |
| the real pass | 56 min | 481 GB read + 483 GB written | **8.9 h** |
| the restore | 9.4 min | 247 GB read + 209 GB written | **4.2 h** |

Eight to nine hours for the pass is the number that matters, and it is why the three cuts below are
worth a decision rather than a shrug.

**The re-run found the fourth fault, and the worst so far — the restore does not scale.** With all
2000 files renamed *and* retagged, the digest search had nothing to go on: its one guess was the
size, a retagged file has a different size, so for every file it asked ffmpeg about every candidate
in the tree. Measured: 8 GB read in four minutes and nowhere near done — hours for this copy, days
for the collection. I stopped it and ranked the candidates instead: the folder the last file of this
album turned up in, then the words the two names share, then the size. The digest still decides, so
the ranking cannot be wrong, only slow. On a twelve-file mp3 fixture that reproduces the shape:
**54 digests unranked, 24 ranked** (24 = one to find each file, one to prove its audio survived).

**Why the suite could not see any of this: every fixture here was Opus, and the collection is mp3.**
Both the lost suffix and the size guess are mp3 behaviour — mutagen's ID3 padding even swallows a
small retag, so a fixture has to write a *big* tag to change the size at all. There is a
`one_second_of_mp3` fixture now, and three cases use it.

**Then the restore of *that* run found the fifth and the sixth, and they are about the digest
itself.** Of 2000 files it reported 1 missing and — against the pristine original — only 1704 of
1999 byte-identical, where the run before had been 2093 of 2093.

5. **The kept originals were filed under the folder the pass had just moved the album into.** The
   pass relocates an album into the scheme *before* the first file is written, and `put_aside` used
   the path the file had at that moment, so the copy landed under a name no snapshot ever recorded.
   `restore` looks for `kept / <the recorded path>`, missed them, fell back to the digest search and
   put those files back **from their tags instead of byte for byte** — 295 of 2000, including one
   whole album whose originals were under a name nothing would ever look for. It is filed under the
   recorded folder now (`run(..., keep_as=…)`), and the case fails without the fix.
6. **The digest in the snapshot was of the file, not of the recording.** `stream_sha` includes the
   trailing tag block that ffmpeg's mp3 demuxer hands over as audio data — its own docstring says so
   — so the moment the pass embedded a cover, every mp3's recorded digest was stale. One file was
   left behind as *missing* while its audio sat there, decoding identically to the pristine original
   (checked by hand: same `decoded_sha`, different `stream_sha`, 116,958 bytes bigger — the cover).
   And the same digest was what "0 files whose audio is not what it was" rested on: where the packets
   had moved, the check had been reduced to *"can ffmpeg read this file at all"*, which is not a
   check. **The snapshot records `decoded_sha` now** — the recording, not the file — and one function,
   `holds_it`, answers both questions. It costs a decode instead of a remux per file at snapshot
   time: three times the CPU, the *same* read, and over a share the read is the ceiling. A snapshot
   written before this still restores, with the weaker digest it was written with.

Both are mp3-and-real-collection faults again, and both now have cases (an embedded cover that does
not hide a file, a file whose audio really changed being named, the kept original filed where the
snapshot looks).

### What the real pass costs, and three ways to cut it

First, what the careful write actually costs, measured on 24 files drawn at random from
`~/Music/legacy` (read, copied to the scratchpad, retagged there — the collection is never written):

| | files | each | packets still match after a retag | two packet digests | two decodes |
|---|---|---|---|---|---|
| flac | 12 | 49.1 MB | 12/12 | 0.21 s | 0.34 s |
| mp3 | 10 | 8.4 MB | 10/10 | 0.14 s | 0.58 s |
| opus | 2 | 3.3 MB | 2/2 | 0.13 s | 0.89 s |

**24 of 24: the cheap check answers, and the decoded fallback never fires.** So the verification is
two packet digests per written file — two full reads — and not the three-times-dearer decode it could
have been. That settles which of the three cuts is worth anything:

Three ways to cut the 8.9 hours, each giving something up — **for the reviewer and the user to
choose**:

1. **Reuse the snapshot's digest in the careful write** — `safely(..., expect=<the recorded digest>)`
   digests only the temporary copy and falls back to the full comparison if it disagrees. One read of
   the collection instead of two: **~2.1 h of the 8.9**, and it gives up nothing, because the digest
   it trusts is one this pass wrote down itself minutes earlier. *(My recommendation. Not
   implemented: it threads a new argument from the pass through `run` into `safely`, and it would
   change the numbers above, so it is the reviewer's call whether it belongs in P81 or after it.)*
2. **Verify cheaply where the original is kept** — **not worth doing.** It was proposed to avoid the
   decoded comparison; the table above says that comparison never happens. What is left is one cheap
   digest, and cut 1 removes it for less.
3. **A snapshot without digests** (`--fast-snapshot`): path, size, mtime and tags only, reading
   ~1.6 GB instead of 43.8 — another **~2.1 h**. The restore can still put names and tags back, and
   can still restore byte for byte from the kept originals; what it loses is the ability to *find* a
   renamed file at all (the search is the digest) and to prove that a recording is the one that was
   written down. With `--keep-originals` that is a smaller loss than it sounds, and without it the
   restore is left with nothing but the paths.

## Done since the last checkpoint

- `cover_beside` was a switch **nothing acted on**: only a download ever fetched a cover, so neither
  `repair` nor a take-in could put one beside an album somebody already owns, whatever the settings
  said. An adopted album's cover address is now its own folder — the picture inside the first track
  that carries one (269 of 2000 files in the reference collection have one) — and `run` asks for a
  cover when the treatment wants one rather than when it happens to be downloading. Two cases in
  `test_state_of_the_library.py`, one in `test_intake.py`.
- The real pass **counted nothing**: `renamed: 0, retagged: 0` came back from a run that renamed and
  rewrote 2000 files, which is how the first run's numbers said nothing about the 1662 files it had
  failed to tag. It counts what the pass itself reports per track now, and says covers, words and
  requests in its closing lines.
- The dry run's cover count was a grep for the word *cover* in its own lines; it is the line the pass
  prints now.
- The restore's "left alone" line claimed plans and covers among files it had never looked at — a
  snapshot records audio only. It says what it means now, and the README says what the snapshot does
  not cover.
- Item 4 of R-335 checked: `MusicBrainz.requests` and `Lrclib.requests` both exist (`mb.py:159`,
  `lyrics.py:192`), counted where a request really goes out, so the reported numbers are real.

## Next steps, in order

1. **A second copy** of `~/Music/legacy` at `~/Musik/noaap-takein2` (running). The first copy is too
   muddled to measure on: two restores were interrupted on purpose, so it holds 21 strays, a kept
   store filed under the wrong names, and plans that no longer match the files. It stays as it is —
   the reviewer's to remove — and nothing is deleted to make room.
2. On the fresh copy, in one run: snapshot (now decoding), dry run, the whole pass with everything on
   and the originals kept, the restore, and the comparison with the pristine original. Those are the
   numbers I-221 reports.
3. Price the lookups on three albums of the fresh copy. The first attempt measured nothing: the
   plans in the muddled copy no longer matched the files, so the pass skipped every track in 0.2 s
   and asked nobody anything (0 requests, 0 renames) — a result I nearly reported as "the lookups
   are free".
4. Report I-221.
