# P81b — what the first real collection was still getting wrong

Working notes. The package is R-342 (six items) with the tree released in R-343; P81 is accepted at
`a9683af`. Message ledger: last out **I-222**, last in **R-343**; the next report of mine is I-223.

## The items, and where each stands

| | what | state |
|---|---|---|
| 1 | per-disc track totals, a disc total, and the repair check naming the 627 | **done** |
| 2 | the snapshot's default name taken from the root | **done** |
| 3 | a restore removes what the pass created, from a record the pass writes | **done** |
| 4 | cut 1: reuse the snapshot's digest in the careful write, then re-measure | **done** |
| 5 | empty disc folders after a flattening | **done** — they were left; now cleared |
| 6 | docs | **done** |

## 1 — the totals

What the writers did, before: `tracktotal`/`totaltracks` (opus, flac), `TRCK` (mp3) and `trkn` (m4a)
all carried `len(plan.tracks)`, the whole album. The disc total was written by the mp4 writer only.

In the user's own library, read-only: **23 multi-disc albums, 627 files, all opus**, every one saying
the album's length, and **0 of 627** carrying a disc total of any kind. Track numbers restart on each
disc — checked across all 52 discs — which is what makes a per-disc total the right number.

The reader needed the same fix, and this is the half that would have been missed: ID3 carries each
number with its total in one frame, and `TPOS` went through the loop that reads a frame's whole text.
Write `TPOS` as `2/3` without touching the reader and every pass would come back with `discnumber` =
"2/3", compare it against "2", and say the file needs retagging — for ever. There is a case for the
round-trip in all three containers.

**What the repair check lists**, from `would_do`, the function the check prints from, over the whole
library (read-only, nothing written, nobody asked anything):

```
DOMINUM — The Dead Don’t Die
  01 would be retagged: tracktotal “20” → “11”; totaltracks “20” → “11”;
                        disctotal nothing → “2”; totaldiscs nothing → “2”
```

247 albums read, **627 files named, 0 single-disc albums named at all** — the fix moves nothing it
should not, because for one disc the two numbers are equal.

**One wrinkle to decide, not decided here**: 8 of those 52 discs have a gap in their numbering (disc 3
of *Memento Mori* has 9 tracks present and numbers up to 11; disc 1 of *Hello World* 31 present up to
38). For those the written total is the count of tracks *present*, not the disc's real length. That is
the same ambiguity a single-disc album has always had, and taking the highest number instead would be
a guess about where the gap is, so the count stands — but the user may prefer the other answer.

## 3 — what a restore now takes away

The pass writes `<root>-snapshot.made.json` beside the collection, one entry per file it created,
appended per album like the resume file. It is **measured, not predicted**: what is in the album
folder besides its audio, asked before the pass touches it and again afterwards. So a writer that
gains a file does not have to remember to declare it.

A restore removes exactly those paths, listing each one in its dry run, and then its own two records.
Run through the real CLI on a two-disc album with a `booklet.jpg` of the owner's in `CD 2`:

```
  put …1-01 - Opening.mp3 back as Aphelion/Live im Winter/CD 1/01 Opening.mp3
  …
  removed Aphelion/Live im Winter/.ytalbum.json — the pass wrote it
5 file(s) restored, 4 renamed back, 1 of the pass's own removed, 0 whose audio is not what it was
  removed collection-snapshot.take-in.json — the record of a pass that is undone
  removed collection-snapshot.made.json — the record of a pass that is undone
```

and the folder afterwards holds its 5 files, both disc folders and the booklet, and nothing else.

## 5 — the disc folders

They were left behind, empty: measured on a two-disc album, `CD 1` and `CD 2` both still standing
after the pass. (The reviewer's run saw none left; mine did.) The pass clears a folder it emptied,
`rmdir` is what decides whether it is empty, and a folder holding anything else keeps it and keeps
the folder.

## 4 — the measurement, and a correction to P81's hours

On a byte-exact copy of `~/Music/legacy` (2153 files, 41 GB), everything on, names to the scheme,
originals kept. Three runs of the pass agreed within 1.5 %: 643.6, 650.5, 662.0 s.

| phase | seconds | read | written | and |
|---|---|---|---|---|
| snapshot | 567.8 | 43.28 GB | 1.1 MB | 2153 files, 499 bytes each |
| dry run | 10.4 | 2.25 GB | 0 | 440 renames, 2000 rewrites, 72 asked for a cover — the pass's own numbers |
| the pass | 650.5 | 43.75 GB | 87.80 GB | 440 renamed, 2000 rewritten, 4 covers, 2000 originals kept |
| restore | 248.5 | 54.21 GB | 43.76 GB | 2153 files, 573 renamed back, **141 of the pass's own removed** |

and afterwards, every file against the pristine original: **2153 of 2153 byte-identical, nothing of
theirs missing or altered, nothing of noaap's left** — not a plan, not a cover, **not an empty
folder**.

### What cut 1 actually buys

The pass went **453 s → 650 s** and its device reads did not fall (43.31 → 43.75 GB). That looks like
a loss, and locally it is: the read cut 1 avoids was being served from the page cache — the original
had just been copied byte for byte — and the digest it adds is a decode, which is dearer than a remux.

So the two shapes were measured directly, on 24 real files, **with the cache evicted** (`os.sync()`
then `posix_fadvise(DONTNEED)`; the first attempt measured 0 bytes because the scratchpad is tmpfs on
this machine — no device to read from):

| | device read | time |
|---|---|---|
| both files (`same_audio(path, tmp)`) | **0.97 GB** for 0.49 GB of audio — twice the file | 4.0 s |
| the copy only (cut 1) | **0.49 GB** — once | 6.5 s |

So cut 1 halves *that step's* reading and costs ~60 % more CPU on it (flac is faster, mp3 and opus
slower). Over a share the read is the ceiling and the CPU overlaps with the wire, so the ruling is
right for the NAS and wrong for this laptop. Both numbers are above; neither is hidden.

### The hours at 30 MB/s — and P81's were too low

**A local `read_bytes` count understates a share, and I reported it as if it did not.** The page cache
serves the second and third read of a file that was just copied, so the pass measured ~1× the
collection locally where a share must carry every read over the wire. The honest figure is the
per-file accounting:

| | reads per file | writes | for 11,000 tracks (225 GB) | at 30 MB/s |
|---|---|---|---|---|
| snapshot | 1 | — | 225 GB | **2.1 h** |
| the pass, with cut 1 | 3 (kept copy, temp copy, verify) | 1 (+1 if the kept store is on the share) | 900 GB | **8.3 h** (10.4 h) |
| the pass, without cut 1 | 4 | 1 (+1) | 1125 GB | **10.4 h** (12.5 h) |
| the restore from kept originals | 1 | 1 | 450 GB | **4.2 h** |
| the lookups | — | — | ~1,400 + ~11,000 requests | **3.2 h**, and no disk |

So **cut 1 is worth ~2.1 h of the pass**, which is what it was ruled in for, and a first take-in with
everything on is **~13.5 hours** rather than the ~12 I reported in I-221 — 2.1 h of snapshot, 8.3 h of
pass, 3.2 h of asking. Leaving the lookups for a later `repair` makes it ~10.5 h. The local times in
the table above are what this laptop did on an NVMe and are not the number to plan by.

## 11 — one more the collection found

A restore left **19 folders standing empty**: the album folders the pass had moved each album into,
husks of noaap's spelling of their names, plus two `.thumb` caches of theirs whose files had gone
back. Ruling 3 covers them — a folder the pass made is something the pass made — so the record holds
directories as well as files, and `rmdir` is what removes them, so one that still holds anything
keeps it.

Fixing that uncovered the shape of the same fault one level up: asking about a folder's **parent**
right after removing the folder asks while the artist's other albums are still there, so whether it
works depends on the order the albums happen to come in. Three artist folders were left — `van Canto`,
`Stimmgewalt`, `Dämmerland, Versengold`. The sweep is deepest-first over the folders *and* their
parents now, and the confirmed run leaves **0**.

And a consequence worth knowing, because it is silent: `relocate` refuses to move an album onto a
folder that already exists, and says so only in the log. While those 19 husks were there, a second
take-in left **17 albums unrenamed** and reported nothing wrong. With the husks gone that cannot
happen; it is the reason the first re-measurement was thrown away and run again.

---

# P81c — the hole in the gapped-disc ruling

R-346, from the user reading the ruling that the count of tracks *present* stands: *"it could have
track 10 of 9? that'd be odd"*. It could, and on 8 of the 52 discs in their collection it would have.

**The rule**: a track total is never below the highest number present on the disc. In order — what
every file on the disc already agreed on, where that is credible; otherwise the highest number
present; and the count only where it equals that. One place decides it, `AlbumPlan.disc_length`, and
all three writers ask it. Six cases in `tests/test_formats.py`.

**The one thing in that ordering I could not take literally**, and it needed a decision rather than a
guess: noaap itself wrote the album's track count into every file, so for all 23 multi-disc albums
"what every file already said" is the album's length — agreed by every track, below no number, and
wrong. Taking it would have kept all 627 files exactly as they are and made the package a no-op,
which the acceptance criterion (*the check must still name the 627*) rules out. So where more than one
disc exists, a remembered total equal to the album's own length is set aside. A value that is the
thing being corrected cannot be evidence about what it should be corrected to.

**The 8 gapped discs, and what each would be written as** (`would_do` over the real library,
read-only):

| album | disc | present | highest | writes |
|---|---|---|---|---|
| Feuerschwanz — Memento Mori | 3 | 9 | 11 | **11** |
| Lord of the Lost — Swan Songs | 2 | 7 | 8 | **8** |
| Lord of the Lost — Weapons of Mass Seduction | 1 | 11 | 12 | **12** |
| Michael Jackson — Hello World | 1 | 31 | 38 | **38** |
| Michael Jackson — Hello World | 3 | 20 | 24 | **24** |
| Mono Inc. — Together Till The End | 3 | 8 | 11 | **11** |
| Mono Inc. — Welcome to Hell | 2 | 9 | 10 | **10** |
| dArtagnan — Feuer & Flamme | 2 | 6 | 7 | **7** |

**And the check now names more than the 627, which is a decision for the reviewer.** The rule is
about a disc, not about a set, so it reaches a single-disc album with a gap too:

| | albums | files |
|---|---|---|
| multi-disc (the 627, unchanged) | 23 | **627** |
| single-disc with a gap in its numbering | 33 | **461** |
| | 56 | **1088** |

Those 461 are albums of thirteen tracks whose highest number is fourteen, which were being written as
*of 13*. It is the same defect one disc at a time, so I left the rule general — but scoping it to
multi-disc albums only is one condition, and the brief said "a single-disc album (unchanged)".
