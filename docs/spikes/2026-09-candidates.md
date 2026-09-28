# Several sources for one track, and replacing the worse one

A design spike, 2026-09-28, on top of the Source boundary proposed in
[2026-09-sources.md](2026-09-sources.md). No code. Everything measured here was read from the
reference library without writing to it.

Four things the sources spike does not cover: a track that can come from more than one place, a
folder as a source, deciding which copy is better, and undoing that decision.

## 1. A track holds candidates, not a video id

Today a track **is** a YouTube video: `video_id` is its identity, and `source_override` is a
special case bolted beside it — one alternative, same provider, used for exactly one purpose ("the
official video has a film around it"). In the reference library it is used **once in 3946 done
tracks**, which says it is the right idea and the wrong shape.

```
Candidate = { ref, provider, found, length, quality?, note? }

PlanTrack.candidates: list[Candidate]   # every place this recording is available
PlanTrack.chosen: str | None            # the ref of the one in use; None = the first
```

`effective_id` becomes "the chosen candidate's ref", and `source_override` folds in exactly:
overriding *is* choosing a non-default candidate. The difference is that the alternatives are now
**written down before they are used**, which is what makes ranking and replacing possible at all.

**Additive, as P43 requires.** `video_id` and `source_override` stay and keep their meaning. When
`candidates` is absent — every plan on disk today — it is synthesised on load: one candidate from
`video_id`, a second from `source_override` if set, `chosen` pointing at the override. Nothing is
rewritten until something else changes the plan, and an older ytalbum reading a newer plan carries
`candidates` through untouched (DESIGN §9, slice 48). The field can therefore ship **before** the
boundary, against YouTube alone, and mean something immediately.

## 2. An intake folder, and the library as its own source

Three arrangements, and they are not the same problem.

| | input | output | what "download" means |
|---|---|---|---|
| today | YouTube | the library | fetch |
| **intake** | a folder you drop files into | the library | copy in, tag, file |
| **in place** | the library itself | the library | nothing; adopt what is there |

**Intake** is the easy one and should come first: it is a Source whose `collection()` is a directory
listing and whose `audio()` is a copy. The library stays the only output, the ownership contract is
untouched, and a file that fails to classify simply stays in the intake folder.

**In place** is the one that can hurt. Scanning the library as a source means the plan starts
describing files ytalbum did not write, and the rule "the plan is the only state" now has to
tolerate files with no plan. It is worth doing — it is how somebody adopts an existing collection —
but it needs its own package and its own answer to "what happens when the scan disagrees with a
plan that already exists".

### Identity, without a video id

A local file has no id. Three candidates, and none is free:

| | breaks when | cost |
|---|---|---|
| path | the file moves or is renamed | free |
| tags | the user edits them — which this program exists to do | free |
| **content hash** | never (the audio is the audio) | a read of every file |

**Recommended: hash the audio stream, not the file.** Hashing the container makes a retag look like
a new recording, which is exactly wrong here, because ytalbum retags constantly. `ffmpeg -i f
-map 0:a -c copy -f hash -` gives the decoded-stream hash without re-encoding. Path and size stay as
a **cheap pre-filter**: hash only when (size, duration) collides with something already known.

What this spike cannot tell you is whether that is fast enough — see the last section.

### `update`, and the watchdog

A folder source's `changed()` is a directory mtime plus the cheap pre-filter; no network, so `update`
over a folder is free where over YouTube it is one request per album. A **watchdog** (react to new
files without being asked) is a fourth thing again: it needs a long-running process, and it must not
run inside the web service's job lanes — a file appearing must *enqueue* an intake job, not do the
work on the watcher's thread. Separate package, after intake works when asked.

## 3. Which copy is better

**The rule, in order. Stop at the first that decides.**

0. **A user edit wins, always.** A field the user set, lyrics the user wrote, a trim the user placed:
   none of these is ever overridden by a better candidate. A replacement inherits them.
1. **Is it the same recording?** Compare the **kept** length — what the user actually hears, after
   any trim — against the authoritative length: the MusicBrainz recording length, else LRCLIB's
   consensus. Bands, from what this library already shows:
   - within **3 s** → the same recording;
   - **3–20 s** → suspect: an intro, an outro, a fade. Do not rank on it; ask the aligner
     (`§9, slice 46` already does exactly this for words) or a person;
   - **beyond 20 s**, or under 60% of the reference → **a different recording**, and not a candidate
     for this track at all.
2. **Then, and only then, quality.** Lossless over lossy; then bitrate; then sample rate; then
   channels.
3. **Tie-breaks**, in order: a file that needs no trim over one that does; tags from MusicBrainz
   over tags from the source over tags derived from a title; then **the incumbent wins** — a tie
   must never cause churn.

**Why length first and not quality.** Because the failure it prevents is categorical and the one it
costs is a matter of degree. Measured over the 2539 done tracks that have both a file length and a
reference: median gap **0.3 s**, p90 **17.1 s**, maximum **514 s**. The tail is not bad encodes, it
is other recordings. Quality-first would happily replace a complete 160 kbps track with a 320 kbps
ninety-second excerpt, and the user would have to notice by listening.

### Three shapes from the real library

| shape | file | reference | gap | rule says |
|---|---|---|---|---|
| a track with the film around it | 245.6 s | 213.8 s | **+31.8** | different-recording band. Not a candidate to rank — this is what `source_override` and trimming are for. |
| an excerpt sold as the song | 99.9 s | 215.5 s | **−115.6** | 46% of the reference: rejected outright, whatever its bitrate. |
| the ordinary case | 282.1 s | 282.1 s | **+0.02** | same recording — so the decision falls through to quality, which is the only place quality should ever decide. |

The middle row is the one that matters: it is a real track in this library, and under a
quality-first rule a 320 kbps copy of that 99.9 s excerpt would win.

## 4. Replacing the worse one, and taking it back

**Nothing is deleted. Ever.** A replacement moves the old file to a recycle bin and writes down why.

**Where: `.recycle/` at the library root**, not inside the album. An album folder can be deleted, and
a bin inside it would go with the thing it exists to protect against. One entry per replacement:

```
.recycle/<when>-<short hash>/
    audio.<ext>          the file that was replaced
    words.lrc            its sidecar, if it had one
    bin.json             everything needed to put it back
```

`bin.json` holds: the album's source id and the track's ref; the **plan track as it was**, whole; the
tags that were on the file; the reason (`replaced by a better candidate`, `pruned`, `deleted`); and
**both candidates' ranking numbers**, so that "why did it do that?" is answerable a month later
without re-deriving anything.

**Retention: never automatic.** No age cap, no size cap, no quiet emptying — the bin exists because
the program made a judgement the user may disagree with, and a bin that empties itself is a bin you
cannot rely on. `ytalbum config` reports its size, the UI shows it, and `ytalbum recycle empty`
needs the user to say so.

```
ytalbum recycle list                 # what is in there, why, and how big
ytalbum recycle restore <entry>      # put it back
ytalbum recycle empty [--older-than] # only ever explicit
```

**What restore does.** The file goes back; the plan track is restored from the snapshot; the
candidate that displaced it is kept in `candidates` but demoted, and **marked as refused for this
track**, so the next pass does not simply do it again — the same shape as `lyrics_rejected`
(§9, slice 27), which exists because a wrong answer that keeps coming back is worse than a wrong
answer. The sidecar is restored **unless the user has written lyrics since**, in which case theirs
stay and the restore says so.

**It is not `.originals/`.** That holds one untouched file per trimmed track so a trim can be re-cut
or undone; it is part of trimming and stays exactly as it is. The bin holds files that were
*superseded*. Different lifetime, different reason.

**`prune` and `delete` should route through it too.** Today `delete_track` unlinks the kept original
and `prune` leaves it behind (catalog E, phase 5). Sending both through the bin makes the rule one
sentence — *ytalbum never removes audio, it only moves it to the bin* — and fixes that inconsistency
on the way. That is a **behaviour change and needs its own DESIGN slice**, not a quiet extension.

## 5. Migration bill, on top of the boundary's

**Additive** — old plans load unchanged, P43's invariant holds:

- `candidates`, `chosen` on a track; `provider` on the plan (already in the boundary's bill)
- `refused_candidates`, the same shape as `lyrics_rejected`
- `.recycle/` at the library root: a new directory, nothing reads it unless asked

**Not additive:**

- routing `prune` and `delete` through the bin — a behaviour change, and the one thing here a user
  could be surprised by
- if identity ever moves off `video_id`, `.originals/<video_id>.<ext>` has to move with it; the
  cheap answer is to keep those filenames as the YouTube provider's names
- the `youtube_id` tag written into every audio file (from the boundary's bill) becomes wrong the
  moment a track's audio comes from somewhere else, and a full-library re-tag is the only fix

**Folder layout** otherwise does not change: `Album artist/Album/…` says nothing about where the
audio came from, which is why none of this touches it.

## 6. Order of work

1. **`candidates` + `chosen`, YouTube only.** Additive, ships before the boundary, folds in
   `source_override`, and makes everything below expressible.
2. **The recycle bin, with `prune`/`delete` routed through it.** Independent of sources entirely,
   fixes an existing inconsistency, and must exist *before* anything can replace a file.
3. **The Source boundary** itself (the other spike): `Ref`, capabilities, YouTube as one provider.
4. **Intake folder** as the second provider — the cheapest real test of the boundary.
5. **Ranking and replacement**, once two providers can offer the same track.
6. **Library-as-source**, then the **watchdog**, last: both need everything above to be settled.

1 and 2 are worth doing whatever is decided about the boundary.

## What this spike cannot know without running

- **Whether stream hashing is affordable.** 3946 tracks; the cost of `-f hash` per file was not
  measured, and if it is slow the pre-filter carries more weight than assumed.
- **Whether the 3 s band transfers.** It was measured on LRCLIB entries against YouTube audio
  (§9, slice 46), not on two encodes of the same recording, where the honest figure may be far
  tighter.
- **How often duplicates actually differ.** This library has exactly one `source_override` and one
  audio format (opus, 3946 of 3946), so it contains no evidence at all about ranking two real
  candidates. That evidence has to come from a real intake folder.
- **What a scan costs on a large library**, and whether `update` over a folder stays cheap when the
  folder is not small.
- Nothing here was prototyped. Every number above is read from existing plans, not produced by
  running any of this.
