# What taking a collection in costs — a measurement (P81, 2026-10-01)

R-339 asked for this and R-340 set the ceiling: *"you have a copy of a handful of albums from the nas
locally: /home/tordt/Music/legacy you can copy it for yourself and train on it without clogging my
network and heating up my nas overnight"*, and *"assume 30MB/sec max"*.

**Nothing here touched the NAS.** Everything ran on a copy of `~/Music/legacy` — 133 album folders,
2000 audio files, 2153 files in all, 41 GB — and `~/Music/legacy` itself was only ever read.

## What is measured, and how

Per phase: wall time, and `read_bytes` / `write_bytes` from `/proc/self/io` before and after. Those two
counters are **device** I/O, which is the number a share would see on the wire; the page cache is
excluded from them, which is why they are the right counters here and why the local wall times are not
comparable to the extrapolations.

The driver, which is all there is to it:

```python
def io_now():
    out = {}
    for line in Path("/proc/self/io").read_text().splitlines():
        key, _, value = line.partition(": ")
        out[key] = int(value)
    return out

before, started = io_now(), time.monotonic()
done = intake.take_in(service, ROOT, choices, dry_run=..., snapshot=SNAP, keep=KEEP)
seconds, after = time.monotonic() - started, io_now()
```

The phases, in the order a person would run them:

| phase | the command it stands for |
|---|---|
| snapshot | `precautions.take(root, snapshot)` — what `--apply` does before its first write |
| dry | `noaap take-in <root>` |
| apply | `noaap take-in <root> --apply --keep-originals <dir>`, everything on, names to the scheme |
| restore | `noaap take-in <root> --restore <snapshot> --apply` |
| compare | every audio file of the restored copy against the pristine original, byte for byte |

The extrapolation to the user's collection is 5.5× this copy (11,000 tracks against 2000) and divides
the bytes by 30 MB/s. Reads and writes are counted together, because both cross the wire.

## The numbers

On a byte-exact copy of `~/Music/legacy`: 133 album folders, 2000 audio files, 153 files of theirs
that are not audio, 41 GB. Everything on, names brought to the scheme, originals kept.

| phase | seconds | read | written | what it did |
|---|---|---|---|---|
| snapshot | 561.6 | 43.78 GB | 1.1 MB | 2153 files written down; the file is 1,073,289 bytes — **499 bytes each** |
| dry run | 10.3 | 2.33 GB | 0 | 132 albums, 2000 tracks: 440 renamed, 2000 rewritten, 72 asked for a cover |
| the pass | 453.1 | 43.31 GB | 87.80 GB | 440 renamed, 2000 rewritten, 4 covers, 2000 originals kept (41 GB) |
| restore | 211.2 | 54.24 GB | 43.76 GB | 2153 files, 573 renamed back, 0 missing, 0 changed, 0 fields of theirs lost |

Then every file of the restored copy against the pristine original: **2153 of 2153 byte-identical**,
nothing of theirs missing or altered. What is left over is 136 files of noaap's own — 132 plans and
the 4 covers it fetched — which the restore names rather than deciding they are rubbish.

**The dry run's numbers are the pass's numbers**: 440 and 2000, said before and done after.

Two things worth having separately:

- **A restore that has nothing to do still costs a full read.** Measured when a pass was skipped:
  568.3 s, 43.26 GB read, 0 written, all 2153 files found at their recorded path — because the only
  way to say *this is the recording that was written down* is to decode it. That is the floor under
  any restore that has no kept originals to copy from.
- **The lookups are not about bytes.** Priced on three albums with a *cold* cache (the user's own
  caches are warm — 84 MB of lyrics, 24 MB of MusicBrainz — and the first attempt therefore measured
  1 request for 43 tracks): **2 MusicBrainz requests an album, ~1 LRCLIB request a track**. The
  client holds itself to 1.1 s and 0.5 s between requests, so for 11,000 tracks the floor is ~2 h of
  deliberate politeness and the measured rate puts it at ~3.2 h. A faster share does not move it.

### At 30 MB/s, for the user's 11,000 tracks

| phase | per track | for 11,000 tracks | local | **at 30 MB/s** |
|---|---|---|---|---|
| snapshot | 21.9 MB read | 241 GB read | 9.4 min | **2.2 h** |
| dry run | 1.17 MB read | 12.8 GB read | 10 s | **7 min** |
| the pass | 21.7 read + 43.9 written | 238 GB + 483 GB | 7.6 min | **6.7 h** |
| the restore | 27.1 read + 21.9 written | 298 GB + 241 GB | 3.5 min | **5.0 h** |
| the lookups | — | ~1,400 + ~11,000 requests | — | **3.2 h** (not disk) |

**A first take-in with everything on is about twelve hours**, and every album is written down as it
finishes, so a connection that drops costs the album it was in. Leaving the lookups for later
(`--no-mb --no-lyrics`) makes it about nine, and a later `repair` does the asking, because the
settings are the state of the library (§9, slice 100).

## What it found

**Ten faults, seven of them in the precautions themselves**, and not one was visible in a suite that
had grown to 1739 cases — every fixture here was Opus, the collection is mp3, and no fixture had a
disc folder, a cover to embed, or a file of the owner's that is not audio.

| | what | what it cost, measured |
|---|---|---|
| 1 | the careful write's temporary copy lost its suffix | 1662 of 2000 files renamed and never tagged |
| 2 | the restore searched for files whose kept originals were right there | gave up on 52 |
| 3 | the digest search was gated on an equal size | a retagged file never has one |
| 4 | the search had no ranking | ~15 digests a file; hours for this copy, days for the collection |
| 5 | the kept copy was filed under the folder the pass had moved the album into | 295 of 2000 back from tags, not bytes |
| 6 | the snapshot's digest was of the *file*, not the recording | 1 file "missing" while its audio was identical; the survival check had become "can ffmpeg read this" |
| 7 | the resume file was one fixed name beside the snapshot | a second collection took in nothing and said `0 albums` |
| 8 | the kept copy dropped the disc subfolder | 67 back from tags; 3 originals never kept at all |
| 9 | the dry run's counter missed one of its own two lines | said 1896, did 2000 |
| 10 | the snapshot recorded audio only | a restore left 8 of the owner's own files in a folder they never made |

Each is written up where it belongs — DESIGN §9 slices 99 and 101 — and each has a case that fails
without its fix, in `tests/test_precautions.py`, `tests/test_intake.py` and
`tests/test_state_of_the_library.py`. There is a `one_second_of_mp3` fixture now, and a two-disc
album with a track of the same name on both discs.

**What this says about the suite**, and it is the point worth keeping: the cases were not weak, they
were *homogeneous*. One format, one folder shape, nothing but audio. A fixture is a hypothesis about
what the world looks like, and this collection disagreed with ours ten times in one afternoon.
