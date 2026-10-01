# P81b — what the first real collection was still getting wrong

Working notes. The package is R-342 (six items) with the tree released in R-343; P81 is accepted at
`a9683af`. Message ledger: last out **I-222**, last in **R-343**; the next report of mine is I-223.

## The items, and where each stands

| | what | state |
|---|---|---|
| 1 | per-disc track totals, a disc total, and the repair check naming the 627 | **done** |
| 2 | the snapshot's default name taken from the root | **done** |
| 3 | a restore removes what the pass created, from a record the pass writes | **done** |
| 4 | cut 1: reuse the snapshot's digest in the careful write, then re-measure | code **done**, measurement to run |
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

## 4 — the measurement

To run on a fresh copy of `~/Music/legacy`, removed afterwards. What changed since P81's numbers: the
careful write reads one file instead of two (`expect`), and the snapshot now also records the 153
files that are not audio. The hours at 30 MB/s go in here when they exist.
