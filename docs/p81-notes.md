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
- Driver and method: **`docs/spikes/2026-10-take-in-cost.md`**, which is the measurement's own
  write-up — what was measured, how, the numbers, and the ten faults in one table.
- Phases to run: dry → real (`--apply --keep-originals`) → restore, then compare the restored copy
  with the pristine `~/Music/legacy`.
- Open question I decided to settle with data: all switches on means ~2000 MusicBrainz + ~2000 LRCLIB
  requests for a *training* run. Plan: price one album with lookups on, then decide whether to run the
  whole copy with them or to extrapolate; **say which was done in I-221**.
### The numbers, on a byte-exact copy of the user's own collection

133 album folders, 2000 audio files, 153 files of theirs that are not audio, 41 GB. Every phase run
with everything on and the names brought to the scheme; `read`/`write` are `/proc/self/io`, which is
device I/O — what a share would see on the wire.

| phase | seconds | read | written | what it did |
|---|---|---|---|---|
| snapshot | 561.6 | 43.78 GB | 1.1 MB | 2153 files written down; the file is 1,073,289 bytes — **499 bytes each** |
| dry run | 10.3 | 2.33 GB | 0 | 132 albums, 2000 tracks: 440 renamed, 2000 rewritten, 72 asked for a cover |
| the pass | 453.1 | 43.31 GB | 87.80 GB | 440 renamed, 2000 rewritten, 4 covers written, 2000 originals kept (41 GB) |
| restore | 211.2 | 54.24 GB | 43.76 GB | 2153 files, 573 renamed back, **0 missing, 0 changed, 0 fields of theirs lost** |

And then every file of the restored copy against the pristine original: **2153 of 2153
byte-identical**, nothing of theirs missing, nothing of theirs altered. What is left over is 136
files of noaap's own — 132 plans and the 4 covers it fetched — which the restore names rather than
deciding they are rubbish.

**The dry run's numbers are the pass's numbers**: 440 renamed and 2000 rewritten, said before and
done after (fault 9). It reads 2.33 GB instead of 43.8 because an adoption does not rank copies and
so does not digest every file (`FolderSource.digests`, which only `merge` needs).

The dry run's numbers are now the pass's numbers (fault 9), and it reads 2.33 GB instead of 43.8
because an adoption does not rank copies and so does not digest every file (`FolderSource.digests`,
which only `merge` needs).

**A restore that has nothing to do still costs a full read.** Measured by accident when a pass was
skipped: 568.3 s and 43.26 GB read, 0 bytes written, every one of the 2153 files found at its
recorded path — because the only way to say *this is the recording that was written down* is to
decode it. That is the floor under any restore without kept originals.

### Earlier runs, kept because they are what found the faults

| phase | seconds | read | written |
|---|---|---|---|
| dry run, before `digests=False` | 171.9 | 43.77 GB | 0 |
| the pass, **1662 files silently not tagged** (fault 1) | 277.4 | 86.13 GB | 87.67 GB |
| the pass, after faults 1–3 | 614.5 | 87.42 GB | 87.80 GB |
| the pass, snapshot taken separately, with the covers | 453.1 | 43.31 GB | 87.76 GB |
| restore of that, 67 files not byte-identical (fault 8) | 231.8 | 53.70 GB | 43.25 GB |

### At 30 MB/s, for the user's 11,000 tracks (R-340)

5.5× this copy (11,000 tracks against 2000). Over a share the bytes cross the wire in both
directions, so the hours are read **plus** written. The local times are what this laptop did on a
local disk and are not the number to plan by.

| phase | per track | for 11,000 tracks | local | **at 30 MB/s** |
|---|---|---|---|---|
| snapshot | 21.9 MB read | 241 GB read | 9.4 min | **2.2 h** |
| dry run | 1.17 MB read | 12.8 GB read | 10 s | **7 min** |
| the pass | 21.7 read + 43.9 written | 238 GB + 483 GB | 7.6 min | **6.7 h** |
| the restore | 27.1 read + 21.9 written | 298 GB + 241 GB | 3.5 min | **5.0 h** |

**Taking the collection in is the snapshot plus the pass: ~9 hours over the share**, and a restore
of all of it would be 5 more. An overnight job, once, with a resume file that means a broken
connection costs the album it was in and nothing else.

Two things to say plainly about those hours. The snapshot's 2.2 h is **one read of the whole
collection and nothing else** — it cannot be made cheaper without giving something up (cut 3 below).
The pass's 6.7 h is mostly the copies: `--keep-originals` writes the collection a second time and the
careful write a third. Without `--keep-originals` the pass is ~4.5 h and the way back is no longer
byte for byte.

### The lookups, which are not about bytes at all (R-335 item 4)

Priced on three album folders copied out of `~/Music/legacy`, with a **cold cache** — the user's own
caches are warm (84 MB of lyrics, 24 MB of MusicBrainz, from their own use of the app), so the first
attempt at this measured **1 request for 43 tracks** and would have been reported as "the lookups are
free". `XDG_CACHE_HOME` pointed at a throwaway directory; their caches were not touched.

| | measured | per unit |
|---|---|---|
| 2 albums, 31 tracks, 33.0 s | 4 MusicBrainz requests | **2 an album** |
| | 32 LRCLIB requests | **~1 a track** |

For 11,000 tracks in ~700 albums that is ~1,400 MusicBrainz and ~11,000 LRCLIB requests. The client
holds itself to one request every 1.1 s at MusicBrainz and every 0.5 s at LRCLIB, so **the floor is
26 min + 1.5 h ≈ 2 h of deliberate politeness**, and the measured end-to-end rate (1.06 s a track)
puts it at **~3.2 h**. None of that is disk: a faster share does not move it, and it is serial by
design because both services are somebody else's.

So: **a first take-in of the collection with everything on is ~12 hours** — 2.2 h of snapshot, 6.7 h
of pass, 3.2 h of asking. Each album is recorded as it finishes, so a connection that drops costs
the album it was in. With the lookups left for later (`--no-mb --no-lyrics`) it is ~9 h, and a later
`repair` does the asking, because the settings are the state of the library (§9, slice 100).

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

**And the seventh came out of my own measurement setup, which is the best kind of evidence.** The
resume file was one fixed name beside the snapshot, `noaap-take-in.state.json`, so two collections
whose snapshots share a directory share one resume file — and the root written into that file from
the first version was never read back. A second copy of the same collection, with its own snapshot
in the same folder, read the first copy's state, decided all 133 albums were done, and took in
**nothing in a tenth of a second** while reporting `0 albums, 0 tracks`. I nearly recorded that as a
measurement. The file is named after its own snapshot now (`<snapshot>.take-in.json`) and the root is
read back and checked; a state file for another root is not this run's.

**The clean cycle then found three more — 8, 9 and 10 — and the tenth is the one a person would
notice.** This is what a measurement is for: every one of these was invisible in a suite of 1736
cases.

8. **The kept original dropped the disc subfolder.** Filing the copy under the *album's* recorded
   folder (fault 5's fix) is not enough: a file at `Album/cd1/x.mp3` was kept as `Album/x.mp3`. 67 of
   2000 came back from their tags instead of byte for byte — and where two discs held a track of the
   **same name**, both mapped onto one kept copy, so **3 originals were never kept at all** and
   nothing said so. It is filed under the file's own recorded path now, which is unique by
   construction, and that ends the class. The case is a two-disc album with a track of one name on
   both discs.
9. **The dry run undercounted by 104 files.** It said 1896 audio files would be rewritten; the pass
   rewrote 2000. The lines were all there — the counter looked only for *would be retagged* and
   missed *would be rewritten with the same tag values*, which is a rewrite too. This is §9 slice 85
   again, in the arithmetic rather than in the lines, and the case now runs both passes over one
   fixture and insists their numbers agree.
10. **A restore left the user's own non-audio files in a folder they never made.** The snapshot
    recorded audio only; the pass moves a whole album folder into the scheme, so their `cover.jpg`,
    their `New Album Releases.url` and their `.thumb` cache went with it — and the restore, which
    puts audio back by its recorded path, left them behind. Measured: **8 of their files** ended up
    somewhere else while the album looked restored. The snapshot records **every** file now, with a
    digest of the bytes where it is not audio; 153 such files in this collection, 14 MB, which is
    nothing for the thing it closes.

And one number that was right and read wrong: the dry run says **72 albums would be asked for a
cover** and the pass wrote **4**. Both are correct — 72 of the 132 album folders hold no `cover.*`,
and 4 of those have a picture inside one of their files. The line said "would be given a cover",
which promised the answer; it says "would be asked for a cover" now.

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

1. **Report I-221.** Everything R-335 asked for is built, measured and pushed; the report carries the
   numbers, the ten faults, and the three decisions below.
2. **For the reviewer and the user**, in the order I would ask them:
   - the three ways to cut the pass (above): cut 1 is my recommendation and gives up nothing, cut 2
     is measurably pointless, cut 3 is a real trade. None is implemented.
   - after a restore, the 132 plans the pass wrote are left behind describing names that no longer
     exist. The restore names them and removes nothing, which is the rule it is built on; whether a
     restore should also undo noaap's *record* of the albums is a question about what "a way back"
     means, not a bug I should settle alone.
   - the QA copies are the reviewer's to remove: `~/Musik/noaap-takein` (41 GB, deliberately left in
     a half-restored state as evidence), `~/Musik/noaap-takein-originals` (41 GB),
     `~/Musik/noaap-takein2` (41 GB, restored and byte-exact), `~/Musik/noaap-takein2-kept` (41 GB),
     `~/Musik/noaap-lookups`, `~/Musik/noaap-lookups2`, and the snapshots beside them.
3. Queued, not started, and not part of P81: bracketed non-speech caption cues; first-load cover
   contention.
