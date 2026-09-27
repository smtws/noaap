# QA catalog

Hand-run checks from a user's point of view, aimed at the places where **features meet**:
trimming a track that has lyrics, renaming one that MusicBrainz matched, pruning an album whose
order you set yourself. The pytest suite covers the pieces; this covers the seams.

Derived from the code as of 2026-09-26 and kept up with it since (379 tests when it was written,
683 pytest plus 87 under node after the fixes it produced and the fourteen packages that followed;
246 albums in the reference library). Sections A–J are the original catalog; K onwards were each
added with the feature they cover, up to AG for v0.6.0.

## How to use it

Tick a box when a case passes and write the one line that proves it. A case that fails gets the
observed behaviour instead — that line is the bug report.

**Two halves, two directions.** The case sections run **oldest first**, A to AG, so that reading
straight through follows the software as it grew and a newcomer meets the lyrics editor before the
near-miss check. The **Results** table at the end runs **newest first**, because that is the half
you consult rather than read. Keep both as they are: a new section is appended with the next letter,
a new result is prepended as the newest row.

### What the classes mean

- **R** — reads the library, never writes to it. May still write *outside* it: the MusicBrainz
  and lrclib caches, the job history in a running server, `.parts/` scratch during a download.
- **M** — changes the library and can be undone: renames, retags, trims, lyrics, edits,
  relocations.
- **D** — deletes audio, or can overwrite something you wrote by hand.

**Markers** — ★ a combination the test suite does not exercise · ⚠ expected to fail

### Safety: use a scratch library, not a backup

An earlier version of this file claimed that copying the `.ytalbum.json` files was enough to
undo the **M** cases. **It is not.** Those cases rewrite tags inside audio files, rename and
move files and folders, create and delete `.lrc` sidecars, and cut audio. Restoring only the
plans would leave the plan describing files that no longer match it — a worse state than the
one you started from.

So: **every M and D case runs against a scratch library**, which is a directory that starts
empty and can be deleted at any point.

```sh
export QA=/tmp/ytalbum-qa
mkdir -p "$QA"

# seed it — about nine tracks; S9 and S10 are fetched later, by B8 and D3 alone
ytalbum fetch --library "$QA" 'https://www.youtube.com/watch?v=___ci9kmRc4'   # S1
ytalbum fetch --library "$QA" 'https://www.youtube.com/watch?v=ITVwzDlOg3M'   # S2
ytalbum fetch --library "$QA" 'https://www.youtube.com/playlist?list=PLfX9CI0JhSo6PJ4yWuk2Cn5HfvRwmOMrV'  # S3

# a second server, so the installed one keeps serving the real library untouched
ytalbum serve --library "$QA" --port 8799
```

Every command in an M or D case carries `--library "$QA"`; every UI action happens on
`http://127.0.0.1:8799`. `ytalbum config` is never used during QA — it would change the real
setup. Throwing the scratch library away is `rm -rf /tmp/ytalbum-qa` (ask first if that rule
applies to you).

**R cases run against the real library**, because several of them are about scale — 246 albums,
the jump rail, the ⏱ filter finding 11 albums out of 246.

Caches are shared with normal use by default, which keeps the load on MusicBrainz and lrclib
low. For a case that must reach the live service (J7–J9, I3), force a cold start with
`XDG_CACHE_HOME="$QA/cache"`.

### Specimens

| id | what it is | address |
|---|---|---|
| S1 | *Viva Vendetta (Official Video)*, 471 s — label suffix in the title, 4 minutes of film around a 3:50 song, no lyrics at that length | `watch?v=___ci9kmRc4` |
| S2 | *Viva Vendetta (Instrumental)*, 230 s, from the band's own channel — the same song without singing | `watch?v=ITVwzDlOg3M` |
| S3 | Feuerschwanz — *Sex Is Muss*, 7 tracks — holds three snippets of 83–104 s against songs of 215–251 s, and a track whose title keeps "(Summer Breeze 2016)" | `playlist?list=PLfX9CI0JhSo6PJ4yWuk2Cn5HfvRwmOMrV` |
| S4 | Sabaton — *Heroes (Track Commentary Version)*, 13 commentary clips carrying the album's track names | `playlist?list=OLAK5uy_mj-XiLHvQGkosFBvRsGnDX6mPhhW2YEo8` |
| S5 | Lord of the Lost — *OPVS NOIR Vol. 1 (Instrumental)*, 11 tracks, and MusicBrainz has the instrumental release too | `playlist?list=OLAK5uy_lqRyvQ-Tz6zW8YJkIb8AGJbU8NFb5Ygwc` |
| S6 | Lord of the Lost — *Judas*, the **audio** release: 56 tracks, 24 of them exactly 223 s | `playlist?list=OLAK5uy_kvKX_bTPTXGRmCQy3wcwy0UD2By9j5y2w` |
| S7 | Visions of Atlantis — *Delta*: MusicBrainz credits the release "Visions **Of** Atlantis" while the artist is "Visions of Atlantis" | `playlist?list=OLAK5uy_kOH7P46M_xmvvnVSUPNiMTvaivBBjn9vk` |
| S8 | Feuerschwanz — *Blöde Frage, Saufgelage* (on *Best Of*): lrclib holds 18 entries for it, 16 of them wordless stubs, one synced (#1948185) at 213 s. Used only as a **query**, never fetched | `playlist?list=OLAK5uy_lZouiZ8ft8t-95Br3K3al8ttP_N5d2hyk` |
| S9 | DOMINUM — *The Dead Don't Die*, 246 s: a second video **uploaded by the same channel as S1** (Napalm Records), carrying the same label ident at the front. Fetched only for B8 | `watch?v=dGO_sx4By28` |
| S10 | *Viva Vendetta*, 230 s — the **sung** album recording as its own audio upload, against MusicBrainz' 229.8 s. The canonical counterpart to S1's 471 s film. Fetched only for D3 | `watch?v=Z2UO4FsFGFM` |

Prefix each with `https://www.youtube.com/`.

### Order

R first (free), then M against the scratch library, then D. Never run a CLI job while a server
is working on the same library — nothing locks them against each other (G5).

---

## A. Acquisition and first contact

- [x] **A1 · R** — dry run over an album already held
  - do: `ytalbum fetch <S3> --dry-run`
  - expect: the plan is printed and nothing is written
  - invariant: the real library's plan and files are untouched
  - evidence: `.ytalbum.json` mtime before/after
  - note: the MusicBrainz cache *is* written; that is outside the library
  - **result 2026-09-26:** pass — sorted plan list identical, 269 folders before and after. My first attempt hashed **unsorted** `find` output and reported a phantom write

- [x] **A2 · R★** — search without picking
  - do: `ytalbum search "Feuerschwanz"`, answer "none"
  - expect: albums already in the library are marked as such
  - invariant: no folder appears
  - evidence: the listing; `ls ~/Music/YouTube\ Downloads/Feuerschwanz`
  - **result 2026-09-26:** pass — every held album marked `✓ in library`, exit 0, no folder created

- [x] **A3 · M** — the documented hand-editing path
  - do: `ytalbum plan <S3> --library "$QA"` → edit the plan (album name, swap two numbers) →
    `ytalbum download "$QA/<artist>/<album>"`
  - expect: your edits are what lands on disk
  - invariant: file names follow the edited values; `tracktotal` matches
  - evidence: file names; `mutagen` tags
  - **result 2026-09-26:** pass — hand-edited the plan (two tracks kept, numbers swapped, album renamed): folder relocated, files named from the edits, `tracknumber` 1/2 and `tracktotal` 2 in the tags

- [x] **A4 · M★** — a single from a label channel
  - do: the S1 fetch from the seed step
  - expect: album name *Viva Vendetta*, **not** *Viva Vendetta | Napalm Records*; `kind = single`
  - invariant: `drop_label` applies to album names, not only track titles
  - evidence: `plan.album`; the folder name
  - **result 2026-09-26:** pass — album *Viva Vendetta*, kind single, no label suffix

- [x] **A5 · M★** — take the audio out of the video stream
  - do: on S1, set `audio_choice` to `combined` (album view, or the `edit` action) and let it
    re-download
  - expect: the track arrives as `.m4a`, tagged through the MP4 atoms
  - invariant: `ext` flips; the cover and `©lyr` are written; the old `.opus` is removed
  - evidence: `mutagen` atom dump; the file suffix
  - note: this is how to reach the m4a path without hunting for a video that has no audio stream
  - **result 2026-09-26:** pass — the track came back as `.m4a` with MP4 atoms and the cover, and the old `.opus` was removed

---

## B. Trim, and everything it touches

- [x] **B1 · M** — cut the front
  - do: on S1, drag the start handle to 19.8 s, *save trim*
  - expect: the file is shorter; `.originals/___ci9kmRc4.opus` holds the untouched download
  - invariant: the original is byte-identical to the file before the trim
  - evidence: `ffprobe` duration; `sha1sum` against a copy taken beforehand
  - **result 2026-09-26:** pass — 470.9 s → 451.1 s, `.originals/___ci9kmRc4.opus` byte-identical to the pristine download

- [x] **B2 · M** — "end here" (regression, fixed 2026-09-26)
  - do: on S1, play, press *end here* around 408 s, adjust, *save trim*
  - expect: playback **stops on the mark** and stays on this track; the row's trim field fills
  - invariant: the save applies to the track you were editing, never the next one
  - evidence: the player title is unchanged; `trim_end` in the plan
  - **result 2026-09-26:** pass — stayed on the track, paused at 61.4 s, handle read *Song ends at 1:01*, mark unsaved

- [x] **B3 · M** — a saved trim still skips the outro
  - do: with B2 saved, play into the end mark
  - expect: it advances to the next track (or stops, if it is the last)
  - invariant: only *unsaved* marks stop playback
  - evidence: the player title
  - **result 2026-09-26:** pass — playing into a saved end advanced *Ketzerei* → *Hexenjagd*. Note: once saved, the file itself ends at the mark, so `ended` and the end-mark check coincide

- [x] **B4 · M** — undo a trim
  - do: clear both marks on S1, save
  - expect: restored from `.originals/`, byte-identical; `trimmed` back to `None`
  - invariant: clearing a trim is a restore, not a re-download — it works offline
  - evidence: `sha1sum`
  - **result 2026-09-26:** pass — clearing both marks restored the file byte-identical to the pristine download (`trimmed` back to `None`)

- [x] **B5 · M★** — play a trimmed track
  - do: play the track you trimmed in B1, from the album view
  - expect: the player loads `?o=1` (the original) and previews the cut itself
  - invariant: the head is cut once, not twice
  - evidence: `audio.src`; position ≈ `trim_start` two seconds in
  - **result 2026-09-26:** pass — the player requested `&o=1`, was served the 470.9 s original, and sat at 22.5 s after three seconds: the head is cut once

- [x] **B6 · M★⚠** — trim an `.m4a` track (needs A5)
  - do: set both marks on the m4a track from A5 and save
  - expect (suspected defect): `original_path` hardcodes `.opus` and `apply()` remuxes into
    `.trim.opus` with `-c copy`, so AAC into an Opus container should fail
  - invariant regardless: the playable file survives and the failure is reported on the track —
    never a truncated or silent file
  - invariant: the playable file survives and the failure is reported on the track
  - evidence: `track.error`; duration unchanged; ffmpeg stderr
  - **result 2026-09-26:** **FAIL, worse than predicted** — see the finding below. The trim read a stale `.opus` original left by an earlier format, wrote Ogg/Opus into the `.m4a`, and the tagger then raised an unhandled `MP4StreamInfoError`
  - **re-run after P1: pass** — the m4a trim produced a 112 s file that is still an MP4 container, the original was kept as `dGO_sx4By28.m4a`, no error

- [x] **B7 · M★** — change the audio source of a trimmed track
  - do: trim S1, then switch it to `combined`
  - expect: it re-downloads, `trimmed` resets, the marks re-apply to the new original
  - invariant: the trim points are kept while the audio underneath is replaced
  - evidence: `plan.trimmed`; duration
  - **result 2026-09-26:** **FAIL (deferred)** — switching a trimmed track to `combined` silently does not apply the trim (fresh 470.9 s file, `trimmed=None`); the **next** run applies it and corrupts the file exactly as in B6
  - **re-run after P1: pass** — switching the trimmed track back to opus and running the next pass applied the same trim in the opus container (112 s, ogg); nothing was corrupted

- [x] **B8 · M★** — one trim for a whole channel
  - do: fetch S9, then set a front trim on S1 and press ⇉
  - expect: S9 is trimmed to the same points, in its own album, keeping its own original
  - invariant: the rule keys on the **uploader**, not the artist — S1 and S9 are different
    bands sharing one label channel, while S2 is the same band on its own channel and must
    **not** be touched
  - evidence: all three plans' `trim_start`/`trim_end`; the job log
  - **result 2026-09-26:** pass on the rule — both *Napalm Records* tracks took the trim while the two *Lord Of The Lost* albums and the *xxFEUERSCHWANZxx* one were untouched, so it keys on the uploader, not the artist. It also exposed the persistence described below
  - **re-run after P1: pass** — the Napalm Records track took the trim and applied it, the Lord Of The Lost track stayed untouched, and no stale original got in the way

---

## C. Lyrics

- [x] **C1 · M** — idempotence
  - do: `ytalbum lyrics --library "$QA"` twice
  - expect: the second run asks nothing and retags nothing
  - invariant: a second pass must not rewrite a single tag
  - evidence: job log line counts
  - **result 2026-09-26:** pass — two runs in a row reported the same `6 none, 2 synced, 1 instrumental` and retagged nothing

- [x] **C2 · M★** — trim a track that has synced lyrics, to a length that still matches
  - do: on S3 track 4 (*Sex is Muss*, 285 s of file against a 217.6 s song, synced lyrics),
    trim the end towards ~218 s
  - expect: the words survive or are re-matched against the new length
  - invariant: timestamps are **never** shifted by the trim; a front trim makes the track be
    looked up again, an end trim need not
  - evidence: the first timestamp in the `.lrc`; `lyrics_id` before/after
  - **result 2026-09-26:** pass — trimming to 218 s did not shift a single timestamp; the words were **re-matched** to the lrclib entry that fits the new length (#1949288 instead of #30840489, first line 00:27.69 against 00:27.71)

- [x] **C3 · M★** — trim to a length nothing matches, then clear it
  - do: trim S3 track 4 to about 250 s — a length no entry has — then clear the trim again
  - expect: the words go, then come back when the length is a known one again
  - invariant: no stale `.lrc` beside a track whose verdict is "none"
  - evidence: `plan.lyrics`; sidecar presence
  - **result 2026-09-26:** pass — at 250 s nothing matched and the words went; clearing the trim brought the file back to 284.7 s and the original entry (#30840489) with it

- [x] **C4 · M** — rename a track that has lyrics
  - do: rename a track that has a `.lrc` in the album view, save
  - expect: the `.lrc` follows the audio and the tag is rewritten from it
  - invariant: `mbid` **and** `mb_length` are both cleared
  - evidence: file names; the `LYRICS` tag; the plan
  - **result 2026-09-26:** pass — the `.lrc` followed the rename, the tag was rewritten from it, and `mbid` **and** `mb_length` were both cleared

- [x] **C5 · M★** — lyrics you wrote yourself
  - do: write a `.lrc` by hand, set `provenance["lyrics"] = "user"`,
    `ytalbum lyrics --library "$QA" --refetch`
  - expect: your file is untouched
  - invariant: a user provenance on lyrics outranks everything, `--refetch` included
  - evidence: `sha1sum` before/after
  - **result 2026-09-26:** pass — a hand-written sidecar marked `user` came through `--refetch` byte-identical, and the tag carries those words
  - **re-run after P2:** pass — still byte-identical (`e2bda878`), status `synced`, provenance `user`

- [x] **C6 · D★** — a hand-written `.lrc` **without** the provenance mark
  - do: write a `.lrc` by hand, leave the provenance alone, then `ytalbum lyrics --library "$QA" --refetch`
  - expect (honest): it is overwritten. Decide whether an unmarked sidecar should be protected
  - invariant: whatever is decided, the plan and the sidecar must not disagree afterwards
  - evidence: the sidecar diff
  - **result 2026-09-26:** **confirmed as written** — the unmarked sidecar was overwritten by `--refetch` (`f6ed83…` → `b20f39…`). Behaviour is as predicted; whether it should be is the product decision
  - **decided and changed in P2** (DESIGN.md §9.21): a sidecar is recognised by its bytes, so the
    mark no longer has to be set by hand. Three re-runs, all pass:
    - **C6a** (edit since the last pass, tag still disagrees): kept, marked `user`, status `synced`,
      tag rewritten from the kept file, no lrclib request needed
    - **C6b** (older edit: the tag was already rewritten from it, so it *agrees* — the common case):
      lrclib was asked what entry `5073938` holds, the answer differed, file kept, marked `user`,
      status `synced`
    - **C6c** (a sidecar we wrote ourselves): replaced by `--refetch` and **not** mistaken for the
      user's — `lyrics_sha` recorded (`ecb495c0…`), provenance untouched

- [x] **C7 · M** — the file is the source of truth
  - do: delete a `.lrc`, then `ytalbum download "$QA/<album>"`
  - expect: the tag loses the words too
  - invariant: the tag never outlives the file it was copied from
  - evidence: `mutagen` tag absent
  - **result 2026-09-26:** pass — deleting the `.lrc` removed the words from the tag on the next run. Note: `plan.lyrics` still reads `synced`, so the status outlives the words it describes
  - **re-run after P2:** pass, and the note is closed — the status went `synced` → `none`, the tag is
    gone and `lyrics_sha` was cleared. `ytalbum lyrics` also stopped skipping albums with nothing to
    look up, which was the path on which a deleted sidecar went unnoticed

- [x] **C8 · M★** — an instrumental never borrows the singer's words
  - do: run the lyrics pass over S2 (*Viva Vendetta (Instrumental)*, 230 s — the same length as
    the sung recording)
  - expect: verdict "no words"; no `.lrc`
  - invariant: the marker is read from the **track** title, never the album's
  - evidence: `plan.lyrics`; the folder
  - **result 2026-09-26:** pass, and it happened at fetch time — S2 arrived as *Viva Vendetta (Instrumental)* with verdict `instrumental` and no sidecar, although the sung recording is the same 230 s

- [x] **C9 · R** — the lyrics panel
  - do: in the real library, open an album, click ♪, click a line, let it play on
  - expect: it seeks there; the line being sung is marked as the song plays
  - invariant: the box scrolls, the page does not
  - evidence: `audio.currentTime`; the `.now` class; `window.scrollY` across a minute
  - **result 2026-09-26:** pass — clicked 1:28 → seek 89.6 s, that line marked, 1:32 marked six seconds later, the box scrolled and the page did not

---

## D. Length signals

- [x] **D1 · R** — the ⏱ filter (real library)
  - do: press the ⏱ button in the library head, then press it again
  - expect: exactly the flagged albums, and the full grid again when toggled off
  - invariant: filtering changes what is shown, never what is stored
  - evidence: card count against `album_length_flag`
  - **result 2026-09-26:** pass — 12 of 246 while filtered, 246 after toggling off

- [x] **D2 · M★** — trim an overlong track until its chip clears
  - do: on S3 track 4 (+67 s), trim towards the known 217.6 s
  - expect: the amber chip turns muted once the gap is under 20 s
  - invariant: the album's badge stays `3 clips` — the three snippets are *too short*, and no
    trim can lengthen them. Only replacing those files clears that badge
  - evidence: `/api/state` for the album; the chip's class in the row
  - **result 2026-09-26:** pass — trimming *Moralisch* from +28 s to 0 s turned its chip muted, and the album kept its `3 clips` badge exactly as the case predicts

- [x] **D3 · D★** — replace a video edit with the canonical audio
  - do: in the scratch library, delete S1 (471 s of film around the song) and fetch S10, the
    same recording as an audio upload
  - expect: the gap against MusicBrainz collapses from +241 s to about 0, and the chip clears
  - invariant: it must be the **same song**, not another variant — S2 is the instrumental and
    would prove nothing about replacing an edit with its release
  - evidence: `length_gap` before and after; `mb_length` unchanged at 229.8 s
  - class: destructive because it deletes an album, even a scratch one
  - **result 2026-09-26:** pass — replacing the 451 s video edit with the canonical 230 s audio collapsed the gap from +221 s to 0 s

- [x] **D4 · R★** — an album nobody has a length for
  - do: open an album whose tracks MusicBrainz and lrclib both lack (any live or fan compilation without a release match)
  - expect: no badge and no chips — silence rather than a false "0:00"
  - invariant: no reference means no claim: neither a chip nor a badge
  - evidence: the album view
  - **result 2026-09-26:** pass — Schandmaul *Wie Pech und Schwefel*: 15 rows, 0 chips, no badge

---

## E. Update, merge, prune

- [x] **E1 · R** — `ytalbum update --dry-run --library "$QA"`
  - do: `ytalbum update --dry-run --library "$QA"`
  - expect: reports only; unchanged albums cost one request each
  - invariant: no plan is written, no folder moves
  - evidence: job log; plan mtimes
  - note: run it against the scratch library — `update` has no `--artist`, so on the real one
    it would walk all 246 albums
  - **result 2026-09-26:** deferred to the M pass — an empty scratch library proves nothing, and the real one would cost 246 requests
  - **result 2026-09-26:** pass — report only, plan mtimes unchanged; 2 of 5 albums took the one-request path

- [x] **E2 · M★** — a user order survives an update
  - do: reorder tracks of S3 in the album view, save, then `ytalbum update --library "$QA"`
  - expect: your numbers stand; a video that appeared since joins the **end**
  - invariant: `provenance["order"] == "user"`
  - evidence: the numbering before/after
  - **result 2026-09-26:** pass — a hand-set order survived `update --deep` unchanged, with `provenance.order == user`

- [x] **E3 · M★** — a disc split survives an update
  - do: set discs 1/2 on S3, save, update
  - expect: the split stands and each disc counts from 1
  - invariant: a disc split is the user's, so the source may not undo it
  - evidence: `plan.tracks[].disc`
  - **result 2026-09-26:** pass — a 4/3 disc split survived `update --deep`, file names carry `1-01`…`2-03`, each disc counting from 1

- [x] **E4 · M★** — user fields against a deep update
  - do: edit a title and an artist on S3, then `ytalbum update --library "$QA" --deep`
  - expect: neither is overwritten by MusicBrainz
  - invariant: a user field is never overwritten, however confident MusicBrainz is
  - evidence: the `provenance` map
  - **result 2026-09-26:** pass — an edited title and artist both came through `update --deep` untouched

- [x] **E5 · D** — prune
  - do: mark a track `in_source: false` in the scratch plan, then `ytalbum prune "$QA/<album>"`
  - expect: only that track's files are deleted; the rest renumber and retag
  - invariant: its `.lrc` and `.originals/` entry go with it
  - evidence: the directory listing; `tracktotal`
  - **result 2026-09-26:** pass with one gap — the pruned track's audio and `.lrc` went and the rest retagged to `tracktotal` 6, but its **`.originals/` copy stayed behind**. `delete_track` removes the original; `prune` does not, so a pruned trimmed track leaves a full-size orphan
  - **re-run after P1: pass** — the pruned track's `.originals` copy went with it
  - **re-run after P3: pass** — a trimmed track pruned from the scratch album took its audio, its
    `.lrc` and `ayFhgxdRV-Q.opus` in `.originals/` with it

- [x] **E6 · D★** — prune an album whose order you set
  - do: set a custom order on S3, save, mark a track `in_source: false`, then prune it
  - expect (open question): gaps close, so your numbers change. Confirm that is wanted, or make
    prune leave a user order alone
  - invariant: whatever is decided, the tracks keep their relative order
  - evidence: numbering before/after
  - **result 2026-09-26:** **observe-only, as instructed (R-002); album restored afterwards.** The renumbering is decided by discs, not by provenance: on a **multi-disc** album prune left the gap (1, 2, 4 …), on a **single-disc** album it renumbered 1..n and closed it, rewriting the numbers the user chose. Relative order was preserved in both. Also observed: collapsing a disc split back to one disc re-sorts by (disc, number) and **reshuffles the user's arrangement**
  - **decision (P3, DESIGN.md §9.22):** the gap closes on **every** album, per disc, and the user
    order flag stays set. What the flag protects is the sequence against the *source*, and a
    deletion the user asked for is not the source; `delete_track` has always renumbered. What must
    never change is the relative order — and that is now what the code is built on, rather than
    the numbers.
  - **run to a conclusion after P3 (no longer observe-only):** pass in all three shapes.
    Single disc: dropping track 3 of 5 left the arrangement intact, numbers 1…4 with no gap, flag
    still `user`, and the victim's audio, `.lrc` and kept original gone. Multi-disc: dropping 1-02
    of a 2/2 split left `{disc 1: [1], disc 2: [1, 2]}` — closed per disc, order and flag kept
    (this is the `1, 2, 4 …` case). Split/merge round trip: a reversed arrangement survived the
    split, and collapsing it back to one disc returned exactly the same order, numbered 1…n, with
    the file names following and no `1-01` prefix left behind

- [x] **E7 · M★** — a spelling that differs from the library's
  - do: in the scratch library, edit S1's album artist to `LORD OF THE LOST` (which marks it
    yours), then fetch S2
  - expect: S2 lands in that same folder under your spelling — one artist folder, not two
  - invariant: a spelling you chose outranks MusicBrainz' when the library is harmonised
  - evidence: `ls "$QA"`; both plans' `albumartist`
  - **result 2026-09-26:** **fails as written** — a fetch does not unify the spellings: seeding produced `LORD OF THE LOST/` and `Lord Of The Lost/` side by side, and only `repair` merged them (finally onto the MusicBrainz spelling). See the phase-1 finding
  - **re-run after P4 (DESIGN.md §9.23): pass in both directions.** Shouting into a library that
    spells it properly: the fetched album adopted `Lord of the Lost`, logged one line, and the
    `LORD OF THE LOST/` folder was gone — one folder. Into a spelling the user chose for *another*
    album: the fetched album adopted `LORD OF THE LOST`, leaving `Lord of the Lost/` only for the
    album the user had not touched. Better evidence arriving: the library's spelling was kept, one
    hint named both spellings and pointed at `ytalbum repair`, and no other album was renamed.
    Following the hint's advice converges in one pass: repair decides each artist key once, before
    it renames anything, from every candidate the library holds — so three scratch albums in three
    spellings all moved onto the MusicBrainz spelling in one run, and the run after it had nothing
    to do (it needed two passes when the decision was made album by album)

---

## F. Deletion (scratch library only)

- [x] **F1 · D** — delete one track
  - do: press ✕ on a track in the album view and confirm
  - expect: audio, `.lrc` and `.originals/` entry all go; the rest renumber and retag
  - invariant: no other track loses a file; only the numbering and totals change
  - evidence: the directory listing; the plan
  - **result 2026-09-26:** pass — audio, `.lrc` and `.originals/` copy all went, the rest renumbered and `tracktotal` retagged to 6

- [x] **F2 · D** — delete an album holding a file you put there
  - do: drop a `notes.txt` into a scratch album, then delete the album
  - expect: ytalbum's files go, your file and the folder stay, and the log says so
  - invariant: ytalbum only deletes what it wrote
  - evidence: the folder contents; the log line
  - **result 2026-09-26:** pass — ytalbum's files went, my `notes.txt` and the folder stayed, and the log named the file it kept

- [x] **F3 · D★** — delete then re-fetch the same source
  - do: delete a scratch album, then fetch the same URL again
  - expect: a clean album with no leftovers from the previous copy
  - invariant: a fresh fetch starts from nothing — no orphan `.lrc`, no stale original
  - evidence: the file count; a fresh plan
  - **result 2026-09-26:** pass — the re-fetch came back with one track, a fresh plan and no stale `.originals/`; the user's file was still there

---

## G. Jobs, concurrency, lifecycle

- [x] **G1 · M** — cancel a running fetch
  - do: start the S6 fetch (56 tracks) on the scratch server, cancel after a few tracks
  - expect: it stops at the next safe point; the plan stays consistent; re-running resumes
  - invariant: a cancelled job leaves a plan that describes the files on disk
  - evidence: job state; a second run completes the album
  - **result 2026-09-26:** pass — cancelling a 56-track fetch left it `cancelled` with 3 done tracks, each with its file, one `.parts` leftover; resuming took it to 28 with no gaps

- [x] **G2 · M★** — queue a lyrics job during a fetch
  - do: start the S6 fetch on the scratch server, then press *Fetch lyrics* on another album
  - expect: both are write-lane jobs, so they serialise
  - invariant: they never interleave on one plan file
  - evidence: job start/finish times
  - **result 2026-09-26:** pass — the lyrics job sat `queued` on the write lane while the fetch ran, then ran on its own

- [x] **G3 · R★** — search during a fetch
  - do: start a fetch, then type an artist name into the search box
  - expect: the search answers straight away on its own lane
  - invariant: a read job never waits for a write job
  - evidence: job lanes in `/api/state`
  - **result 2026-09-26:** deferred to the M pass — needs a write job in flight
  - **result 2026-09-26:** pass — a search answered on the read lane in 20.7 s while the fetch kept running

- [x] **G4 · R** — restart while busy *(limitation of the case, not of the software)*
  - do: with a scratch job running, `ytalbum service restart`
  - expect: it refuses and says why, unless given `--force`
  - invariant: the refusal is the default; `--force` is the deliberate way past it
  - evidence: exit code; the message
  - note: needs an M job in flight to be meaningful; the restart itself writes nothing
  - **result 2026-09-26:** deferred to the M pass — the refusal only triggers on a *write* job; a search deliberately does not block a restart
  - **result 2026-09-26:** **not executable under the run's constraints** — the refusal only triggers on a *write* job, and the busy check always targets the installed service on the real library, which is read-only here. Observation: `service restart --port N` accepts the flag and ignores it; `restart()` calls `busy()` with no port
  - **recorded as a limitation of the case (P6):** it cannot be run without a write job on the
    *installed* service, i.e. against the real library, which every pass of this catalog forbids.
    The refusal itself is covered offline (`test_restart_refuses_while_a_job_runs`). The flag
    observation was a real fault and is fixed: `--port` and `--idle-exit` only describe the units,
    so outside `install` they are now refused with a message and exit 2 instead of being ignored

- [ ] **G5 · M★⚠** — CLI and server writing at once
  - do: start a fetch on the scratch server, then run `ytalbum lyrics --library "$QA"` in a
    terminal against the same album
  - expect (suspected gap): nothing locks the two processes, so the last writer of
    `.ytalbum.json` wins. Decide between a lock file and documenting the rule
  - invariant: whatever is decided, a plan must never be left describing files that do not exist
  - evidence: the plan afterwards against each job's log

- [x] **G6 · R** — idle exit
  - do: `ytalbum serve --library "$QA" --port 8799 --idle-exit 60`, leave it alone
  - expect: it exits only after a minute with no requests **and** no jobs
  - invariant: a job in flight keeps the server alive past its idle timeout
  - evidence: process lifetime
  - **result 2026-09-26:** pass — log reads `idle for 60s, stopping`, port free. `pgrep -f` matched its own shell and claimed the opposite: check the **port**

---

## H. Web UI and PWA

- [x] **H1 · M★** — an open editor during a download
  - do: open an album on the scratch server, then start a job that changes it
  - expect: the editor stays where it is; focus does not drag the viewport
  - invariant: a library refresh never moves the viewport
  - evidence: `window.scrollY` across a library refresh
  - class: the UI part is read-only, but triggering it needs a mutating job
  - **result 2026-09-26:** pass — 14 samples over 21 s of an active download: the scroll position never moved off 453 and the editor stayed open

- [x] **H2 · R** — filter, then "play matches" (real library)
  - do: filter the library for a song title, press *play matches*
  - expect: the matching songs play across albums, in grid order
  - invariant: playing from a filter plays what the filter showed, in that order
  - evidence: the queue
  - **result 2026-09-26:** pass — 3 cards, button `▶ Play 3 tracks`, queue held exactly those three

- [x] **H3 · R** — the jump rail and back-to-top (real library, 246 albums)
  - do: press a letter in the jump rail, then the back-to-top button
  - expect: the rail spans the viewport; a jump lands with the card fully visible
  - invariant: a jump lands with the card fully visible under the sticky header
  - evidence: scroll offsets
  - **result 2026-09-26:** pass — rail 864 px of a 1080 px viewport; jump to S landed Sabaton at top 88 against a header bottom of 65; back-to-top settled at 0

- [x] **H4 · R★** — reload after a restart
  - do: restart the scratch server, reload its page
  - expect: the new content-hashed `app.js` loads; no stale UI from the service worker
  - invariant: a restart never serves a stale `app.js` from the service worker
  - evidence: the asset URL in the DOM
  - **result 2026-09-26:** pass — `/` is `no-store` and cites the on-disk hashes (app.js `aa3c375da3`, style.css `361034385a`); assets are `no-cache`; the worker is network-first and skips `/api/*`

- [x] **H5 · R★** — server gone
  - do: stop the scratch server with its page open
  - expect: the offline banner appears and clears when the server returns
  - invariant: the page keeps working as a viewer while the server is away
  - evidence: the banner
  - **result 2026-09-26:** pass — banner within seconds of the server dying, page still usable, cleared on recovery; console held only `ERR_CONNECTION_REFUSED`

- [x] **H6 · R★** — media keys with an unsaved trim
  - do: set an end mark without saving, press the media key for the next track, then for the
    previous one; afterwards press ▶ on the album card to start it afresh
  - expect (specified from the code, confirm it holds): going next and back **keeps** the mark,
    because the queue holds it; playing the album afresh rebuilds the queue from the plan and
    the mark is gone. A reload loses it too
  - invariant: nothing reaches the disk without *save trim* — in every one of those paths the
    plan is byte-identical
  - evidence: the end handle after each step; `sha1sum` of `.ytalbum.json` throughout
  - **result 2026-09-26:** pass — next+previous kept the 31.09 s mark and the plan's sha; replaying the album dropped it. The handle stays *visible* either way (it then marks the track end)

---

## I. Degraded modes

- [x] **I1 · M** — without MusicBrainz or lrclib
  - do: `ytalbum fetch <S2> --library "$QA" --no-mb --no-lyrics`
  - expect: it downloads and tags from YouTube's data alone
  - invariant: both lookups are optional; the download path does not depend on them
  - evidence: provenance in the plan; no `.lrc`
  - **result 2026-09-26:** pass — `mbid` absent everywhere, every provenance `yt_title`, no `.lrc`

- [x] **I2 · M★** — lyrics switched off for the run
  - do: the same fetch with `--no-lyrics` only
  - expect: no lrclib traffic at all
  - invariant: switching lyrics off for a run leaves the configured setting alone
  - evidence: the lyrics cache file's mtime
  - note: `config --lyrics off` would change the real setup — use the flag
  - **result 2026-09-26:** pass — the lyrics cache file was not touched by the fetch

- [x] **I3 · M★** — the network drops mid-lookup
  - do: `XDG_CACHE_HOME="$QA/cache" HTTPS_PROXY=http://127.0.0.1:9 ytalbum lyrics --library "$QA"`
    — an empty cache and a dead proxy make every request fail, with no root and no cable to pull
  - expect: the failure is transient — the status stays unset so the track is asked again
  - invariant: a network error is never recorded as "none"
  - evidence: the plan afterwards; a re-run finds the words
  - **result 2026-09-26:** pass — with a cold cache behind a dead proxy, all five tracks stayed unset and **none** was recorded as `none`

- [x] **I4 · M★** — no ffmpeg
  - do: run a trim with `ffmpeg` off `PATH`
  - expect: it fails loudly, on that track
  - invariant: the audio file is never damaged; the original in `.originals/` is intact
  - evidence: `track.error`; the duration is unchanged
  - class: `apply()` copies the file into `.originals/` before ffmpeg runs, so this writes
  - **result 2026-09-26:** **invariant holds, expectation fails** — the audio was untouched (83.1 s before and after, no exception), but the failure was **not loud**: `error=None` in the plan, nothing in the job log, and a `trim_end` advertised that never happened. The pending trim was then applied silently on a later run once ffmpeg was back
  - **re-run after P1: pass** — audio untouched, `could not trim: … 'ffmpeg'` recorded on the track, and a `trim failed` event reached the job log

---

## J. Version markers and matchers, end to end

Today's failures, each as a case. All but J10 need no downloads: `--dry-run` and `plan` produce
the metadata, and the lyrics matcher can be asked directly.

- [x] **J1 · R★** — a release of other recordings is not our album
  - do: `ytalbum fetch <S4> --dry-run`
  - expect: the album stays *Heroes (Track Commentary Version)*; no MusicBrainz release is
    accepted for it
  - invariant: `core()` may drop the brackets, but a version marker must match
  - evidence: the printed plan; `mbid` absent
  - **result 2026-09-26:** pass — album stayed *Heroes (Track Commentary Version)*, provenance YTM, no release accepted

- [x] **J2 · M★** — the marker finds the *right* release
  - do: `ytalbum plan <S5> --library "$QA"`
  - expect: the album keeps "(Instrumental)" **and** matches MusicBrainz' instrumental release
  - invariant: a version marker narrows the match, it does not only block it
  - evidence: `plan.album`; `plan.mbid` resolves to a title containing "(Instrumental)"
  - **result 2026-09-26:** pass — album kept *(Instrumental)* and matched the MusicBrainz release **of the same name**; no audio downloaded

- [x] **J3 · R★** — an edition marker is still normalised
  - do: `ytalbum fetch <S6> --dry-run`
  - expect: YouTube's "(Deluxe Version)" becomes MusicBrainz' "Judas (Deluxe Digital Edition)"
  - invariant: editions hold the same recordings; versions do not
  - evidence: the printed album name
  - **result 2026-09-26:** pass — YouTube's *(Deluxe Version)* became MusicBrainz' *Judas (Deluxe Digital Edition)*

- [x] **J4 · R★** — a credit's typography is not the artist's name
  - do: `ytalbum fetch <S7> --dry-run`
  - expect: album artist *Visions of Atlantis* although the release credit shouts "Of"
  - invariant: only case and punctuation may be corrected this way
  - evidence: the printed plan
  - **result 2026-09-26:** pass — album artist *Visions of Atlantis* against a release credited *Visions Of Atlantis*

- [x] **J5 · R★** — a label is not part of an album name
  - do: `ytalbum fetch <S1> --dry-run`
  - expect: *Viva Vendetta*, not *Viva Vendetta | Napalm Records*
  - invariant: the label belongs to the uploader, never to the album
  - evidence: the printed plan
  - **result 2026-09-26:** pass — album *Viva Vendetta*, kind single. Note: a dry run prints the artist **before** harmonisation (*LORD OF THE LOST*), so the preview is not the outcome
  - **re-run after P4: the note is closed** — harmonisation now runs in the `--dry-run` path too
    (read-only, and guarded for a dry run with no library), so the preview prints the artist and
    folder the fetch would write

- [x] **J6 · M★** — a rejected recording leaves no length behind
  - do: `ytalbum plan <S3> --library "$QA"`, look at *Ketzerei (Summer Breeze 2016)*
  - expect: the title keeps its bracket group, `mbid` is absent, and `mb_length` is absent too
  - invariant: a length belongs to the recording it was read from
  - evidence: the plan JSON
  - **result 2026-09-26:** pass — *Krieger des Mets (Wacken 2016)* and *Ketzerei (Summer Breeze 2016)* both have `mbid=None` **and** `mb_length=None`, while *Ringelpietz (mit Anfassen)*, whose brackets are part of the title, keeps both

- [x] **J7 · R★** — a wordless entry does not end the search
  - do: ask the matcher directly for S8's track 5 at 211.7 s, with a cold cache
  - expect: lrclib #1948185 (213 s, synced), not one of the 16 wordless stubs
  - invariant: lrclib's "instrumental" means nobody submitted words, not that there are none
  - evidence: the returned id and the first line of the text
  - **result 2026-09-26:** pass — #1948185, synced, *Mein lieber Herr Hauptmann…*, from a cold cache

- [x] **J8 · R★** — an instrumental refuses the sung words
  - do: ask the matcher for *Viva Vendetta (Instrumental)* at 230 s
  - expect: no words, although the sung recording is the same length
  - invariant: only the title can separate an instrumental cut from the sung one
  - evidence: `status`; `text is None`
  - **result 2026-09-26:** **the case was wrong, not the code** — lrclib's search for a title containing *(Instrumental)* returns 0 rows, so `get()` answers `None`; the *no words* verdict is made by `update_track`. Assert end to end, not at the client
  - **corrected expectation (P5, DESIGN.md §9.24):** no words **and** a length reference. Asking
    with the marker in the query is what returned nothing, so the marker is now stripped from the
    query alone. **Re-run from a cold cache against live lrclib: pass** — the query asked is
    *Viva Vendetta*, `text is None`, status `none`, `length` 230.0; the same search *with* the
    marker still returns 0 rows, which is the fault this closes

- [x] **J9 · R★** — a refused length is still remembered
  - do: ask the matcher for *Viva Vendetta* at 471 s
  - expect: no words, but a `length` comes back as the second opinion. It is the candidate
    **closest to our file**, which for a padded file is the least representative one: 248 s
    here, where eight of the nine entries say 230 s
  - invariant: that is what feeds the length chip for tracks MusicBrainz does not know
  - evidence: the returned object
  - **result 2026-09-26:** **the case was wrong, and found something** — the near miss is the candidate closest to *our* 471 s file (248 s), not the 230 s that eight of nine entries agree on. Expectation corrected below; the selector itself is now an open question
  - **decided and fixed (P5, DESIGN.md §9.24):** the reference is the consensus of the same-artist
    candidates — the commonest whole second, the median of the tied values on a tie. **Re-run from
    a cold cache against live lrclib: pass** — the nine candidates came back as 229.0, 229.8,
    229.8, 230.0 ×5 and 248.0, and the kept length is 230.0

- [x] **J10 · R★** — a repeated length is real data
  - do: in the real library, look at S6's 24 tracks of 223 s
  - expect: every one keeps its `mb_length`; the album carries no length flag
  - invariant: repetition is not evidence of a bad reference — the rule that assumed so was
    reverted on 2026-09-26
  - evidence: the plan; `album_length_flag` is `None`
  - **result 2026-09-26:** pass — 23 of 56 tracks share 222.1 s, every one kept, flag `None`

---

## K. The lyrics editor (P8, DESIGN §9.26)

Added 2026-09-26 with the editor itself. Run against a freshly seeded scratch library (S1 and S3)
on a scratch server at 8799, driven through the real page with Playwright; the sidecar, the tag
and the plan were read on disk after each step.

- [x] **K1 · M** — write lyrics for a track that has none
  - do: open S3, click the faint ♪ on track 1 (*Ketzerei*, status `none`), press "Write lyrics",
    type two timestamped lines, Save
  - expect: the words are on disk, marked as yours, and in the tag
  - invariant: nothing is looked up — an editor that asked LRCLIB could answer a save by
    replacing what was just typed
  - evidence: the `.lrc`; `lyrics`/`lyrics_sha`/`provenance` in the plan; the `LYRICS` tag
  - **result:** pass — a ♪ is now offered on every downloaded track, faint when there are no words.
    The panel said "no words yet" and offered "Write lyrics" with no Delete. After Save:
    `.lrc` written with one trailing newline, `lyrics=synced`, `lyrics_sha=5e1fb9f6e6a231de`,
    `provenance.lyrics=user`, tag identical to the file, and the job log read
    "Save your lyrics for Ketzerei"

- [x] **K2 · M** — edit LRCLIB's words
  - do: on track 4 (*Sex is Muss*, LRCLIB #30840489, 42 lines), press Edit, change one line, Save
  - expect: the file is yours from then on, with the edit in the tag too
  - invariant: the panel shows who owns the words, immediately
  - evidence: the panel header; the `.lrc`; the tag
  - **result:** pass — the textarea opened prefilled with all 42 lines; after Save the panel came
    back at once showing the edited line and a **yours** badge in place of the lrclib link, and on
    disk `provenance.lyrics=user`, a new `lyrics_sha`, the tag equal to the file. `lyrics_id` is
    deliberately kept (it is how P2 recognises a file as ours)

- [x] **K3 · M** — clear from the editor
  - do: Edit that track again, press Delete
  - expect: the `.lrc` and the tag go, the status becomes `none`, the mark is dropped
  - invariant: a clear is the same act as deleting the file by hand (§9.21)
  - evidence: the folder; the plan; the tag
  - **result:** pass — sidecar gone, `lyrics=none`, `lyrics_sha=None`, no `provenance.lyrics`, tag
    absent; the row's ♪ went faint and the panel offered "Write lyrics" again, with no wait for the
    poll. One blemish found and fixed here: the header still cited `lrclib #30840489` beside
    "no words yet", because the id is kept — the link is now shown only when there are words

- [x] **K4 · R** — a save while a pass holds the album
  - do: start a `--refetch` of S3, then POST a save for one of its tracks
  - expect: refused, with the job named; nothing written
  - invariant: a pass retags from the sidecar, so the two must not interleave
  - evidence: the status code and message; the file
  - **result:** pass — `HTTP 400: “Look up all lyrics of Sex Is Muss” is working on this album —
    wait for it, then save again`, and the user's file was untouched. Known gap, stated in
    DESIGN §9.26: a `fetch` is named by its URL and learns the album id while it runs, so the
    check cannot see it

- [x] **K5 · M** — what a later lyrics run does to both
  - do: after K1 and K3, run `--refetch` over the album
  - expect: the words written in the editor are kept; the cleared track gets LRCLIB's back
  - invariant: the mark protects a file, and a clear gives it up (§9.21)
  - evidence: both sidecars and both provenance entries
  - **result:** pass — *Ketzerei* still reads `[00:12.00] Ketzerei, written by hand` with
    `provenance.lyrics=user`; *Sex is Muss* came back with LRCLIB's own first line (without the K2
    edit) and no mark

- [x] **K6 · R** — a track with no file, and a track from another album
  - do: POST a save for a track in state `pending`, and for a `video_id` of a different album
  - expect: refused, with a message that says which
  - invariant: `sidecar_path` is the only path builder; nothing is written outside the album folder
  - evidence: the status codes
  - **result:** covered offline in `test_web.py` (400 "no file yet", 400 "no such track"), since the
    scratch album has every track downloaded

- [x] **K7 · R** — an open panel survives a refresh
  - do: keep a panel open while a job runs and the page polls
  - expect: the words stay on screen
  - invariant: a rebuild of the table must not close what you are reading
  - evidence: the panel after a poll-driven re-render
  - **result:** **found as a defect and fixed in this package** — the first save wrote the file
    correctly but the panel vanished, because every re-render rebuilt the table and dropped the
    row. Open panels are now remembered and restored after any render, which also stops a poll
    closing lyrics you are reading while a download runs

### K8–K11, the per-track actions (P9, DESIGN §9.27)

Run 2026-09-26 against a scratch library seeded fresh inside the session scratchpad (S3 only),
server on 8799, through the real page.

- [x] **K8 · M** — reject a match, and prove it stays rejected
  - do: on S3 track 4 (*Sex is Muss*, LRCLIB #30840489), press "Not these words"; then press
    "Look up again" on the same track
  - expect: the words go; the rejected entry is never taken again, even by an explicit new lookup
  - invariant: rejecting says "that entry is not this recording", which stays true; deleting the
    file only said "not now"
  - evidence: `lyrics_rejected` in the plan; the second job's log line; the panel
  - **result:** pass — after the reject: `lyrics=none`, `lyrics_id=None`, `lyrics_rejected=[30840489]`,
    sidecar and tag gone, the row's ♪ faint, and "Not these words" no longer offered (nothing left to
    reject). The following "Look up again" logged *"Sex is Muss: nothing lrclib has fits this
    recording"* — the only entry LRCLIB has for it stayed out. `lyrics_length` (218 s) is kept, so
    the ⏱ reference survives the rejection

- [x] **K9 · M** — look one track up again
  - do: on track 7 (*Moralisch*, LRCLIB #28467509), press "Look up again"
  - expect: that track alone is asked about, and the sidecar and tag are rewritten from the answer
  - invariant: no other track of the album is looked up
  - evidence: the panel header before and after; the job log
  - **result:** pass — header unchanged at `· lrclib #28467509` with the words rewritten, job labelled
    "Look up the lyrics of Moralisch (höchst verwerflich)". The offline tests cover the case where a
    track that had none gains words, since every track of this album had already been looked up

- [x] **K10 · M** — neither action touches words of the user's
  - do: write lyrics for track 1 in the editor, then POST both actions for it
  - expect: refused, and the panel offers only Edit
  - invariant: a user's words are not LRCLIB's to replace — the editor's Delete is the way back
  - evidence: two 400s; the panel's buttons; the file
  - **result:** pass — both answered `400 Ketzerei: these lyrics are yours — delete them first`, the
    file kept its `[00:05.00] mine, not lrclib's`, and the panel showed **yours** with Edit as the
    only action

- [x] **K11 · R** — the refusals
  - do: a per-track action while a pass holds the album; on a track that is not `done`; rejecting
    when there is no match to reject
  - expect: refused with a message naming the reason
  - evidence: the status codes and messages
  - **result:** covered offline in `test_web.py` (the job name in the message, "no file yet",
    "no lrclib match"); the live album has every track downloaded and no long pass to race

## L. Repair from the web UI (P10, the decision is in DESIGN §12)

Added 2026-09-26. A scratch library inside the session scratchpad, seeded with one album fetched
for real and a second copy of it under a shouted spelling of the same artist (`FEUERSCHWANZ` /
`Feuerschwanz`), so repair had something to do. Server on 8799, driven through the real page.

- [x] **L1 · M** — the button, its confirm and its log
  - do: press "Repair library" in the header, read the dialog, accept
  - expect: a write-lane job whose log carries repair's own lines and its summary
  - invariant: nothing is downloaded and nothing is deleted; the dialog says so before it runs
  - evidence: the dialog text; the job's lane, label and log; the folders on disk
  - **result:** pass — the confirm showed README's paragraph (what it changes, that folders and
    files are renamed, that nothing is downloaded or deleted, that edited values are kept). The job
    came back `lane=write`, `kind=repair`, label "Repair the library", and its log ended with
    `=== Feuerschwanz — Sex Is Muss (Shouted)`, four rename/retag lines and
    **`1 album(s) tidied up`**. On disk the `FEUERSCHWANZ/` folder is gone and both albums sit under
    `Feuerschwanz/`, both now `provenance.albumartist = mb`

- [x] **L2 · R** — refused while the library is being changed
  - do: start a lyrics `--refetch`, then press Repair
  - expect: refused, naming the job in the way
  - invariant: repair renames folders across the library, so it must not run beside a writer
  - evidence: the status code and message
  - **result:** pass — `HTTP 400: “Look up all lyrics of Sex Is Muss” is running — wait for it,
    then repair`

- [x] **L3 · R** — the hint a user can now follow
  - do: read the fetch-time spelling hint
  - expect: it names both the command and the button
  - invariant: one message serves the CLI user and the UI user; neither is sent somewhere they
    cannot go
  - evidence: the log line
  - **result:** pass — "… 'ytalbum repair' (or “Repair library” in the web UI) unifies them on the
    better spelling", asserted in `test_web.py` as well so the two cannot drift apart

## M. The fetch preview (P11, DESIGN §9.28)

Added 2026-09-26. A scratch server on 8799 over an **empty** library inside the session scratchpad,
so "writes nothing" could be seen rather than argued. Driven through the real page.

- [x] **M1 · R** — preview a single with a label suffix (S1)
  - do: paste `watch?v=___ci9kmRc4` and press Go
  - expect: the names a fetch would write, and nothing on disk
  - invariant: a preview is a read job: it must not block writes and must not write
  - evidence: the panel; the library folder
  - **result:** pass — *Lord of the Lost — Viva Vendetta*, `single · 1 tracks → Lord of the Lost/Viva
    Vendetta`, "new to the library — nothing is written until you press Download". The label suffix
    and `(Official Video)` are gone and the artist carries MusicBrainz' spelling, i.e. enrichment and
    harmonisation have run. The library folder stayed empty

- [x] **M2 · R** — Cancel
  - do: press Cancel
  - expect: nothing written, nothing queued
  - evidence: `ls` of the library root
  - **result:** pass — the library was still empty (not even an album folder)

- [x] **M3 · M** — preview then Download, and compare (S9, the handle case)
  - do: paste `watch?v=dGO_sx4By28`, read the preview, press Download
  - expect: what was written is what was shown, character for character
  - invariant: the preview is the outcome, because both come from one code path
  - evidence: the preview panel against the stored plan
  - **result:** pass — preview and plan both read album *The Dead Don’t Die feat. Feuerschwanz*,
    artist `DOMINUM`, folder `DOMINUM/The Dead Don’t Die feat. Feuerschwanz`, track 1 with the same
    title; no `@handle` anywhere, and the album name equals the track title (P7's rule) in both

- [x] **M4 · R** — preview an album that is already here
  - do: paste the same URL again
  - expect: the "already in the library" state, and the plan as a fetch would keep it
  - invariant: the merge happens in the preview too, or a user's edits would appear to be about to
    be overwritten
  - evidence: the panel line; the button label
  - **result:** pass — "already in the library — downloading fetches what is missing and leaves your
    edits alone", button "Download what is missing". **One blemish found here and fixed:** the line
    first printed the server's absolute path; it now names the library-relative folder, and only
    when the album would move. The offline test covers the edit-keeping half (an album renamed by the
    user previews under the user's name, with `provenance.album = user`)

- [x] **M5 · R** — the preview is not compulsory
  - do: Shift+click Go on a URL
  - expect: the fetch starts directly, no preview panel
  - invariant: someone who does not want to look first must not be made to
  - evidence: the job kind and lane
  - **result:** covered offline — the direct path posts `/api/fetch` (write lane) with no preview job
    in between. Shift on a form submit is captured on the form's capture phase, since a submit event
    carries no modifier state

## N. A way back from an edit (P12, DESIGN §9.29)

Added 2026-09-26. Scratch library (S3, 7 tracks) inside the session scratchpad, server on 8799,
through the real page.

- [x] **N1 · M** — reset an album field
  - do: change the album artist to `FEUERSCHWANZ (my spelling)`, save, then press the "you ↺" badge
  - expect: the derived value is back, the field is ytalbum's again, and the folder follows
  - invariant: the value matters more than the mark — a merge decides ownership by comparing the
    value with `auto`, so a reset that only dropped the mark would be undone by the next update
  - evidence: the plan; the folder on disk; the badge
  - **result:** pass — back to `Feuerschwanz` with no `provenance.albumartist` at all, the folder
    renamed from `FEUERSCHWANZ (my spelling)/` to `Feuerschwanz/`, and the badge became a plain
    (absent) one, since nothing claims that value now

- [x] **N2 · M** — reset a track field
  - do: change track 1's title to `Ketzerei (my title)`, save, press its "you ↺"
  - expect: the title and the file name go back
  - evidence: the plan; the file on disk
  - **result:** pass — `Ketzerei` again, provenance gone, file renamed back to
    `Feuerschwanz - Sex Is Muss - 01 - Ketzerei.opus` and present (so the rename followed rather
    than leaving a dangling name)

- [x] **N3 · M** — the order flag, and what resetting it does *not* do
  - do: reverse the album by typing positions, save; then press the reset on "Track order is yours"
  - expect: the flag goes, and nothing is renumbered at that moment
  - invariant: a button that silently rearranged 56 tracks would be a trap, so the tooltip says the
    next update may reorder and the reset itself does not
  - evidence: `provenance.order`; the track order before and after
  - **result:** pass — flag `user` → absent, the reversed order untouched, the "Track order is
    yours" line gone

- [x] **N4 · M** — and then the source may order it again
  - do: after N3, run "Update library" (full)
  - expect: the album comes back in the source's order, files renamed
  - evidence: the job log; the plan
  - **result:** pass — the update logged renames for 5 of the 7 tracks and the order is the
    playlist's again (*Ketzerei, Hexenjagd, Ringelpietz, Sex is Muss, Krieger des Mets, Ketzerei
    (Summer Breeze 2016), Moralisch*). Watch the job: an earlier read of mine reported "no change"
    while the update was still running

- [x] **N5 · R** — nothing offered where nothing can be given back
  - do: a field the user never touched; and a field whose plan has no `auto` entry
  - expect: the plain badge in the first case; in the second, a badge that says why
  - evidence: covered offline in `test_web.py` (the reset is a no-op and the value stays the user's)

## O. Opening an album asks the disk (P13, DESIGN §9.30)

Added 2026-09-26. Scratch library holding S6 (*Judas (Deluxe Digital Edition)*, 56 tracks, 30 with
lyrics) inside the session scratchpad, server on 8799. The sidecars were changed with a shell, not
through the UI — that is the case this package is about.

- [x] **O1 · M** — a sidecar edited on disk
  - do: `sed` a line into one track's `.lrc`, then open the album in the UI
  - expect: the words are recognised as the user's at once, and the tag catches up
  - invariant: the same `reconcile` rules as a pass (§9.21) — the UI gets no special ones
  - evidence: the panel badge; the plan; the tag
  - **result:** pass — the row's panel read *Priest · with timestamps* with the **yours** badge, the
    hand-added line first, and only "Edit" offered (the per-track lookups withdraw for a user's
    words). One job, "Check the lyrics of Judas (Deluxe Digital Edition)", then carried the line
    into the `LYRICS` tag

- [x] **O2 · M** — a sidecar deleted on disk
  - do: `rm` another track's `.lrc`, open the album again
  - expect: the ♪ goes faint, the status becomes `none`, the grid count follows
  - evidence: the row's button class; the plan; `/api/state`
  - **result:** pass — `synced` → `none`, `lyrics_sha` cleared, the ♪ button gained the `empty`
    class, and the album's grid count went 30 → 29 once the job had saved the plan

- [x] **O3 · R** — an album that agrees with its files
  - do: open an unchanged album three times
  - expect: no job, and not a byte written
  - invariant: the check must be free when there is nothing to fix, or it would rewrite tags for a
    living
  - evidence: job count; every file's mtime
  - **result:** covered offline in `test_web.py` — three opens, no new job, every mtime unchanged

- [x] **O4 · R** — the cost
  - do: time `/api/album` for the 56-track album, against `/api/state` as a baseline
  - expect: bounded by the album, not the library
  - evidence: `curl -w %{time_total}`; the browser's own measure
  - **result:** **4–5 ms** for `/api/album` (five runs) against 2–3 ms for `/api/state`, so the
    reconcile costs about 2 ms for 56 tracks; 22 ms for the whole open measured in the page. The
    real library's largest albums are this size, so the same figure applies there

## P. The ⏱ mark leads to a cut (P14, DESIGN §9.31)

Added 2026-09-26. Scratch library with S3 inside the session scratchpad, server on 8799, through the
real page. The specimen is D2's: *Sex is Muss*, a 4:44.7 file against a 3:37.6 song, +1:07.

- [x] **P1 · R** — the target appears with the chip's own numbers
  - do: play track 4 and read the trim bar
  - expect: what the file is now, what the marks would keep, the reference, and the difference
  - invariant: one reference for both — the chip and the target must never disagree
  - evidence: the chip's tooltip against the readout
  - **result:** pass — chip `+1:07` ("MusicBrainz 3:37.6 · LRCLIB 4:45 · this file 4:44.7 — an intro
    or outro to cut?"), readout **"now 4:44.7 · keeping 4:45 · MusicBrainz 3:37.6 · +67.4s"** in the
    same amber band

- [x] **P2 · M** — mark the end while listening and watch the gap close
  - do: seek to where the song ends, press "end here"
  - expect: the readout follows the marks, in the chip's colours, before anything is saved
  - invariant: nothing is written until the trim is saved
  - evidence: the readout; the row's input; the plan
  - **result:** pass — **"keeping 3:37.9 · MusicBrainz 3:37.6 · +0.3s"**, green (inside the 5 s band),
    the row's end field showing `3:37.9`, the mark rounded to a tenth (217.9 from a
    `currentTime` of 217.64…), and the player paused at the mark. The plan was untouched at this point

- [x] **P3 · M** — save, and the chip goes quiet
  - do: press "save trim"
  - expect: the file is cut, the chip turns muted, the original is kept
  - evidence: the plan; `audio_length` of both files
  - **result:** pass — `trimmed = 0.00-217.90`, chip now `0:00` and muted ("MusicBrainz 3:37.6 ·
    LRCLIB 3:38 · this file 3:37.9"), the file on disk 217.91 s and
    `.originals/A9Z3Qkr-F9g.opus` still 284.66 s

- [x] **P4 · R** — which file you are hearing, and ▶ from start
  - do: play the now-cut track, press "▶ from start"
  - expect: the original is playing, the player says so, and the seek lands on the start mark
  - invariant: marks are in the video's timeline, so a cut track must be heard as the original
    (§9.17), or a mark would mean two different places
  - evidence: the audio URL; the player's line; `currentTime`
  - **result:** pass — URL carries `o=1`, the player reads *"playing the untouched original · the file
    on disk is cut to 0.00-217.90"*, and ▶ from start seeked to the mark

- [x] **P5 · R** — the arithmetic
  - do: the pending-gap function over marks, references and an unknown duration
  - evidence: `tests/test_length.py`
  - **result:** 10 cases in Python (`plan.trimmed_gap`), including that the target counts from the
    *video's* duration and not from a file already cut, and that MusicBrainz outranks LRCLIB as it
    does for the chip. The JS mirror has no unit test: this repo has no JavaScript harness, and P14
    was not the place to add one — P1 to P4 are the evidence for it

## Q. Reordering by dragging (P15, DESIGN §9.32)

Added 2026-09-26. Scratch library with S3 split into two discs (3 + 4) inside the session scratchpad,
server on 8799, through the real page. Playwright's `dragTo` for the mouse case, dispatched pointer
events for the rest.

- [x] **Q1 · M** — drag a row across a disc boundary
  - do: drag the last row of disc 2 (*Moralisch*, 2-4) onto the second row of disc 1
  - expect: it lands at 1-2, both discs renumber live, and the save writes exactly that
  - invariant: what is on screen before the save is what the save writes
  - evidence: the inputs after the drop; the plan after the save
  - **result:** pass — on screen `1-1 … 1-2 Moralisch … 2-1 …` with disc 1 counting 1–4 and disc 2
    1–3, and the saved plan identical to it, `provenance.order = user`

- [x] **Q2 · M** — the case a changed number cannot describe
  - do: drag a disc-2 row into disc 1 at a position where it keeps its per-disc number
  - expect: it still lands where it was dropped
  - **result:** **found as a defect and fixed in this package.** *Sex is Muss* (2-01) dropped at 1-2
    keeps the number… 2 for the row below it, and the dragged row's own number was unchanged, so the
    server read it as a row that had stayed put and filed it after disc 1's rows. The payload now
    carries `moved` for rows the user has put somewhere; re-run, the drop lands at 1-2 and the save
    agrees. Two offline tests pin both halves (with the flag it moves, without it does not)

- [x] **Q3 · R** — the keyboard
  - do: focus a row, Alt+↓, then Alt+↑
  - expect: one position each way, nothing written
  - **result:** pass — down then up returned the album to its arrangement, and the plan on disk was
    untouched throughout

- [x] **Q4 · R** — Escape during a drag
  - do: start a drag, move over another row, press Escape before releasing
  - expect: every row back where it was, including the disc
  - **result:** pass — mid-drag the row showed at 1-4, after Escape the arrangement was identical to
    before, with a "move cancelled" toast

- [x] **Q5 · M** — a drag and a typed number in one save
  - do: drag a row, then type a position into another row, then save
  - expect: both intents honoured
  - **result:** pass — the dragged row kept its dropped position while the typed row went to the
    front; the saved plan shows both

*Note:* a synthetic `pointerdown` made `setPointerCapture` throw inside the handler — harmless in
practice (the drag still ran, because the moves arrive on `document` anyway) but it was an exception
escaping an event handler, so the call is now guarded.

## R. The page's logic under test (P19, DESIGN §9.33)

Added 2026-09-26 with the harness. The split moves what `app.js` computes into `webui/logic.mjs`,
which the page imports as a module; these cases are about the page still working, since the tests
themselves are the harness's own evidence.

- [x] **R1 · R** — the page loads as a module and says nothing
  - do: open a scratch server's page after the split
  - expect: no console error, the library renders
  - evidence: the console; the snapshot
  - **result:** pass — `<script type="module" src="/app.js?v=fbce9445f5">`, 0 console errors, the
    grid and header render as before. `/logic.mjs` is served as `text/javascript`

- [x] **R2 · R** — everything the split touched still behaves
  - do: open an album; filter; drag a row to the top; open a lyrics panel; play and set a mark
  - expect: unchanged behaviour
  - evidence: the DOM after each
  - **result:** pass — 7 rows with their ⏱ chips (−1:56, −2:27, −2:19, +1:07); the filter narrowed 1
    album on "muss"; the drag put the last row first and renumbered 1…7 live; the lyrics panel read
    *with timestamps · lrclib #…* with Edit / Look up again / Not these words and 53 timed lines;
    a mark taken at 42.37 s was written as `0:42.7` and the target line read "now 3:45.2 · keeping
    3:02.3 · MusicBrainz 3:17 · −14.7s"

- [x] **R3 · R** — H4 again: the cache chain survives a second file
  - do: check the headers, then change `logic.mjs` and re-read the hashes
  - expect: the index is never cached, the assets are hashed, and a change to the module moves
    `app.js`'s URL too
  - invariant: a module's import URL is inside the module, where the index's rewriting never
    reached — a new `logic.mjs` behind a cached `app.js` would never be asked for
  - evidence: `Cache-Control`; the hashes before and after
  - **result:** pass — index `no-store`, `app.js` and `logic.mjs` `no-cache`; the served `app.js`
    imports `./logic.mjs?v=d65eda03de`; touching the module took it to `05175a0217` **and** `app.js`
    from `fbce9445f5` to `a3cd1aeba3`, then both restored

## S. Four layout defects in the track rows (P20)

Reported by the user from real use on v0.3.0, with an annotated screenshot — four red circles, left
to right. Checked after the fix in all three row states (plain, selected, playing), at 1184 and 1280,
and on a two-disc album.

- [x] **S1 · R** — the grip broke the line
  - was: `⋮⋮` sat *above* the position field, so the cell was two lines tall and the whole row grew
  - cause: the grip and an `input.num` at `width: 100%` in the same cell — the input took the line
  - now: both in a `.cell-row` flex box; the grip's centre and the number's centre are the same pixel
    (369 in the check), the cell is one line of 38 px inside a 49 px row
  - **result:** pass

- [x] **S2 · R** — the trim cell sat higher than the rest
  - was: the two time fields, the gap figure and the ⇉ button floated above the row's other content
  - cause: **`td.trim { display: flex }`** — a `display: flex` on a `<td>` stops it being a table
    cell, so it no longer shares the row's height or its vertical alignment
  - now: the cell is a table cell again and the flex row is inside it; the trim field's centre and
    the title field's centre are the same pixel
  - **result:** pass

- [x] **S3 · R** — the highlight had a gap
  - was: on the playing row the band broke off under the trim cell, so the row read as two pieces
  - cause: the same `display: flex` — the cell's box was its content's height, not the row's, so the
    row background did not reach across it
  - now: all eight cells report the same `y`, the same height (49) and the same background
  - **result:** pass, and S2 and S3 were one defect with two faces

- [x] **S4 · R** — the ✓ ♪ ✕ cluster was cramped
  - was: ~18 px targets, touching, with room going spare to the right
  - now: 28 × 28 each with .35 rem between them (measured: ♪ at x=1117, ✕ at x=1150, both 28 × 28)
  - **result:** pass

- [x] **S5 · R** — on a highlighted row the controls lost their edges
  - was: the ♪, the ⇉ and the ✕ kept only their glyphs on the playing row; their borders vanished
    into the highlight
  - cause: `--line` is chosen against the panel, and the band sits on top of it. **Measured, with the
    translucent band composited over what is behind it:** in dark the band is `48,44,68` and a
    control's border `46,43,54` → **1.03**; in light the band is `234,230,250` and the border
    `226,223,232` → **1.08**; the delete button's border is transparent by design → **1.00**. No
    boundary at all, in either theme — worse than reported, which had it as a dark-mode fault
  - now: on a highlighted row the borders follow the foreground — **4.66** in dark and **4.51** in
    light, past the 3:1 that WCAG 1.4.11 asks of a control's boundary
  - **result:** pass. And the reviewer's assumption about the lyrics panel is **wrong in a useful
    way**: the panel row does not inherit the band (measured backgrounds `31,29,37` dark and
    `255,255,255` light, i.e. the plain panel), so opening it from a highlighted row changes nothing.
    What the same measurement *did* show is reported below rather than fixed here
  - *my own measurement lied first:* the first pass parsed `color(srgb 0.6 0.52 1 / 0.14)` as if the
    floats were 0–255 and reported 1.51 and 15.92 — plausible numbers, both wrong. A contrast figure
    is worthless unless the parser handles the colour space and composites the alpha

- [x] **S6 · R** — what the fix itself broke, found while looking
  - the position field was 2.6 rem with .6 rem side padding: its own value overflowed its box
    (`scrollWidth` 43 vs `clientWidth` 40) and a two-digit number would have been clipped — a 56-track
    album has those. Now 3 rem with tighter side padding; `7`, `12` and `56` all fit exactly
  - **result:** pass. Caught by measuring rather than by looking, which is the complement to the
    entry below

## T. A control boundary you can see, and a handle you can find (P21, backlog 11 + 12)

Both items came out of P20's measurements rather than from use: the highlighted-row fix (S5) was the
extreme of a condition the whole page had, and the grip that S1 put back in line was still hard to
spot. CSS only — no markup changed, so the glyph, the tooltip and the drag are the ones P15 shipped.

**The measurement.** Every figure below comes from this, run in the page's own console (or through
`browser_evaluate`). It exists because the first attempt at S5 lied: Chrome answers a `color-mix`
with `color(srgb 0.6 0.52 1 / 0.14)`, whose floats are 0..1, and reading them as 0..255 gives
plausible, wrong numbers. It also composites — a translucent band or a transparent border means
nothing until it is laid over what is actually behind it.

```js
const CT = (() => {
  const parse = (s) => {
    let m = s.match(/^color\(srgb\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)(?:\s*\/\s*([\d.eE+-]+))?\)$/);
    if (m) return [+m[1] * 255, +m[2] * 255, +m[3] * 255, m[4] === undefined ? 1 : +m[4]];
    m = s.match(/^rgba?\(([^)]+)\)$/);
    if (m) { const p = m[1].split(/[,\s/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p[3] ?? 1]; }
    if (s === "transparent") return [0, 0, 0, 0];
    throw new Error("unparsed colour: " + s);
  };
  const over = (fg, bg) => [0, 1, 2].map((i) => fg[i] * fg[3] + bg[i] * (1 - fg[3])).concat([1]);
  const lum = (c) => { const f = (u) => (u /= 255) <= 0.03928 ? u / 12.92 : ((u + 0.055) / 1.055) ** 2.4;
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b);
    return +((Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)).toFixed(2); };
  const behind = (el) => {                       // what is painted behind el, from the page up
    const stack = [];
    for (let n = el.parentElement; n; n = n.parentElement) stack.push(parse(getComputedStyle(n).backgroundColor));
    stack.push(parse(getComputedStyle(document.documentElement).backgroundColor));
    let c = [255, 255, 255, 1];
    for (let i = stack.length - 1; i >= 0; i--) c = over(stack[i], c);
    return c;
  };
  const hex = (c) => "#" + c.slice(0, 3).map((x) => Math.round(x).toString(16).padStart(2, "0")).join("");
  const edge = (el) => {                         // the control's border over its own background
    const cs = getComputedStyle(el), back = behind(el);
    const own = over(parse(cs.backgroundColor), back);
    const line = over(parse(cs.borderTopColor), own);
    return { border: hex(line), on: hex(own), ratio: ratio(line, own) };
  };
  return { parse, over, lum, ratio, behind, hex, edge };
})();
CT.edge(document.querySelector("button.quiet"));   // → { border: "#6f6882", on: "#1f1d25", ratio: 3.16 }
```

- [x] **T1 · R** — `--edge` clears 3:1 where `--line` did not
  - the four pairs, measured in the page before and after (WCAG 2.2 1.4.11 asks 3:1 of a control's
    visual boundary):

    | pair | before (`--line`) | after (`--edge`) |
    |---|---|---|
    | dark, control on `--panel` | 1.20 | **3.16** |
    | dark, control on `--bg` | 1.32 | **3.47** |
    | light, control on `--panel` | 1.32 | **3.43** |
    | light, control on `--bg` | 1.21 | **3.16** |

  - the same 3.16 / 3.43 was read off every control that takes the token: the header's quiet
    buttons, the filter and URL inputs, both settings selects, the settings and row text inputs, the
    row's ⇉ and ♪, the lyrics editor's textarea (which had no border rule at all and drew the
    browser's default box), and a plain `.badge`
  - `--edge` is the same hue and saturation as `--line`, moved along the ramp until both pairs clear
    3:1 with a little to spare, so the page's colour does not change — only the strength of an edge
  - **result:** pass

- [x] **T2 · R** — the dividers stayed quiet, and `.card` stayed as it was
  - `--line` is untouched on table rules, panel and card edges, the header's underline, the sticky
    action bar, the job and toast cards, the rail and the lyrics box frame — measured still 1.20 in
    dark on `.panel` and `.card`, which is what "quiet" means here
  - `.card` is a `<button>` and so is a control, but it is not in the decision's list and it is not
    identified by its edge: it is a cover image, a title and its badges. Left alone deliberately;
    the same goes for `.pick`, whose hover border is an affordance and whose state is a real
    checkbox. Named here so the next reader knows it was a decision and not an oversight
  - **result:** pass

- [x] **T3 · R** — the highlighted row still needs its own rule
  - the question the decision asked: does `--edge` make S5's override redundant? **No.** Composited
    over the playing band, `--edge` measures **2.53** in dark and **2.82** in light — better than
    the 1.03 / 1.08 it replaced, still short of 3:1
  - so `tr.playing td button` keeps following the foreground: measured again after the change,
    **4.66** dark and **4.51** light
  - an `input` on a highlighted row is unaffected either way (3.16 / 3.43): it paints its own
    `--panel` background, so the band is never behind its border. Only the transparent-backed
    controls were ever the problem
  - a plain `.badge` on the band sits at 2.53 / 2.82 and is left there: a badge is a label, not a
    control, and the coloured ones (`mb`, `ok`, `bad`) carry meaning in their border that a
    blanket override would flatten — measured 4.60 for `.badge.mb` on the band
  - **result:** pass

- [x] **T4 · R** — the ✕ still has no edge, on purpose
  - `button.danger-text` measures **1.00** at rest in both themes: `border-color: transparent` is
    what makes it a text button beside the boxed ♪. Its glyph carries it (`--bad` on the panel), and
    where the glyph would float — the highlighted row — S5's rule already gives it 4.66 / 4.51
  - left as it is rather than boxed: 1.4.11 asks for a boundary where the boundary is what
    identifies the control, and here it is the red ✕
  - **result:** pass, recorded so the 1.00 is not read later as an oversight

- [x] **T5 · R** — the grip reads as a handle, and the row lights it
  - at rest: two separated columns of three dots, `--muted`, **5.78** dark and **5.49** light
    (contrast was never the problem — the columns merging into one stripe was)
  - on row hover, and on keyboard focus anywhere in the row: `--ink`, **14.05** dark. Measured with
    a real pointer over the title field of row 3 — that row's grip brightened, the others stayed
    `rgb(154,150,166)`
  - cursor `grab` over the handle, `grabbing` while a row is being dragged
  - touch, where there is no hover at all: the rest state *is* the touch state. Proven by listing
    every rule that matches `.grip` — three, of which two only *add* (colour on hover/focus, the
    grabbing cursor). Nothing is hidden until hovered, which is why the glyph had to change too
  - the drag itself still works after the restyle: dragging row 3 onto row 1 by the grip reordered
    the rows to *Pale Tortured Blue, Louise, And There Will…* and renumbered 1–4. Nothing was saved
  - **result:** pass

- [x] **T6 · R** — which glyph, decided by looking
  - the decision offered `⠿`, `⣿` or separated `⋮⋮`. All three were rendered in the live rows at
    the row's own size and photographed at 10×: the braille pair falls back to another font on this
    system — softer, a pixel low, and 18.9 px wide against 16.7 — while `⋮⋮` comes from the UI font
    and draws two crisp columns once the letter-spacing stops pulling them together
  - so: `⋮⋮` at 1.05em with `letter-spacing: .06em` (was .9em at `-.12em`). No glyph change in
    `app.js`, which also means no dependency on a font having braille at all
  - **result:** pass

- [x] **T7 · R** — a pass over every view, both themes
  - library, album, channel listing (*My Dark Lullabies*, 20 albums) and settings, in dark and in
    light, looked at one by one: controls bounded, dividers unchanged, nothing newly ruled, no
    truncation or overlap introduced, badge colours intact, the `.to-top` button bounded like the
    rest
  - the player bar was **not** exercised: it only renders while audio plays, and the check would
    have played sound on the user's desktop. Its trim handles are accent on `--panel` (5.71 dark,
    5.62 light) and already clear 3:1 without a ring, so their 1 px `--panel` outline — a separator
    from the track line beneath, not a boundary — was left alone, against the letter of the
    decision. Flagged rather than changed
  - **result:** pass, with that one gap named

## U. A track's audio from another video (P22, DESIGN §9.34)

Run 2026-09-27 against a scratch library inside the session scratchpad, seeded with **S1** — *Viva
Vendetta (Official Video)*, 471 s of film around a 3:50 song, MusicBrainz 229.8 s — and pointed at
**S10**, `Z2UO4FsFGFM`, the sung album recording as its own upload. The same shape as the user's
Kupfergold case, with one difference that makes it a better specimen: the two uploads are by
different channels (Napalm Records and Lord Of The Lost), so "the uploader follows the audio" is
visible rather than argued. Nothing was written to the real library; the real library was used only
for the read-only load below. Server on 8799, everything driven through the real page.

- [x] **U1 · R** — old plans still load, and nothing lights up on them
  - all **246 plans / 3946 tracks** of the real library loaded read-only with the new fields: 0
    overrides, 0 tracks that would show a timing notice — and 2055 tracks with synced lyrics, none of
    which raises one, because a sidecar from before the record exists says nothing about what it was
    written for
  - **result:** pass

- [x] **U2 · M** — a paste that is not one video is refused, twice
  - a playlist URL, a channel URL and the prose "not a video" are each refused *in the page*, before
    anything is sent: "That is not a single video. Paste the watch link of one video, or its
    11-character id — a playlist or a channel cannot be a track's source."
  - the server refuses the same three itself (`apply_user_edits` raises with the track's title in the
    message), because the page is not the only door; `tests/shared/video_ids.json` is the table both
    sides are tested against
  - **result:** pass

- [x] **U3 · M** — the switch asks first, and names the marks it will clear
  - with the track trimmed to 1:30–5:20 and the user's own timestamped `.lrc` beside it, the confirm
    read: *"Take the audio from Z2UO4FsFGFM instead of the playlist's own ___ci9kmRc4. The track is
    downloaded again — it is a different recording. The trim 1:30–5:20 belongs to the current file
    and will be cleared. Your lyrics are kept, but their timings were written for the current file."*
  - **result:** pass

- [x] **U4 · M** — what the switch actually did
  - `source_override` set, `video_id` unchanged; state → pending → done, downloaded from the override
  - trim marks and `trimmed` cleared; `.originals/` left **empty** — the previous recording's kept
    original was deleted, which is the one thing that could have shadowed the new file
  - `channel` Napalm Records → **Lord Of The Lost**, `duration` 471 → 230: the ⇉ button now offers
    "Apply this trim to every track from Lord Of The Lost", which is the point of following the
    uploader
  - `file_length` measured fresh at 229.841 against an unchanged `mb_length` of 229.8, so the ⏱ chip
    went from **+4:01 (warn)** to **0:00 (muted)** without anything else being touched
  - job log: `Viva Vendetta: audio now from Z2UO4FsFGFM (was ___ci9kmRc4)` and
    `downloaded from Z2UO4FsFGFM: 01 Lord of the Lost - Viva Vendetta`
  - **result:** pass

- [x] **U5 · R** — the album view and the preview both say so
  - in the row, the ⇄ button in the *from* cell turns accent and its title reads "Audio from
    Z2UO4FsFGFM, not the playlist's ___ci9kmRc4"; the panel under the row carries the video as a link,
    the field, and the "you ↺" badge
  - the preview of the same URL (which is the merge, run dry) shows `audio ← Z2UO4FsFGFM` on the
    track, titled "its audio comes from Z2UO4FsFGFM, which you chose, not the playlist's ___ci9kmRc4"
  - the same preview is the merge evidence: the override survived a fresh reading of the source, and
    the track was **not** marked "gone" — `in_source` is about the playlist's video, which is still there
  - **result:** pass

- [x] **U6 · M** — the words are untouched, and their timings say what they were written for
  - the user's `.lrc` came through the switch unchanged and still marked "yours"
  - the panel showed: *"⚠ these timings were written for a different file — save them again to say
    they are for this one"*. No lengths, correctly: 230.0135 → 229.841 is the same song measured
    twice, and printing "3:50 → 3:49" would have said nothing
  - saving the words again in the editor cleared the notice; after the *reset* (back to the 471 s
    film) it came back **with** its numbers — "3:49 → 7:50" — because now they really differ
  - **result:** pass

- [x] **U7 · M** — the way back costs what choosing cost
  - the "you ↺" badge in the panel confirms first ("Back to the playlist's own video, ___ci9kmRc4 …"),
    then re-downloads the playlist's video, clears the override, and takes the uploader back to
    Napalm Records; the chip returned to +4:01
  - **result:** pass

- [x] **U8 · R** — looked at, in both themes
  - the panel's field, "Use this video" and "Cancel" on one line (the base `input { width: 100% }`
    had taken the whole row first — found by looking, fixed with a flex basis), the refusal in
    `--bad` (5.44 on the light panel), the ⇄ button bounded like the row's other controls (3.43)
  - the timing notice is `--ink` with a `--warn` edge rather than warn text: a sentence to be read
    wants the body text colour. As `--ink` on its own tint it reads 14.93 in light and 11.23 in dark
  - measuring it found that `--warn` itself was **3.64** on the light panel and 3.35 on the page
    background, under the 4.5:1 body text asks for — which the page's own chips (`.len.warn`,
    `.p-target.warn`, `.badge.warn`) were drawn in. Reported rather than changed here; the owner
    asked for it as its own commit, and light `--warn` is `#98641a` since (**5.03** and **4.63**,
    measured in the page; dark was already 7.83 / 8.61 and is untouched)
  - **result:** pass

## V. Stamping the words to the file's clock (P23, DESIGN §9.35)

Run 2026-09-27 on a scratch library in the session scratchpad (S1, *Viva Vendetta*), **trimmed to
1:30–5:20** so the player's clock and the file's differ by exactly 90 s — the condition that made the
user's hand-typed stamps land late. Driven through the real page on 8799; the audio element was
muted for the run, because the machine belongs to someone. Nothing was written to the real library.

- [x] **V1 · R** — the two clocks, and the readout that names them
  - the trimmed track plays from `?o=1` (the untouched original), the player's line says so, and the
    editor's readout reads **"in file 0:05.8 · player 1:35.8"** — 90.0 s apart, to the tenth
  - with the trim cleared, the same readout is one number ("in file 0:43.4" against a player time of
    43.415): on an untrimmed track the two clocks are the same one, and a second number would be noise
  - the player's own display and the trim bar are untouched, as the task asks — they are the video's
    clock because the marks are
  - **result:** pass

- [x] **V2 · M** — the tap, which is the whole point
  - playing, cursor on line 1, Ctrl+Enter: player at **114.870** → the line became **`[00:24.9]`**,
    which is `114.870 − 90` rounded to a tenth, and the cursor moved to line 2
  - paused, cursor on line 2: player **114.872** → `[00:24.9]` again, exactly `round((t − 90) × 10)/10`
  - the button does the same for touch: on the untrimmed file at **100.0** it wrote `[01:40.0]`
  - a second tap on a stamped line rewrites it; it never grows a second stamp
  - **result:** pass

- [x] **V3 · M** — nudge and play, by ear
  - Alt+← on `[00:24.9]` → `[00:24.8]`; Shift+Alt+→ → `[00:25.3]`, and the player was at 115.85 a
    moment later, i.e. playing from 25.3 + 90
  - Alt+Enter from a rewound player jumped back to the same place and played
  - a line with no stamp is left alone and says why: *"This line has no stamp yet — stamp it first"*
  - **result:** pass

- [x] **V4 · M** — shift all
  - `-2.4` applied to *`[00:25.3]` / `[00:24.9]` / an unstamped line / a blank line / a last line*
    gave `[00:22.9]` and `[00:22.5]`, with the three unstamped lines untouched, and the toast said
    "moved 2 stamps by -2.4 s — nothing is saved until you press Save"
  - **result:** pass

- [x] **V5 · M** — what reaches the disk is what the textarea had
  - Save wrote exactly `[00:22.9] Bury me in fire / [00:22.5] and call it a morning / Viva Vendetta /
    (blank) / the last line`, the status became `synced`, the words stayed the user's, and
    `lyrics_for_length` recorded the cut file's 230.0135 s — the length these stamps belong to
  - nothing had touched the disk before that: the shift, the taps and the nudges only moved text
  - **result:** pass

- [x] **V6 · R** — the editor still behaves like a text box
  - Enter alone still makes a newline; only Ctrl/⌘+Enter, Alt+Enter and Alt+arrows are taken, and the
    page's own player keys already ignore anything typed inside a textarea
  - **result:** pass

- [x] **V7 · R** — looked at, both themes
  - three tidy rows: the tap, ▶ and the four nudges; the shift field with its button; then Save /
    Cancel / Delete. The readout sits at the right end of the first row and is empty when this track
    is not playing
  - one blemish of my own, found by looking: the shift field took the whole line, because
    `.panel-actions input[type=text]` (from P22) outranks a plain `input.shift-by`. Specificity
    fixed, field 80 px, on one line with its button in both themes
  - **result:** pass

## W. Words placed on a clock by a provider (P25, DESIGN §9.36)

Run 2026-09-27 on a scratch library inside the session scratchpad: the two albums that hold the
spike's mis-timed specimens, **Feuerschwanz — Fegefeuer** (*Berzerkermode*) and **Visions of
Atlantis — Pirates** (*Clocks*), copied out of the real library read-only. Each server ran with its
own `XDG_CONFIG_HOME`, so the user's own configuration was never touched. The audio element was
muted for the run.

- [x] **W1 · R** — with no provider, nothing exists
  - default config: `/api/state` reports `timing: {provider: "none", capabilities: []}`, and the
    lyrics editor has no align action — the tool row is P23's six buttons and nothing else
  - asking anyway is refused by the server: `POST /api/align` → 400, *"no timing provider can align
    words — see `timing_provider` in the config"*
  - **result:** pass

- [x] **W2 · M** — the local provider corrects a `.lrc` the spike found to be wrong
  - `timing_provider = "local"`, GPU. *Berzerkermode*: the LRCLIB stamps read `[00:12.52]`,
    `[00:31.06]`, `[00:31.92]`; the proposal reads **`[00:19.6]`, `[00:37.0]`, `[00:38.0]`** — the
    +6.3 s correction the spike predicted, arrived at independently here
  - all 65 lines placed; the notice says *"timed by local/VOXPOPULI_ASR_BASE_10K_DE + htdemucs — a
    machine's proposal, nothing is saved yet: press ▶ on the first line"*
  - **12.2 s** end to end, from the click to the stamps appearing
  - **result:** pass

- [x] **W3 · M** — and the English one, with the English aligner
  - *Clocks*: `[00:00.02]`, `[00:02.89]` → **`[00:06.8]`, `[00:09.5]`**, the +6.5 s the spike
    measured. `local/WAV2VEC2_ASR_BASE_960H + htdemucs`, **18.3 s** for a 5:19 track
  - the language is chosen from the words, not configured: German words took the German model in W2
    and English words the English one here
  - **result:** pass

- [x] **W4 · M** — what Save writes, and what it does not
  - after Save: the `.lrc` holds exactly the proposal, `lyrics` is `synced`,
    `lyrics_timed_by = "local/VOXPOPULI_ASR_BASE_10K_DE + htdemucs 2.11.0+cu130"`, and
    `provenance.lyrics` is still **`user`** — the words are the user's, the clock is a machine's
  - the panel afterwards carries both badges: **“timed by local”** beside **“yours”**
  - before Save, nothing on disk had changed: the album folder's files and mtimes were identical
    after an alignment (also asserted in `tests/test_timing.py`)
  - **result:** pass

- [x] **W5 · R** — the same work without a GPU
  - `timing_device = "cpu"`, same track: **108.4 s** against 12.2 s, which is the two minutes the
    spike predicted for a four-minute track and the number that matters for a machine without a GPU
  - the two devices agreed on **64 of 65 lines to the tenth**. They disagreed on the *first* line
    (0.0 s on the CPU against 19.6 s on the GPU) — the line a CTC aligner has most freedom over,
    since the song opens with a shouted intro. One line in sixty-five, and it is why the result is
    a proposal in an editor rather than a write to disk
  - **result:** pass, with that difference recorded rather than smoothed over

- [x] **W6 · M** — the other machine's shape, on this machine
  - `ytalbum timing-serve --port 8770 --device cuda` in the environment that has the extra;
    `GET /capabilities` → `{"capabilities": ["align"], "device": "cuda", "provider": "local"}`
  - the web UI run from the **project environment, which has no torch at all**
    (`import torch` → `ModuleNotFoundError`), with `timing_provider = "http"`: the page offered the
    action, and *Clocks* aligned to the same `[00:06.8]`, `[00:09.5]` in **19.3 s** — about a second
    of HTTP over the direct run
  - this is the deployment the user asked for, demonstrated with the app and the model in different
    processes with different interpreters
  - **result:** pass

- [x] **W7 · R** — two defects of my own, found by using it
  - the editor was **thrown away when the job finished**: `poll()` rebuilt the album panel on every
    busy→idle transition, which is right for a job that changed the disk and exactly wrong for one
    whose whole purpose is to put text into the open editor. A read-lane job no longer triggers the
    rebuild
  - `applyStamps` was used but never imported, so the alignment finished and the editor sat on
    "asking the timing provider" for ever. The page's own error handling had it (`ReferenceError`
    in the console, a toast for the sibling bug) — I had not looked
  - both fixed before the commit; neither would have been caught by a unit test, and both were
    obvious within one click of using the thing
  - **result:** pass

- [x] **W8 · R** — a stamped line with no words keeps its stamp
  - LRCLIB entries often end with a bare `[03:05.66]` marking the outro. The provider is only ever
    given lines that have words, so such a line is left exactly as it was — which after an
    alignment can leave it out of order. Not fixed, on the owner's call: an empty stamped line is a
    legitimate LRC device and rewriting it would be guessing at what it means
  - **said rather than fixed**, though: the notice now ends with *"1 stamped line without words was
    left as it was; check it is still in order"* (plural for more), so the one thing the user has to
    look at is named instead of left to be discovered
  - **result:** pass

## X. Providers that are somebody else's computer (P26, DESIGN §9.37)

Run 2026-09-27 against **a server of my own speaking the two vendors' documented shapes**, on a
scratch library (one album copied out of the real one, read-only), with each web server given its
own `XDG_CONFIG_HOME`. **No request in this package has ever been billed**: there is no key for
either vendor, the fixtures come from the documentation, and the clients were pointed at the fake
with `YTALBUM_TIMING_BASE_ELEVENLABS` / `_DEEPGRAM`, which exists for exactly this and for anyone
running a gateway.

- [x] **X1 · R** — each vendor claims only what it sells
  - ElevenLabs, with a key: `capabilities` = `["align", "transcribe"]`. Deepgram, with a key:
    `["transcribe"]` — and asking it to align raises *"Deepgram does not align words you give it"*
  - either vendor **without** a key offers nothing at all, so the page shows nothing rather than a
    button that fails
  - with Deepgram configured, the editor of a track that has words shows P23's six tools, **no
    "align these words"**; the read panel of a track without words shows **"✎ draft the words"**
  - **result:** pass

- [x] **X2 · M** — alignment through ElevenLabs' documented shape
  - the confirm came first: *"The audio of this track is sent to elevenlabs to place these words on
    its clock. It leaves this machine and this network… ytalbum never retries, so one press is one
    request."*
  - the fake answered with one word per second from 5 s, and the editor filled with `[00:05.0]`,
    `[00:07.0]`, `[00:11.0]` — the fake's own arithmetic mapped back onto the *lines*, which is the
    word-to-line walk doing its job against the documented `words[]` array
  - job log: `aligning 65 lines of Berzerkermode with elevenlabs · 3.7 min of audio` — the minutes a
    per-minute bill is about to be charged for, before the request
  - **result:** pass

- [x] **X3 · M** — a draft, through both vendors
  - ElevenLabs: the editor opened with `[00:01.0] Erfundene Worte eins.` / `[00:12.0] Erfundene Worte
    zwei.` — its `words[]` grouped into sentences, with `spacing` and `(Applaus)` (`audio_event`)
    left out, because those are not lyrics
  - Deepgram: `[00:01.0] Made up words one.` / `[00:12.0] Made up words two.` — taken from its
    `paragraphs.sentences`, which is what that field is for
  - the notice never claims more than it is: *"drafted by deepgram/nova-3 — a machine's guess, half a
    song for some tracks: read it before you save it. 2 of 2 lines came with a time."*
  - **result:** pass

- [x] **X4 · M** — what a saved draft records
  - after Save: `lyrics_words_by = "elevenlabs/scribe_v2"`, `provenance.lyrics = user`, and the panel
    carries **"words by elevenlabs"** beside **"yours"** — the words were a machine's, the file is
    the user's, and neither claim is hidden behind the other
  - a later save of hand-written words clears the mark (asserted in `tests/test_timing_cloud.py`)
  - **result:** pass

- [x] **X5 · R** — the key stays here
  - `/api/state` carries `keys: {elevenlabs: true, deepgram: false}` and **not the key**; searching
    the whole payload for the configured value finds nothing. The settings field is a password input
    that is never filled from the server — it shows `•••••••• (set)` as a placeholder and an empty
    value, so a save that leaves it alone leaves the key alone
  - the job log for a request contains the file name and the minutes, never the key
  - **result:** pass

- [x] **X6 · R** — the price, where the choice is made
  - the provider row reads: *"…or a paid service. ⚠ deepgram receives the audio of every track you
    use it on. Their list price was $0.0043 per audio minute, i.e. $0.26 per hour (Nova-3, pay as you
    go) (checked 2026-09-27)."* The number is a constant with its date, and nothing looks a price up
    at runtime
  - **result:** pass

- [x] **X7 · R** — a defect of my own, the sibling of W7
  - the finished **draft** job rebuilt the album panel and threw the draft away, because the page's
    read-lane list was a hard-coded set of job kinds that I had not added `draft` to. It now asks the
    job for its `lane`, which the server already sends, so the page cannot drift from the server's
    own list again
  - **result:** pass, and the class of bug is closed rather than the instance

- [x] **X8 · R** — one live request, because the user provided a free-tier key
  - the key lives in `~/.config/ytalbum/deepgram.env` (mode 600), was read into the environment of a
    single command and of nothing else, and is not in this repository, the app's config, any log or
    any transcript
  - **one** request, a 211-second German track: **7.1 s** end to end (about 30× real time, upload
    included), `test_deepgram_for_real` passed
  - the documented shape held: `metadata` + `results` at the top, `words[]` with `word`,
    `punctuated_word`, `start`, `end`, `confidence`, starts in **seconds** (the first at 37.25),
    and `paragraphs.paragraphs[].sentences[]` present — 107 words became 16 lines, `grouped_by=sentences`
  - **two fields the documentation does not list**: `languages` on the alternative and `language` on
    every word, both from asking for multi-language recognition. ytalbum ignores them; the fixture
    now carries them so the next reader is not surprised. No client change was needed
  - **result:** pass — and the fixtures were right about everything the client reads

### Where the documentation is not certain

Written down rather than guessed at, per the task:

- **Deepgram's `paragraphs`** is only present when `paragraphs`/`smart_format` are requested, and the
  documentation does not promise it for every model. The client asks for it, uses its sentences when
  they are there, and groups the words itself when they are not — both paths are tested
  (`deepgram_listen.json` and `deepgram_listen_no_paragraphs.json`). **Answered on 2026-09-27** by
  the one live request (X8): for `nova-3` with those parameters it *was* returned, 16 sentences for a
  211-second track.
- **ElevenLabs' `start`/`end` are documented as nullable** on speech-to-text words. A word without a
  time does not take its line's stamp with it; the line simply starts at the first word that has one.
- **ElevenLabs' forced alignment returns its own word list**, which need not match the words sent
  (one splits a contraction, another drops an aside). The walk allows four words of slippage and
  leaves a line unplaced rather than shifting every later stamp by one word.
- **No vendor's error body is promised in detail.** The client reads `detail`, `message`, `error` and
  `err_msg`, and falls back to the first 200 characters of the body, so a shape nobody documented
  still reaches the user as the vendor's own words.

## Y. A second opinion on an alignment (P27, DESIGN §9.38)

Run 2026-09-27 on **sixteen tracks copied read-only out of the real library** into the session's own
cache: the four the P24 spike found the two methods far apart on (Feuerschwanz *Bastard of Asgard*,
Lord of the Lost *Argent*, Mono Inc. *A Love That Never Dies*, Sabaton *A Lifetime of War*) and twelve
it found them agreeing on, 731 stamped lines in all, English and German. Every track was aligned
**twice** — once with the cross-check and once without — so the added time is measured rather than
guessed, and a third pass recorded how far apart the two methods were per line, because the two
constants in §9.38 should come from a distribution and not from a round number.

Machine: the laptop of `docs/spikes/2026-09-alignment.md` (RTX 4060 Laptop, 8 GB), which is the
**favourable** case; the CPU column below is the figure closer to a typical install.

- [x] **Y1 · M** — the sixteen tracks, and what the check costs on each

| # | track | lines | alone | checked | +added | median apart | max apart | unplaced at 2 s | lost at 5 s | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 00 | DOMINUM — Killed by Life (en) | 42 | 12.7 s | 18.8 s | +6.1 s | 0.46 s | 1.77 s | 0 | 0 | 42 placed |
| 01 | DOMINUM — Die for the Devil (en) | 45 | 10.5 s | 18.1 s | +7.6 s | 0.73 s | 18.27 s | 16 | 13 | 29 placed |
| 02 | DOMINUM — Can’t Kill a Dead Man (en) | 49 | 9.9 s | 15.7 s | +5.8 s | 0.33 s | 9.77 s | 2 | 2 | 47 placed |
| 03 | Die Legende von Nord — Bösewicht (de) | 39 | 11.4 s | 17.0 s | +5.6 s | 0.78 s | 2.77 s | 1 | 0 | 38 placed |
| 04 | Feuerschwanz — Bastard of Asgard (de) | 61 | 10.7 s | 22.8 s | +12.1 s | 1.39 s | 5.17 s | 22 | 1 | 39 placed |
| 05 | Kupfergold — Zombie Malone (de) | 51 | 10.2 s | 16.2 s | +6.0 s | 0.64 s | 17.24 s | 10 | 9 | 41 placed |
| 06 | Lord of the Lost — 2000 Years a Pyre (en) | 37 | 12.1 s | 17.4 s | +5.3 s | 1.41 s | 33.95 s | 17 | 7 | 20 placed |
| 07 | Lord of the Lost — Argent (en) | 46 | 14.9 s | 21.1 s | +6.2 s | 120.21 s | 171.75 s | 45 | 45 | **all 46 kept, flagged** |
| 08 | Lord of the Lost — A War Within (en) | 40 | 14.1 s | 33.9 s | +19.8 s | 0.86 s | 23.23 s | 6 | 1 | 34 placed |
| 09 | Michael Jackson — Ben (en) | 28 | 7.3 s | 11.4 s | +4.1 s | 0.83 s | 2.13 s | 2 | 0 | 26 placed |
| 10 | Mono Inc. — A Love That Never Dies (en) | 43 | 13.2 s | 24.4 s | +11.2 s | 28.62 s | 87.00 s | 27 | 25 | **all 43 kept, flagged** |
| 11 | Powerwolf — Armata Strigoi (en) | 31 | 10.5 s | 16.8 s | +6.3 s | 54.83 s | 139.19 s | 31 | 31 | **all 31 kept, flagged** |
| 12 | Sabaton — A Lifetime of War (en) | 69 | 16.9 s | 27.9 s | +11.0 s | 94.53 s | 147.33 s | 69 | 69 | **all 69 kept, flagged** |
| 13 | Saltatio Mortis — Brunhild (de) | 52 | 9.8 s | 15.0 s | +5.2 s | 0.47 s | 24.00 s | 1 | 1 | 51 placed |
| 14 | Warkings — Azrael (en) | 55 | 11.6 s | 20.0 s | +8.4 s | 59.71 s | 80.31 s | 55 | 54 | **all 55 kept, flagged** |
| 15 | dArtagnan — Alles aus Liebe (de) | 43 | 9.3 s | 18.7 s | +9.4 s | 0.48 s | 6.79 s | 1 | 1 | 42 placed |

  The **alone** and **checked** columns are the same track aligned twice; **median/max apart** are the
  per-line distances between the two methods; **unplaced at 2 s** and **lost at 5 s** are the two rules
  of §9.38 counted separately. Every figure is on the GPU.
  - **The distances come in two shapes**, which is what the two constants are for: eleven tracks sit
    at a median under 1.5 s (jitter), five sit at 28–120 s (one method has lost the song). There is
    nothing in between — no track has a median between 1.5 s and 28 s.
  - **Cost of the per-line rule on a track where nothing is wrong:** a median of 1 line per track at
    2.0 s across the twelve agreeing tracks, against 5–10 at 1.0 s. That is the measurement the
    default came from.
  - **result:** pass — the four tracks the spike flagged are all flagged here, and the check never
    silently changed a stamp

- [x] **Y2 · M** — and when the two part company, **which of them is wrong**

  The question the rest of the section turns on, asked the same way the spike asked it: both methods
  against the library's own `.lrc` (not ground truth — the spike found two sidecars 6.3 s out — but
  independent of both aligners).

| track | \|primary − sidecar\| | \|second − sidecar\| | what §9.38 did |
|---|---|---|---|
| Feuerschwanz — Bastard of Asgard | median 1.35 s, p90 3.94 | **median 0.60 s**, p90 2.66 | 39 placed, 22 dropped |
| Lord of the Lost — 2000 Years a Pyre | median 0.86 s, p90 7.91 | median 0.90 s, p90 6.73 | 20 placed, 17 dropped |
| Lord of the Lost — Argent | **median 0.31 s**, p90 1.47 | median 120.21 s | all kept, flagged |
| Mono Inc. — A Love That Never Dies | **median 0.36 s**, p90 4.35 | median 28.26 s | all kept, flagged |
| Powerwolf — Armata Strigoi | **median 0.13 s**, p90 0.36 | median 54.82 s | all kept, flagged |
| Sabaton — A Lifetime of War | **median 0.95 s**, p90 1.11 | median 93.70 s | all kept, flagged |
| Warkings — Azrael | **median 0.10 s**, p90 0.22 | median 59.75 s | all kept, flagged |

  - **On all five tracks where the whole-track rule fires, the CTC pass was the accurate one** — twice
    to within a tenth of a second — and the Whisper pass had lost the song. The rule as written has
    caught a bad primary **nought times out of five** and discarded a good alignment five times.
  - The two tracks it does *not* fire on are the only two where the second method is competitive: on
    *Bastard of Asgard* it is the better of the two, which is also where the per-line rule earns its
    keep (the 22 lines it drops are the primary's worst).
  - The spike's own labels do not carry over: **Armata Strigoi** and **Azrael** were in its *agreeing*
    twelve, because its second arm was whisperX and this extra's is stable-ts, and they lose different
    songs.
  - **result:** the rule was changed on this evidence — see *A rule tried and overturned* below

  The verdict columns in both tables are what §9.38 does **now**. They are not what it did when the
  run was made: at that point the whole-track case placed nothing, which is exactly what the run
  disproved.

- [x] **Y4 · M** — the same sixteen tracks without a graphics card

  The GPU is the favourable case, so the whole set was aligned twice again on the processor alone
  (`timing_device = "cpu"`), 11:25 to 13:32. **Timing only**: this arm ran with the code as it stood
  before the rule was inverted, so its placement columns are not what ytalbum does now — the placement
  figures in Y1 and Y2 are the ones that count.

| # | track | GPU alone | GPU checked | CPU alone | CPU checked | the check costs |
|---|---|---|---|---|---|---|
| 00 | Killed by Life | 12.7 s | 18.8 s | 109.0 s | 186.4 s | +71% on the processor |
| 01 | Die for the Devil | 10.5 s | 18.1 s | 141.1 s | 229.0 s | +62% on the processor |
| 02 | Can’t Kill a Dead Man | 9.9 s | 15.7 s | 129.9 s | 200.7 s | +55% on the processor |
| 03 | Bösewicht | 11.4 s | 17.0 s | 133.8 s | 213.2 s | +59% on the processor |
| 04 | Bastard of Asgard | 10.7 s | 22.8 s | 165.2 s | 450.1 s | +172% on the processor |
| 05 | Zombie Malone | 10.2 s | 16.2 s | 167.1 s | 273.4 s | +64% on the processor |
| 06 | 2000 Years a Pyre | 12.1 s | 17.4 s | 209.5 s | 321.6 s | +54% on the processor |
| 07 | Argent | 14.9 s | 21.1 s | 266.6 s | 335.3 s | +26% on the processor |
| 08 | A War Within | 14.1 s | 33.9 s | 238.5 s | 639.1 s | +168% on the processor |
| 09 | Ben | 7.3 s | 11.4 s | 116.8 s | 156.4 s | +34% on the processor |
| 10 | A Love That Never Dies | 13.2 s | 24.4 s | 228.9 s | 357.6 s | +56% on the processor |
| 11 | Armata Strigoi | 10.5 s | 16.8 s | 183.9 s | 264.4 s | +44% on the processor |
| 12 | A Lifetime of War | 16.9 s | 27.9 s | 272.0 s | 330.3 s | +21% on the processor |
| 13 | Brunhild | 9.8 s | 15.0 s | 148.2 s | 202.1 s | +36% on the processor |
| 14 | Azrael | 11.6 s | 20.0 s | 179.8 s | 344.5 s | +92% on the processor |
| 15 | Alles aus Liebe | 9.3 s | 18.7 s | 138.1 s | 225.1 s | +63% on the processor |

  - **Medians: 11.1 s → 18.4 s on the card, 166 s → 269 s on the processor.** So the check costs about
    **+60%** either way, and the whole job is roughly **15× slower** without a GPU: three minutes for
    an alignment, four and a half with the second opinion, for a song of three to five minutes.
  - **Divergence does not predict the cost**, which is what I expected before the table existed and
    the table denies: the four tracks the methods argue about added a median of **+41%**, the twelve
    agreeing ones **+61%**. The two expensive outliers (*Bastard of Asgard* +172%, *A War Within*
    +168%) are one of each. What drives it is how much of the track Whisper re-decodes, not who is
    right.
  - **A library-wide pass is still a night's work on a processor** — 246 albums at four and a half
    minutes a track — which is what the `http` provider and `timing-serve` exist for.
  - **result:** pass

- [x] **Y3 · R** — `timing-serve` advertises what its own machine has

  Two servers side by side, from two different virtual environments:
  - with both extras: `{"capabilities": ["align", "transcribe"], "device": "cuda", "provider":
    "local", "verifies": true}`
  - with `ytalbum[timing]` only: `{"capabilities": ["align"], …, "verifies": false}`
  - the `http` provider in the app reports exactly what each answered — `['align', 'transcribe']` and
    `['align']` — so a second opinion, like the models, can live on another machine
  - `timing-serve` reads its *own* config for the two widths, since it is the machine doing the
    checking
  - **result:** pass

## Z. Two slots, two providers (P28, DESIGN §9.40, backlog 17)

Run 2026-09-27 through the real page on the same scratch copy of *Fegefeuer*, with the two slots
holding **different** providers — `timing_align_provider = "local"`, `timing_draft_provider =
"deepgram"` — and a deliberately fake Deepgram key, because nothing in this section is allowed to
reach a vendor.

- [x] **Z1 · R** — the two slots answer separately, and the page is told both
  - `/api/state`: `align_provider: local`, `draft_provider: deepgram`, `capabilities: ["align",
    "transcribe"]` — **a union neither provider could have reported alone**
  - `sends_audio: {align: false, transcribe: true}` and `price: {align: ["", ""], transcribe:
    ["$0.0043 per audio minute …", "2026-09-27"]}` — per capability, because the answer differs
  - the key is set (`keys.deepgram: true`) and appears nowhere in the payload
  - **result:** pass

- [x] **Z2 · R** — a slot offers only what could ever fill it
  - the aligning row's list is `none, local*, http, elevenlabs` — **no Deepgram**, which transcribes
    and says so; the drafting row's list has all five with `deepgram` selected
  - the two rows are named for the jobs, not for the machinery: *"Placing words on the clock"* and
    *"Drafting words for a track that has none"*
  - the aligning row's help says local and http send nothing off the network; the drafting row's says
    *"⚠ deepgram receives the audio of every track you use it on"* with the dated price
  - **result:** pass

- [x] **Z3 · M** — the same album, one job local and the other a vendor
  - *Berzerkermode* (has words): **⚖ align these words ran in 8 s with no confirm at all**, and the
    notice read *"timed by local/VOXPOPULI_ASR_BASE_10K_DE + htdemucs"*. This is backlog 17's whole
    point: choosing a vendor for drafts no longer gives up local alignment
  - *SGFRD Dragonslayer* (no words): the panel offered **✎ draft the words**, its title naming
    *deepgram* and saying the audio is sent there
  - **result:** pass

- [x] **Z4 · R** — the confirm names the slot's provider, and Cancel sends nothing
  - pressing draft raised: *"The audio of this track is sent to **deepgram** to write down what it
    hears… ytalbum never retries, so one press is one request."*
  - the dialog was **dismissed**, and the server log shows no request at all — which is what the
    confirm is for and why this case does not need a key that works
  - **result:** pass

## AA. The words being edited are what plays (P29, DESIGN §9.39)

Run 2026-09-27 through the real page (Playwright, a second server on `:8799` with its own
`XDG_CONFIG_HOME`), against a **scratch copy of Feuerschwanz — Fegefeuer** taken read-only out of the
real library. The specimen is the spike's *Berzerkermode*, whose LRCLIB sidecar is about 6.3 s early:
its saved stamps and an alignment of the same words differ by more than a line's width, which is the
whole point of the case. The audio element was muted.

- [x] **AA1 · R** — with the editor closed, nothing has changed
  - the panel is the saved sidecar as before: `0:12 Berzerkermode ON`, `0:31 …`, `0:31 …`, and no
    preview list exists in the DOM at all
  - **result:** pass

- [x] **AA2 · M** — the editor's list is the editor's words, not the file's
  - Edit → the list appears below the tools, **66 timed lines**, first stamp `12.52` — the file's
  - ⚖ align these words → the textarea fills with `[00:19.6]`, `[00:37.0]`, `[00:38.0]` and **the list
    follows within the debounce**: `19.6`, `37`, `38`. The file on disk still says `12.52 / 31.06 /
    31.92`
  - notice: *"timed by local/VOXPOPULI_ASR_BASE_10K_DE + htdemucs — a machine's proposal, nothing is
    saved yet: press ▶ on the first line. 1 stamped line without words was left as it was"*
  - **result:** pass

- [x] **AA3 · M** — the highlight follows the proposal, and the two truths are visibly different
  - a preview line was clicked (`1:08 Party hard berserkermode`) and the track played on from there
  - at player time **80.1 s** the marked line was the proposal's **79.8 s** *"Party hard berserkermode
    ON"*, and `.line.now` was inside the preview
  - the saved file would have marked a **different line** at that moment — its own `80.0 s` *"In der
    Birne bisschen dumm"* — computed from the sidecar independently. This is the user's *two views fed
    by two truths*, now decided the right way round
  - **result:** pass

- [x] **AA4 · M** — every kind of edit moves the highlight at once
  - **shift all +30 s**: the textarea became `[00:49.6] …`, and 0.8 s later the marked line was
    `150.3` at player time `153.2` — the walk agreeing exactly with the list's own stamps
  - **typing**: inserting `[00:05.0] Typed by hand` at the top put `0:05 Typed by hand @5` at the head
    of the list within the debounce
  - so both paths are covered: the `input` event a person's keystrokes fire, and the tools, which fire
    none and are routed through `editorText` for exactly that reason
  - **result:** pass

- [x] **AA5 · M** — Cancel gives the file back, Save makes the proposal the file
  - Cancel → editor gone, no preview in the DOM, the list is the saved `12.52 / 31.06 / 31.92` again,
    and at player time `205.2 s` the highlight was following **that** file's stamps (`185.66`)
  - Save (after aligning again) → the `.lrc` on disk begins `[00:19.6] Berzerkermode ON`, and the plan
    records `provenance.lyrics = user` with `lyrics_timed_by = "local/VOXPOPULI_ASR_BASE_10K_DE +
    htdemucs 2.11.0+cu130"` — the words the user's, the clock the provider's, as §9.36 requires
  - **result:** pass

- [x] **AA6 · R** — what the picture said that the assertions did not
  - the list rendered correctly and read as *a second copy of the lyrics*: nothing on screen said what
    it was, and the panel showed the same words twice with different numbers. A caption was added —
    *"what you are editing, as it will play — click a line to hear it"* — measured at **5.06:1**
    against the page background, above the 4.5:1 minimum for body text
  - **result:** pass after the fix

### A rule tried and overturned by its own measurement

The most useful thing this section did. §9.38 was built with the rule the spike's numbers suggested:
where two aligners disagree about most of a track, place **nothing**, because a half-filled editor is
worse than an empty one. The sixteen tracks say that rule is backwards.

- It fired on **5 of 16** tracks. On **5 of 5** the primary was the accurate method (Y2), twice to
  within a tenth of a second of the library's own sidecar, and the second method was 28–120 s out.
- So it caught a bad primary **nought times out of five**, and threw away a correct alignment **five
  times out of five** — on the longest tracks in the set, where hand-stamping 46 to 69 lines is the
  work the feature exists to avoid.
- The two tracks where it did *not* fire are the only two where the second method is competitive.
  The check's per-line rule is genuinely useful there, and it is kept.

What replaced it: in that regime **every primary stamp is kept** and the notice states the size of the
disagreement — *"a second method disagreed about the whole track — 45 of 46 lines more than 5 seconds
apart — so this is one method's word: play the first line before you save it."* The per-line rule is
switched off there too, since applying it would strip most of the stamps and make the change hollow.
Trusting the primary is a **policy**, not a measurement of *this* track; giving verify mode a way to
tell which method is lost is backlog 21, and this run is its evidence.

Two smaller lessons from the same run, both about believing a number:

- **The second method must hear the mix, not the stem.** Feeding Whisper the separated vocal — the same
  input the CTC pass needs — left 11 of 42 lines unplaced and the rest ~20 s early, which looked exactly
  like "the primary is wrong" until both were compared against a sidecar. On the mixed track the same
  model landed within 0.7 s. Two methods sharing a front end are not two opinions.
- **A silent fallback invalidates a timing.** When the card is full, Whisper drops to the processor and
  the work takes about **thirty times** as long. Nothing says so unless something logs it, and a
  measurement that has quietly changed device is not the measurement anybody asked for. It cost twenty
  minutes of a run before the progress bar gave it away.

### Three defects of my own, every one found by running it

Worth writing down because the green suite had no opinion on any of them: all three are about a
second model on a real graphics card, and none is visible from the unit tests.

1. **The CUDA-library guard sat where the error does not happen.** `ctranslate2` touches no CUDA
   library until the *first inference*, so loading `large-v3` on the GPU succeeded and
   `libcublas.so.12 is not found` arrived a minute later, killing the first run on track one. The
   retry now wraps the inference. (The trap itself is known from the spike; the guard's placement was
   the new mistake.)
2. **8 GB does not hold the aligner, the separator and 3 GB of Whisper.** The cache is now emptied
   before the check, an out-of-memory is retried on the processor with a message saying why, and — the
   one that mattered — **a failure of the check never loses the alignment**: the primary result comes
   back with `unchecked` naming the reason. Before that, three separate failures took the whole job
   with them.
3. **Whisper squatted on the card between tracks**, so track *two*'s separation died with "tried to
   allocate 1.34 GiB". It is released after each check on a GPU, and kept on a processor where there
   is nothing to compete for. A related trap, met twice: when the card is full the fallback to the
   processor is *silent* unless something logs it, and the same work then takes **thirty times** as
   long — the second time it cost me twenty minutes of a measurement run before I noticed the
   progress bar.

Beside them, one defect that was not mine: the `timing` extra installed **without numpy**, because
`uv` resolves **demucs 4.1.0** on Linux and 4.1.0 declares no numpy at all (4.0.1 did, behind a darwin
marker) while importing it at module level. So `ytalbum[timing]` installed cleanly and the provider
then reported *"the local timing provider needs the optional extra"*. Found by the reviewer installing
the extra into a clean project venv — not visible in a working tree that has numpy for other reasons.
Fixed by declaring it, and verified on two fresh venvs: `.[timing]` → `['align']`, both extras →
`['align', 'transcribe']`.

## AB. Giving the graphics card back (P30, DESIGN §9.41, backlog 18)

Run 2026-09-27 on this laptop's RTX 4060 (8 GB), with `nvidia-smi
--query-compute-apps=used_memory` sampled around real alignments of *Berzerkermode* — through the
app's own service on `:8799` and through `ytalbum timing-serve` on `:8793`, both on the scratch copy
of *Fegefeuer*.

- [x] **AB1 · M** — what the defect actually was, measured before the fix
  - the scratch server, minutes after its last alignment and with an empty queue, held **3314 MiB**
  - that is the same number as the *peak during* an alignment: nothing had been given back at all.
    The installed service showed the same thing at 2894 MiB while P27 was being measured, and that is
    what made a second process's separation die with "tried to allocate 1.34 GiB"
  - **result:** the defect, confirmed

- [x] **AB2 · M** — the app's service gives it back when the queue empties
  - one alignment through `/api/align`: peak **3314 MiB**, and **180 MiB** a few seconds later, with
    the job `done` and 65 of 65 lines placed
  - the 180 MiB is the CUDA context, which belongs to the process until it exits; everything else is
    returned
  - **result:** pass

- [x] **AB3 · M** — `timing-serve` lets go of the models after its idle time
  - `timing_idle_minutes = 0.5`, so a 30-second window. One alignment: 11.8 s, 4629 bytes of answer,
    **3314 MiB** held. Then, sampled every 5 s: `3314, 3314, 3314, 3314, 3314, 3314, 180 …` — it let
    go between +30 s and +35 s, exactly when it said it would
  - the server's log says it in words: *"let go of the models; the next request loads them again"*
  - **result:** pass

- [x] **AB4 · M** — and the next request pays for it in nothing anybody can measure
  - a second alignment after the release: the log shows the reload (*"loading the separator (htdemucs,
    81 MB on first use)"*, *"loading the de aligner …"*) and the request took **11.2 s** against the
    first one's 11.8 s — the weights come off a warm disk
  - **result:** pass

### The defect this found: a tidy-up that did not know the thing was in use

The first live run of AB3 used a six-second idle window against an eleven-second alignment, and the
request died:

```
File "…/timing_local.py", line 346, in _vocals
    return vocals, int(self._separator.samplerate)
AttributeError: 'NoneType' object has no attribute 'samplerate'
```

The watcher had released the models **while the request was still being served**. Two fixes, and both
are the same lesson from different ends: a request now holds the `Idle` object while it works, so the
watcher releases only when nothing is being served; and `_vocals` keeps the separator in a local
rather than reading a cache twice that another thread may empty in between. A unit test with a fake
clock now holds a request open across several idle periods and asserts that nothing is released,
which is the case the live run found and no earlier test could have.

It is worth saying how it was found: not by the suite, which was green, but by pointing the thing at
a real server with a deliberately short timer. A timer set to a realistic five minutes would have
hidden this for as long as nobody aligned a very long track.

## AC. Giving the words back to LRCLIB (P31, DESIGN §9.42, backlog 19)

Run 2026-09-27 through the real page on a scratch copy of *Fegefeuer*, against **a server of my own
speaking LRCLIB's documented publish flow** (`YTALBUM_LRCLIB_BASE`). **Nothing in this section
reached lrclib.net**: their database is public and permanent, and test words do not belong in it.
The one live request made anywhere in this package was a single `POST /api/request-challenge` — it
publishes nothing — to learn the real difficulty, which turned out to be exactly the `000000FF…`
their documentation prints.

- [x] **AC1 · R** — LRCLIB's own words are never offered back to LRCLIB
  - *Berzerkermode* with its LRCLIB sidecar: the panel has **no publish button at all**, and the
    payload says why: `"these are lrclib's own words, not yours"`
  - *Uruk-Hai*, whose sidecar is plain text: also refused, with `"only timed lyrics are worth giving
    back — these have no timestamps"`
  - **result:** pass

- [x] **AC2 · M** — once the words are the user's, the button appears and says everything first
  - the same track, shifted by −0.5 s in the editor and saved: the head reads *"with timestamps ·
    yours"* and **"↑ publish to lrclib"** is there
  - the confirm: *"These words are about to be published to LRCLIB, for everyone. / Feuerschwanz —
    Berzerkermode / album: Fegefeuer / length: 219 s (the file's own, which is what the timestamps
    follow) / 66 lines, with their timestamps, and the same words without them / LRCLIB is a public
    database and takes no account. A publish cannot be taken back… / OK: publish them. Cancel:
    nothing leaves this machine."*
  - **result:** pass

- [x] **AC3 · M** — what actually goes, and what comes back
  - the fake received `trackName: Berzerkermode`, `artistName: Feuerschwanz`, `albumName: Fegefeuer`,
    `duration: 219.14` — **the file's length, not the video's** — 66 synced lines and the same words
    with the stamps taken off, under a token `qaPrefixForTheCatalog:56997` whose SHA-256 it verified
    against the target itself
  - the job log, in full: *"publishing Feuerschwanz — Berzerkermode to lrclib (66 lines, 219 s) —
    this is public and cannot be undone"*, *"solving lrclib's challenge (target 0000ffff…)"*,
    *"solved in 0.0 s"*, *"published Berzerkermode — thank you: the next person looking for this song
    finds it"*
  - the panel comes back with **"✓ published to lrclib"**, disabled, titled *"These words were
    published on 2026-09-27T13:07:36+00:00…"*, and the plan holds
    `lyrics_published = {"at": …, "sha": "1cc3adfbd63bc387"}` — a fingerprint, with none of the words
    in it
  - **result:** pass

- [x] **AC4 · M** — the same words are never sent twice, edited ones may go again
  - asked again: `failed — Berzerkermode: already published`, and the fake saw **no second request**
  - a line added to the sidecar and asked again: accepted, because "I changed it" is the only
    sensible reading of new bytes
  - **result:** pass

- [x] **AC5 · M** — a refusal changes nothing on disk
  - the fake set to refuse with *"This track already has these lyrics"*: the job is `failed`, the
    message is **LRCLIB's own**, `lyrics_published` on disk is still `None`, and the button is still
    there to try again
  - **result:** pass

### The evidence method that nearly lied here

`/api/state` shows only the **last three lines** of each job's log (`Job.summary(full=False)`), and I
read the publish job from there: the first line — the one that says a publish is about to happen and
cannot be undone — was simply outside the window, and for several minutes I believed it was missing
and was hunting a bug in my own logging. `/api/job?id=N` returns the whole log and showed all four
lines. **A truncated view is not evidence of absence**; ask for the full record before reporting that
something did not happen.

## AD. Offering an album to MusicBrainz (P32, DESIGN §9.43, backlog 20)

Run 2026-09-27 through the real page on a scratch copy of *Fegefeuer*, with `YTALBUM_MUSICBRAINZ_WEB`
pointing at **a stand-in that prints what a seeded form arrived with**. Nothing here touched
musicbrainz.org: an edit form opened for real is a real edit waiting to be submitted, and a test has
no business anywhere near one. The album's `mbid` was cleared and two recordings given a length
45 s from the file's, to make the two cases exist.

- [x] **AD1 · R** — offered where MusicBrainz has nothing, refused where it has or would not want
  - the album head shows **"Add to MusicBrainz"**
  - made a compilation: the button is gone and the head says, in its place, *"MusicBrainz wants
    releases that exist as releases, not compilations"*
  - `/api/mbseed` refuses the same album with 400 and that sentence, so a page that asked anyway
    would be told no by the server
  - **result:** pass

- [x] **AD2 · M** — the form that opens, and what it carries
  - the confirm first: *"MusicBrainz's release editor is about to open, with 11 tracks already filled
    in… ytalbum submits nothing. The form opens in a new tab, signed in as you, and nothing reaches
    MusicBrainz until you press their own submit button… MusicBrainz wants releases that were really
    released."*
  - the stand-in received a POST to `/release/add` with **41 fields**: `name = Fegefeuer`,
    `artist_credit.names.0.name = Feuerschwanz`, `type = Album`, `mediums.0.format = Digital Media`,
    `events.0.date.year = 2023`, the playlist's URL, the edit note naming ytalbum and asking for a
    check, and eleven tracks — `mediums.0.track.0.{name, number, length}` with `length = 248581`,
    milliseconds measured from the file
  - no track artist credit was seeded on this album, because none of its tracks differs from the
    album's artist (the differing case is covered in `tests/test_seed.py`)
  - **result:** pass

- [x] **AD3 · M** — the correction seeding cannot make
  - the length chip on a track whose recording MusicBrainz has 45 s shorter is now a **button**; its
    title reads *"MusicBrainz 3:23.6 · LRCLIB 3:35.1 · this file 4:08.6 — an intro or outro to cut? ·
    click to open the recording on MusicBrainz, where their length can be corrected"*
  - the confirm names both numbers and what ytalbum cannot do: *"ytalbum cannot seed a correction —
    the seeding format is for releases, not recordings — so the change is yours to make, and only if
    you are sure: a file can be shorter because it was trimmed, or longer because the upload has an
    intro. Nothing is sent from here."*
  - the stand-in logged `GET /recording/abe94365-…/edit`
  - a track with a length difference but **no recording id** keeps a plain chip: there is nothing to
    open
  - **result:** pass

### The defect the page found on itself: two affordances, one fact

The first version put a separate badge in the track row — `MB ≠ 3:23.6` — next to the length chip.
Looking at the row showed what that meant: **the chip already prints those two numbers**, and has
since §9.31, with the reading *"an intro or outro to cut?"* — while the new badge printed them again
with the opposite reading, *"their number may be wrong"*. Two affordances for one fact, disagreeing
about what it means.

The fix was to make the chip itself the button where MusicBrainz knows the recording and the gap is
large, which is P14's lesson a second time: a diagnosis that is worth showing is worth acting on, and
the place to act is where the diagnosis already is.

## AE. Telling which method lost the song (P33, DESIGN §9.44, backlog 21)

Run 2026-09-27 on the sixteen P27 tracks (copied read-only out of the real library again) plus two
**held out**: Mono Inc. — *Princess of the Night* and Saltatio Mortis — *Seitdem du weg bist*, which
were measured only after the thresholds below were written down. Each track was separated once, then
aligned by both methods, and every candidate signal was computed from the recorded stamps — the run
writes the raw stamps, the sung stretches and both methods' own confidences, so a signal can be
designed and re-designed without asking the models again.

- [x] **AE1 · M** — what the two methods' answers look like, and what decides between them

  `span` is how much of the sung part of the track a method's stamps cover; `piled` is the share of
  its stamps within a third of a second of the one before.

| track | CTC span | Whisper span | Whisper piled | verdict |
|---|---|---|---|---|
| DOMINUM — Killed by Life | 0.87 | 0.86 | 0.00 | cannot tell |
| DOMINUM — Die for the Devil | 0.99 | 0.98 | 0.11 | cannot tell |
| DOMINUM — Can't Kill a Dead Man | 0.98 | 0.95 | 0.00 | cannot tell |
| Die Legende von Nord — Bösewicht | 0.93 | 0.93 | 0.00 | cannot tell |
| Feuerschwanz — Bastard of Asgard | 1.12 | 1.12 | 0.03 | cannot tell |
| Kupfergold — Zombie Malone | 0.98 | 0.98 | 0.04 | cannot tell |
| Lord of the Lost — 2000 Years a Pyre | 0.97 | 0.93 | 0.00 | cannot tell |
| **Lord of the Lost — Argent** | 0.99 | **0.41** | 0.26 | **Whisper lost** |
| Lord of the Lost — A War Within | 0.98 | 0.98 | 0.00 | cannot tell |
| Michael Jackson — Ben | 0.98 | 0.96 | 0.00 | cannot tell |
| **Mono Inc. — A Love That Never Dies** | 0.95 | **0.67** | 0.21 | **Whisper lost** |
| **Powerwolf — Armata Strigoi** | 0.98 | **0.52** | 0.06 | **Whisper lost** |
| **Sabaton — A Lifetime of War** | 1.00 | **0.45** | 0.36 | **Whisper lost** |
| Saltatio Mortis — Brunhild | 0.99 | 0.98 | 0.00 | cannot tell |
| **Warkings — Azrael** | 1.09 | **0.70** | 0.24 | **Whisper lost** |
| dArtagnan — Alles aus Liebe | 0.97 | 0.97 | 0.00 | cannot tell |
| *held out:* Mono Inc. — Princess of the Night | 0.98 | 0.98 | 0.00 | cannot tell |
| *held out:* Saltatio Mortis — Seitdem du weg bist | 0.99 | 0.98 | 0.00 | cannot tell |

  - **five right, none wrong, none missed.** The five methods that lost the song span **0.41–0.70**
    of the singing; all twenty-seven answers that followed it span **0.86–1.12**
  - *Bastard of Asgard*, the one track where the CTC pass was the worse of the two, comes out
    **cannot tell** — which the task allowed, and which falls back to the policy of §9.38
  - the thresholds are **0.75** of the singing and **12%** piled. Any floor from **0.70 to 0.85**
    gives these eighteen verdicts unchanged, so the number is not load-bearing; the piling rule alone
    gets four of the five, and is kept because it catches a different shape of failure
  - **the held-out pair was measured after the thresholds were fixed** and answered correctly
  - **result:** pass

### Two signals that do not work, with the numbers that killed them

Both are still **recorded on every alignment and never judged**, so that nobody has to take this on
trust — and so nobody proposes them again.

- **"Its stamps fall where nobody sings"** (the first idea, and the obvious one). These tracks are
  **55–86% singing**, so a method that is somewhere else entirely still lands *inside* singing nearly
  every time. A human's own stamps shifted by a full minute — the cleanest synthetic "lost" there is,
  and it touches none of the judged tracks — put only **12–30%** of themselves in silence. On *Argent*,
  where the second method is **120 s** out, **0 of its 46 stamps** fell in silence. The sharper form,
  "is a phrase starting here", is no better: on a good track the **human's own** sidecar has 26 of 42
  stamps away from any detected onset, because lines begin inside sung stretches, not at their edges.
- **"Each method's own confidence"**, which looked decisive for an hour and is not. The CTC pass and
  the decoder do not report the same quantity (0.13–0.41 against 0.77–0.98 for the same quality of
  work), so no shared threshold is honest — and the per-method version fails on its own terms:
  Whisper **lost** *A Love That Never Dies* at **0.60**, higher than the CTC pass's **0.36** on
  *Argent* where it was right, and on *Armata Strigoi* both collapse at once (0.07 and 0.03) so it
  names nobody.

### And one evidence method that lied, again

A patch that adds a field to a measurement script was checked by grepping for the word `stamps` —
which the script already contained six times, in `lines_and_stamps` and elsewhere. The patch had
never applied (a self-matching `pkill` had killed the shell before the heredoc ran), the grep said
otherwise, and a forty-minute run produced a file with no raw stamps in it. **Verify a patch by
something new and unique to it** — here `"raw_stamps"` — never by a word the file could already have.

## AF. A draft that reads like a song (P34, DESIGN §9.45)

From the user's own test: **“draft the words” on Mr. Hurley & Die Pulveraffen — *Blackbeard*** (3:38)
came back as **eleven lines**, one of them a whole verse of three sung lines, with a minute of silence
in the middle that nothing on the screen mentioned, under the notice *“11 of 11 lines came with a
time”*. Two faults, one ours and one the vendor's, measured 2026-09-27 on a read-only copy.

- [x] **AF1 · M** — the line builder, on the user's own response

  No new request: the same Deepgram answer, re-read.

| | lines | longest line | gap markers | what the notice says |
|---|---|---|---|---|
| before | 11 | a whole verse (three sung lines) | none | "11 of 11 lines came with a time" |
| after | **17** | **6.5 s** | **4** | "Words for 1:01.3 of 3:38.1 of audio, with 4 gaps longer than 6 s marked in the text" |

  - lines now break where the singing pauses (over 0.6 s), not where the vendor put a full stop;
    where a line must still be split, it splits **at its own longest internal pause** — the first
    attempt cut at the twelfth word and left a line reading just *“Rauch.”*
  - the holes are visible: `… (46 s without words)` stamped where the singing stopped
  - **result:** pass

- [x] **AF2 · M** — what the transcriber should listen to: the mix, or the separated voice?

  Four arms on the same 218 s track, scored against **LRCLIB's own synced lyric for the recording**
  (52 usable lines), which is a reference rather than my ear. Two of the arms are paid requests; that
  is all the money this section spent.

| arm | lines | covered | gaps | longest line | reference lines found | time |
|---|---|---|---|---|---|---|
| whisper · mix | 71 | 152.6 s | 0 | 8.9 s | 28 of 52 | 208 s |
| whisper · **stem** | 54 | 144.4 s | 1 | 11.0 s | **38 of 52** | 116 s |
| deepgram · mix | 17 | 61.3 s | 4 | 6.9 s | 16 of 52 | 3.9 s |
| deepgram · **stem** | 27 | 103.3 s | 4 | 6.6 s | **32 of 52** | 21 s |

  - **the voice wins for both vendors**: Deepgram doubles (16 → 32), the local decoder gains a third
    (28 → 38). Drafting now separates first wherever the `timing` extra is installed, and says so
  - **more lines is not better**: whisper on the mix wrote the *most* lines (71) and found the
    *fewest* of the reference's (28) — it writes words over the instrumental passages. The stem arm
    produced 54, almost exactly the reference's 52, and found ten more of them
  - sending the isolated voice instead of the record is also less of somebody's music leaving the
    house, which is worth having for free
  - **result:** pass

### The thing this found that was bigger than the package

**LRCLIB has this song's words** — entry 24423598, 53 timed lines, the same recording. ytalbum
refused it because the file is **218.06 s** and the entry says **213.68 s**: 4.4 s apart, against
`TOLERANCE = 3.0`. The user was offered a machine's guess for a song whose words were one lookup
away, and the eleven lines they got were a poor copy of something that already existed. A 2% length
difference on a four-minute song is ordinary, so this is unlikely to be one track. It is not fixed
here — a tuned constant with its own reasons does not get widened inside a package about line breaks
— and it is queued as its own measurement-first package.

## AG. An entry that is nearly this recording (P35, DESIGN §9.46)

**The current gate has never refused something it could have matched.** Over this library's 1089
tracks with no words at all, the number whose cached lrclib entry sits within the 3 s tolerance is
**zero**. Everything below is about the boundary, not about a bug.

Measured 2026-09-27, read-only, on the real library: every wordless track was asked about (the pass
made **no new requests** — a lyrics run had already cached each one), and every candidate with the
same artist and title was measured against the file it claims to describe.

    wordless tracks                                              1089
      a same-artist, same-title candidate with words exists       203
        within 3 s — the gate takes it today                        0
        over 3 s but within 3% of the file's length                74
        beyond 3%                                                 129
      no candidate with words at all                              886

- [x] **AG1 · M** — do the words of a near miss belong to this recording?

  The instrument is the aligner, and the criterion was written down **before** the run and not moved:
  *fits* = the entry's stamps span ≥ 0.85 of the singing with ≤ 10% of lines unplaced; *does not fit*
  = span < 0.75, span > 1.15, or > 25% unplaced; anything else is inconclusive and counted as neither.
  The reference, measured before the criterion was fixed: LRCLIB's 52 lines against the user's own
  *Blackbeard* — the 4.4 s miss that started this — span **0.968**, 52 of 52 lines placed.

| population | fits | inconclusive | does not fit | uncheckable |
|---|---|---|---|---|
| near misses, 3 s to 3% (74) | **62** | 10 | 2 | — |
| beyond 3%, the "control" (129) | **91** | 19 | 18 | 1 |

  - the control is not a control: **71% of the entries beyond 3% are still this recording's words.**
    The length gap is a poor proxy for "the same recording"; the alignment is a direct measurement
  - fits are not marginal: span median 0.96 among the near misses, lowest 0.87
  - **the span needed a ceiling, and I had not written one.** Four control candidates passed as fits
    with the lyric spanning *more* than the singing — the worst an 83 s file against a 222 s entry at
    span 1.60. A span over **1.15** is now a non-fit. The verdict is a pure function of two recorded
    numbers, so both populations were recounted from the recordings rather than re-aligned
  - **result:** pass

- [x] **AG2 · M** — what the non-fits are, one by one

  Every one of the 18 has **0% of its lines unplaced**: the aligner found all the words, in order, in
  our audio. They are not wrong songs. What they are is a different *cut*:

| track | our file | their entry | span | what it is |
|---|---|---|---|---|
| Schandmaul — Willst du (Live) | 305 s | 289 s | 0.45 | a live recording |
| Schandmaul — Dein Anblick (Live) | 368 s | 313 s | 0.64 | a live recording |
| ASP — Schneefall in der Hölle (Plakat…) | 342 s | 407 s | 0.65 | a marked variant |
| Feuerschwanz — Ringelpietz (mit Anfassen) | 83 s | 222 s | 1.60 | a marked variant |
| Faun — Wenn wir uns wiedersehen | 78 s | 199 s | 1.17 | a clip: our file is much shorter |
| Lord of the Lost — Last Words | 344 s | 264 s | 0.66 | a longer cut |
| Lord of the Lost — Prison | 346 s | 302 s | 0.66 | a longer cut |
| Lord of the Lost — Sex on Legs | 268 s | 238 s | 0.72 | a longer cut |
| Lord of the Lost — Dry the Rain | 277 s | 293 s | 0.57 | a shorter cut |
| Darkher — Ghost Tears | 243 s | 254 s | 0.58 | a shorter cut |
| Joachim Witt — Gloria | 286 s | 317 s | 0.68 | a shorter cut |
| Assemblage 23 — Lullaby | 335 s | 324 s | 0.72 | a longer cut |
| Saltatio Mortis — Wir sind Papst | 233 s | 222 s | 0.73 | a longer cut |
| Feuerschwanz — Metfest (×2 in the library) | 253 s | 243 s | 0.73 | a longer cut |
| Schandmaul — Knüppel aus dem Sack | 291 s | 270 s | 0.75 | a longer cut |
| Warkings — Stahl auf Stahl | 226 s | 216 s | 1.23 | a longer cut |
| Kupfergold — Met Kasalla Uss Valhalla | 239 s | 177 s | 1.29 | a longer cut |

  - **the only wrong song in 203 candidates** is *Feuerschwanz — Gangnam Style* among the near
    misses, at **71% of its lines unplaced** — a title collision, and the one case the aligner
    rejects outright
  - so the alignment answers two questions at once, and they are different questions: *how many
    lines it can place* says whether these are the song's words, *how much of the singing they span*
    says whether the entry's clock is this recording's
  - **result:** pass — and this is what the three outcomes in §9.46 are built on

- [x] **AG3 · M** — the outcomes, through the page, against a stand-in lrclib

  A scratch copy of *Fegefeuer* with its words removed, and a server of my own answering with
  deliberate near misses. Nothing touched lrclib.net.
  - a 5.6 s near miss: the panel offered *"⚖ check them against the audio"*, the check aligned the
    entry's words (**span 0.976, nothing unplaced**) and took **words and timings**; the sidecar was
    written, the track retagged, and `lyrics_fit` recorded the two numbers
  - an entry 124 s away: **no alignment was spent**, and the panel says *"lrclib has words for this
    title, for a 1:35 recording; this file is 3:39.1 — probably another cut. Nothing was taken; you
    can take the words as plain text."*
  - taking them by hand: status `plain`, `lyrics_id` set, **owner unchanged** — lrclib's words stay
    lrclib's however they were taken — and the sidecar holds the words without stamps
  - **result:** pass. The *another cut* and *reject* outcomes are covered by the service tests with
    fakes rather than here: a stand-in's synthetic words align to anything, so the page could not
    have shown those two honestly

### The defect the panel showed and no test had

A track that already had words was still being offered *"take the words as plain text"*, because a
verdict recorded earlier stayed in the plan and the panel read it without asking whether the words
question was still open. Found by preparing a scratch album carelessly — the plan said "no words"
while the `.lrc` files were still on disk, so `reconcile` made them the user's own words and the
stale verdict kept offering more. Now the panel says only *where words came from* once there are any.

## AH. A corpus that keeps the measurements (P37, docs/regression.md)

The numbers P27, P33 and P35 produced were in prose and in scratch directories. This turns them into
tests: a fast half from the recordings that runs every time, and a slow half that does the work again
on real audio. Details and the rule for adding a case are in `docs/regression.md`.

- [x] **AH1 · R** — the fast half asserts outcomes, not numbers

  27 cases over five new fixtures (`verify_spans`, `verify_tracks`, `verify_sidecar`,
  `separation_arms`, `verify_signals`) plus the existing `lrclib_near_misses`. Each asserts a
  verdict; where a threshold decides, it also asserts the range the threshold may move within.
  - **result:** pass, 0.04 s, no model and no audio

- [x] **AH2 · R** — the four dead heuristics each have a case that must keep failing them

  Silence (Argent: 0 of 46), confidence (the kindest cut misclassifies 3 of 32), raw-versus-stem
  agreement (would reject 11 correct alignments; Berzerkermode and Clocks agree while both are 6 s
  wrong), and place-nothing-on-disagreement (five for five it discarded the accurate method, 244
  stamps).
  - **result:** pass

- [x] **AH3 · M** — the slow half does the work again

  `YTALBUM_CORPUS_AUDIO=1 uv run pytest -m slow`, against the real library, audio copied read-only
  into pytest's temp directory. **3 passed, 1 skipped, 89 s.** Berzerkermode's first line landed at
  19 s against LRCLIB's 12.5 s, the +6.4 s the spike measured; Blackbeard's entry came out
  `words+stamps` as recorded; the trimmed copy of *Und 'n Tripper* put its stamps in the file's own
  clock, 30 s behind the whole file's. Argent skipped: the `timing-check` extra is not installed.
  - **result:** pass

- [x] **AH4 · R** — what the corpus cannot reach

  P33's raw stamps, sung stretches and confidences went with the scratch virtualenv. Nothing above
  depends on them; designing a **new** signal does, and costs about 40 minutes on a GPU plus the
  3.09 GB extra. Recorded in `docs/regression.md` rather than re-run.
  - **result:** pass, as a limit rather than a gap

### Three defects in the corpus's own cases, found by running it

- **Two API names invented rather than read.** `ytalbum.config.load_config` and
  `ytalbum.lyrics.candidates` as a module function: neither exists (`config.load`, and `candidates`
  is a method on `Lrclib`). The first broke collection of a suite that was otherwise skipped, which
  is worse than it sounds: **a skipped suite still has to import**.
- **The wrong entry, and then no span at all.** The Blackbeard case first took whichever lrclib
  candidate came back first — a different entry from the recorded one, and a different measurement
  wearing the same name. Pinned to the recorded `lrclib_id`. It then reported `span None`, because
  span needs the separated voice: it is the provider's `own_span`, not something to recompute from
  the stamps.
- **A case that skipped for a reason that was my error.** *Und 'n Tripper* is by **Kupfergold**, not
  the artist the case named, so it skipped with "not in this library" — a skip that reads like a fact
  about the library and was a fact about the test. A skip message has to be true.

## Results

| Date | Cases run | Passed | Failed | Notes |
|---|---|---|---|---|
| 2026-09-28 | the AH cases (P37: a corpus that keeps the measurements) | 4 | 0 in the design; 3 of my own in the corpus's own cases (two invented API names, the wrong lrclib entry and then no span, and a skip message that was my error rather than a fact about the library) | 27 fast cases over six fixtures, no model and no audio, 0.04 s. The slow half against the real library: 3 passed, 1 skipped (no `timing-check` extra), 89 s. Four dead heuristics each have a case that must keep failing them. 710 pytest + 88 node. |
| 2026-09-27 | the AG cases (P35: an entry that is nearly this recording) | 3 | 0 in the design; 1 of my own (a criterion with no ceiling, declared and fixed before the design), 1 in the page (a stale verdict offering words to a track that had them) | 203 real candidates aligned against the files they claim to describe. 71% of the entries *beyond* 3% are still the recording's words, so length is a poor proxy and the alignment is the measurement. The only wrong song in 203 is one title collision. 683 pytest + 87 node. |
| 2026-09-27 | the AF cases (P34: a draft that reads like a song) | 2 | 0 | The user's own draft, re-read: 11 lines → 17, longest a whole verse → 6.5 s, four gap markers, and a notice that counts seconds instead of lines. Four arms scored against LRCLIB's lyric for the recording: separating the voice doubles what Deepgram hears (16 → 32 of 52) and gains the local decoder a third (28 → 38). 665 pytest + 78 node. |
| 2026-09-27 | the AE cases (P33: which method lost the song) | 1 | 0 | Eighteen tracks, both methods, every candidate signal. Coverage of the singing separates cleanly (lost 0.41–0.70, good 0.86–1.12): five right, none wrong, none missed, and the held-out pair correct after the thresholds were fixed. Two signals measured and discarded, including the one the task specified. 646 pytest + 77 node. |
| 2026-09-27 | the AD cases (P32: offering an album to MusicBrainz) | 3 | 0 in the design; 1 of my own (a second badge printing the length chip's own numbers with the opposite implication), fixed by making the chip the button | Against a stand-in for their release editor — **nothing touched musicbrainz.org**. 41 seeded fields including eleven tracks with millisecond lengths measured from the files, the refusal for compilations shown where the button would be, and the recording deep link the seeding format cannot replace. 636 pytest + 75 node. |
| 2026-09-27 | the AC cases (P31: publishing to LRCLIB) | 5 | 0 | Against a server speaking LRCLIB's documented publish flow — **nothing reached lrclib.net**, and the only live request in the package was one `request-challenge`, which publishes nothing. The gate, the confirm, the payload (with the file's length), the fingerprint, the never-twice rule and a refusal that changes nothing on disk. 621 pytest + 70 node. |
| 2026-09-27 | the AB cases (P30: giving the card back) | 4 | 0 in the design; 1 defect of my own (the idle timer released the models out of a running request), found live and fixed | 3314 MiB held after an alignment before the fix; 180 MiB after it, which is the CUDA context. `timing-serve` let go 30–35 s into a 30 s idle window and said so in its log; the next request reloaded in 11.2 s against 11.8 s. 609 pytest + 67 node. |
| 2026-09-27 | the Z cases (P28: two slots) | 4 | 0 | `local` aligning and `deepgram` drafting on one album: the capability union, the panel offering no Deepgram for aligning, an 8 s local alignment with no confirm, and a draft confirm naming Deepgram that was cancelled — nothing was sent, and the key used was deliberately fake. 601 pytest + 67 node. |
| 2026-09-27 | the AA cases (P29: the editor's own clock) | 6 | 0 in the design; 1 blemish of my own (the list had no name and read as the saved words shown twice), fixed before the commit | Scratch copy of *Fegefeuer* through the real page. The editor's list showed the alignment's `19.6` while the file still said `12.52`, and at player time 80.1 s the highlight marked the proposal's line where the file would have marked a different one. Cancel gave the file back; Save wrote the proposal with `lyrics_timed_by`. 593 pytest + 67 node. |
| 2026-09-27 | the Y cases (P27: a second opinion) | 4 | 0 in the design; 3 defects of my own (a CUDA guard in the wrong place, a full graphics card taking the whole job with it, a model squatting on the card between tracks) and **one rule of the design overturned by its own measurement** | Sixteen real tracks, 731 lines, aligned twice on a GPU and twice on a processor, plus both methods compared against the library's own sidecars. The check costs about +60% on either device. The whole-track rule that placed nothing was measured to be backwards — five for five it discarded a correct alignment — and now keeps every stamp and says so. 593 pytest + 60 node. |
| 2026-09-27 | the X cases (P26: paid providers) | 7 | 0 in the design; 1 defect of my own (a finished draft discarded the editor), fixed | Against a server speaking the vendors' documented shapes — **nothing was ever billed**. Both vendors' capabilities, the confirm, the draft, the key redaction and the dated price all verified through the real page. 581 pytest + 56 node. |
| 2026-09-27 | the W cases (P25: a timing provider) | 8 | 0 in the design; 2 defects of my own (the editor discarded when a read job finished, a missing import), both fixed before the commit | Scratch library with the spike's two mis-timed specimens. Local provider 12.2 s (GPU) and 108.4 s (CPU) for the same track, agreeing on 64 of 65 lines; the HTTP provider 19.3 s from an app with no torch installed. Both `.lrc` files corrected by about 6.4 s. 567 pytest + 52 node. |
| 2026-09-27 | the V cases (P23: stamping to the file's clock) | 7 | 0 in the package; 1 blemish of my own (the shift field's width), fixed before the commit | A track trimmed to 1:30–5:20, so the player's clock and the file's differ by 90 s. The tap wrote `[00:24.9]` from a player time of 114.870, which is the arithmetic the case asks for. 554 pytest + 45 node. |
| 2026-09-27 | the U cases (P22: audio from another video) | 8 | 0 in the package; 1 blemish of my own (the field took the whole line), fixed before the commit | S1 pointed at S10 on a scratch library, through the real page: the confirm named the trim, the uploader followed the audio both ways, the ⏱ chip went +4:01 → 0:00 → +4:01, the user's words were never touched. 554 pytest + 32 node. |
| 2026-09-27 | the T cases (P21: `--edge` and the grip) | 7 | 0 | CSS only, measured in the page and looked at in every view and both themes. The one question the decision asked — whether `--edge` makes S5's highlighted-row rule redundant — is answered no, by measurement (2.53 / 2.82 on the band). The player bar was not exercised; see T7. |
| 2026-09-26 | the 22 R cases | 17 | 0 in the software; 2 cases mis-specified (J8, J9) | E1, G3 and G4 deferred to the M pass. No file in the real library changed. |
| 2026-09-26 | the M cases (A–E, H, J) | 28 | 3 real faults, 1 case impossible as written | The faults: a trim re-cut from the previous format's original and corrupted the file (B6/B7); a failed trim was recorded nowhere (I4); prune left the kept original behind (E5). E7 failed as written — a fetch did not unify the spelling. Scratch library only. |
| 2026-09-26 | the D cases (D3, E6, F1–F3, C6) | 6 | 0 | All in the scratch library, after the plan-file backup described above. E6 was observe-only on instruction and is now run to a conclusion; C6 confirmed the unmarked-sidecar overwrite it predicted, which P2 then changed. |
| 2026-09-26 | the R cases (the page's logic under test) | 3 | 0 | After the module split: the page loads and behaves, and the cache chain covers the imported file. 23 JS tests, 518 from `uv run pytest`. |
| 2026-09-26 | the Q cases (reordering by dragging) | 5 | 1 defect found in the package under test (Q2), fixed | Two-disc split; drags driven with real and synthetic pointer events. |
| 2026-09-26 | the P cases (the ⏱ mark leads to a cut) | 5 | 0 | D2's specimen taken from +1:07 to +0.3s by ear and by target, in one pass through the page. |
| 2026-09-26 | the O cases (opening an album asks the disk) | 4 | 0 | Sidecars changed with a shell, on a 56-track album; the open costs about 2 ms more than before. |
| 2026-09-26 | the N cases (a way back from an edit) | 5 | 0 | Both field resets and the order flag driven through the page; the following update put the source's order back. |
| 2026-09-26 | the M cases (the fetch preview) | 5 | 1 blemish (M4, the absolute path), fixed | Run over an empty scratch library so "writes nothing" was observable. The preview already existed; the work was making it the outcome. |
| 2026-09-26 | the L cases (repair from the web UI) | 3 | 0 | Scratch library with two spellings of one artist; the shouted folder was gone afterwards. Nothing measured on the real library: repair is a no-op there today (I-014, I-015). |
| 2026-09-26 | the K8–K11 cases (per-track lyrics actions) | 4 | 0 | Driven through the real page on a scratch library inside the session scratchpad. The rejected entry stayed rejected across an explicit new lookup. |
| 2026-09-26 | the backlog packages, `067f1ae..3a07b5c` | sections K–Q, 33 cases | 4 defects found in the packages under test, all fixed before their commits | P8 lyrics editor, P9 per-track lyrics actions, P10 repair in the UI, P11 the fetch preview, P12 a way back from an edit, P13 opening an album asks the disk, P14 the ⏱ mark leads to a cut, P15 reordering by dragging. Every case driven through the real page on a scratch library; the real library was read-only throughout. 516 tests at the end. |
| 2026-09-26 | the K cases (the lyrics editor) | 7 | 1 defect found in the package under test (K7), 1 blemish (K3) | Both fixed before the commit. Driven through the real page against a freshly seeded scratch library. |
| 2026-09-26 | re-runs after the fixes, `1e3da95..456d83e` | B6, B7, B8, I4, E5, E6, C5, C6, C7, E7, J5, J8, J9 + the split/merge round trip | all pass | Nine commits: trim integrity and its two mirrors, the lyrics ownership contract and its follow-up, order and prune, artist unification, repair's one-pass decision, the consensus length reference. 458 tests at the end, from 379. |

### Evidence methods that lied

Nine of my own checks produced a false result, or none at all, before the software did anything
wrong. Use these forms:

- **A layout check needs a picture *and* a measurement, and neither finds the other's faults.**
  P15 added the grip and P17 photographed the rows; both passed. The grip was sitting above the
  position field in the very image that was approved (docs/screenshots/album.jpg, P17), and nobody
  saw it — a row one line taller looks like a row. It took a user working in the UI to notice.
  Measuring found the opposite kind: the position field's value overflowing its box by three pixels,
  invisible at 7 tracks and a clipped digit at 56. Photograph it to see what is ugly, measure it to
  see what is wrong.
- **A DOM assertion proves an element exists, not that anyone can see it.** P12's reset affordance
  was checked by `outerHTML`, `textContent`, class names and the plan after the click — all passing,
  all blind to contrast. `button.badge.reset` inherited the button default's accent *background*
  while `.badge.user` gave it accent *text*, so "you ↺" shipped as an empty purple pill and stayed
  that way through two more packages. It was obvious in the first screenshot anyone looked at. A
  picture is the only test of contrast, truncation and overlap; assertions cannot see any of them.
- **A scripted doc edit that matches nothing succeeds.** `str.replace` returns the string
  unchanged when its target is absent, so a heredoc that rewrites a paragraph, writes the file and
  exits 0 can leave the document untouched — twice here, both times because an earlier edit in the
  same session had already changed the text being matched. Two commits therefore claimed
  documentation that was never written. Assert the target appears exactly once before writing, and
  grep for the new text afterwards.
- **The specimens carry the scars of earlier cases.** Case C4 renamed a track to *Sex is Muss
  (QA rename)*, which silently took it out of lrclib's reach — so a later lyrics case run against
  that specimen reported a FAIL that was nothing to do with the code under test. Check a specimen's
  current title and status before using it as evidence, or pick one no earlier case touched.
- **File-state comparison**: `find … -printf '%T@ %p\n' | sort`. Unsorted, directory order
  alone changes the hash and a dry run looks like a write.
- **Page state**: `queue`, `qi`, `state` are top-level `let` bindings — global, but **not**
  properties of `window`. `window.queue` is `undefined`, and the throw leaves your promise
  unresolved so the call hangs.
- **Process checks**: never `pgrep -f`/`pkill -f` with a pattern that appears in your own
  command line — it matches the shell running it. `pkill` that way killed the shell instead of
  the server. Check the **port** (`ss -ltnp | grep 8799`) or the log.
- **A green suite proves nothing about an environment it was not run in — twice over.** P27's commit
  was green here and red on CI, because two tests of the second model's retry logic reached
  `resolved_device()`, which imports `torch`; both of us had the `timing` extra installed for the
  user's manual test, so neither of us was checking the base install any more. The rule (R-067) was
  already written: `uv run pytest` must be green with **no** extra installed. The proof is one
  command, and it belongs before every push that touches an optional path:
  `uv venv /tmp/base && VIRTUAL_ENV=/tmp/base uv pip install -e . pytest && /tmp/base/bin/python -m pytest -q`.
  The same run exposed the other half: CI was reporting "404 passed, 190 skipped" and nobody read the
  second number. **187 of those skips were ffmpeg**, which the workflow deliberately did not install —
  reproduced exactly by running the suite with a PATH that has no ffmpeg (406 passed, 190 skipped
  locally). A third of the suite had been skipping itself on every run since the workflow was written.
  Read the skip count, not just the colour.
- **Silence you designed in is not a stall, and an estimate needs the start time first.** A
  measurement script of mine printed one row per track and nothing in between, because it passed no
  log callback; when the log stopped growing I read it as a stalled track, reached for `ps` elapsed
  figures that were giving nonsense, and told the reviewer a ninety-minute run would take five hours
  — which became a question put to the user about cutting a measurement short. The process's own start
  time settled it in one command: `ps -o lstart,etimes -p <pid>`, two tracks in eleven minutes, the
  estimate wrong by a factor of three. Take the start time before saying anything about duration, and
  log a wall-clock line per finished unit so the next claim has a number behind it rather than an
  impression.

### Findings from the M pass

- **A fetch can leave the library split; only `repair` unifies it.** Seeding produced
  `LORD OF THE LOST/` and `Lord Of The Lost/` side by side, because `_harmonize_artist` aligns
  the album being fetched to the library but never rewrites the albums already there. Observed
  again in the other direction when a MusicBrainz-spelled plan arrived after a repair had
  settled on the YouTube spelling. Each `repair` converged correctly (finally on
  *Lord of the Lost*), so the fault is that a fetch alone does not.
  *Fixed in P4 (DESIGN.md §9.23): the fetched album adopts the library's spelling either way, and
  when it is itself the better evidence one line says so and leaves the upgrade to `repair`.*
- **An album artist can disagree with its own track artists.** After the first repair,
  *Viva Vendetta* read `albumartist='Lord Of The Lost'` (`yt_title`, borrowed from the other
  album) while its only track read `'Lord of the Lost'` (`mb`). The evidence order weighs
  provenance on the album field, and the MusicBrainz evidence sat on the track. *Fixed in P4: an
  album is made consistent with its own tracks — most common track artist, key-equal, spelled
  differently, MB behind it — before the library is consulted, and `repair` does the same step, or
  the fetch-time hint could not keep its promise.* It resolved
  once an MB-spelled album joined the library, but the intermediate state was wrong.
- **A single's album name keeps what a track title drops.** Fetching the DOMINUM video gave
  the album `The Dead Don't Die (feat. @xxFEUERSCHWANZxx)` — the raw `@handle` and the feat.
  group — while the track title was cleaned to `The Dead Don't Die (feat. xxFEUERSCHWANZxx)`.
  Same class as the label suffix fixed in `5f49752`: album naming does not share all of
  `clean_title`'s hygiene.

### The trim/format defect, from B6, B7 and B8

Four faults, one root. `original_path()` names the kept original `<video id>.opus` whatever the
track's format is, and `apply()` always cuts into `.trim.opus` with `-c copy`.

1. **Silent container corruption.** A track that was trimmed as `.opus`, then switched to
   `combined`, is trimmed again from the **stale Opus original**: ffmpeg copies happily and the
   result is written over the `.m4a`. `file` reports *Ogg data, Opus audio* for a `.m4a`.
2. **An unhandled exception, and a stuck album.** `tag_file` dispatches on the suffix, so it
   calls `MP4()` on Ogg content and raises `MP4StreamInfoError`. It is not caught: the run
   aborts, and **every later run on that album raises the same thing** until someone removes
   the file by hand. Switching the format back recovers it (re-download plus cleanup).
3. **The plan and the disk disagree in silence.** After the crash the plan said `state=done`,
   `trimmed=None`, `error=None`, while the file was 388 s of Ogg named `.m4a`.
4. **A cleanly failed trim is not recorded.** With no stale original, ffmpeg refuses the m4a
   ("Unsupported codec id in stream 0"), `run()` sets `track.error` — and then nothing saves
   the plan, because no signature changed. The plan keeps `error=None` and advertises a trim
   that never happened. In the web UI the warning goes to the logger, not the job log, so the
   user sees **nothing at all**.

And it outlives the format switch: `.originals/dGO_sx4By28.opus` still held AAC after the track
was switched back to `.opus`, so every future trim of that track fails on a wrong-codec
original. B8 ran into this while otherwise passing.

### Two more from phase 3

- **A broken trim is retried for ever.** The DOMINUM track left with a wrong-codec original
  re-attempted its trim on *every* pass over that library — each `lyrics` or `download` run
  logged the same ffmpeg refusal. Nothing records the failure, so nothing ever gives up on it.
- **A status can outlive the words it describes.** After the sidecar was deleted (C7) the tag
  lost the lyrics, as designed, but `plan.lyrics` still read `synced`. *Fixed in P2.*

### From phase 4

- **Any failed trim is silent, not just the m4a one.** I4 removed ffmpeg from `PATH`: the trim
  failed, the audio was untouched, and nothing recorded it — no `error` in the plan, nothing in
  the job log, and the plan still advertising the trim. This is the same fault as the m4a case
  reached from a realistic direction, since ffmpeg is an optional dependency.
- **A failed trim is retried until it succeeds.** The trim I4 left pending was applied on a
  later unrelated run, once ffmpeg was back, so a track can change length long after the edit
  that asked for it.

### From phase 5

- **Prune keeps the original, delete removes it.** `delete_track` unlinks `.originals/<id>.opus`;
  `prune` does not. A pruned track that had been trimmed leaves a full-size orphan nothing will
  ever read again.
- **Merging discs reshuffles a user's order.** Setting every track back to disc 1 re-sorts by
  (disc, number), so tracks that were 2-01…2-03 land among the disc-1 numbers. A split is
  reversible on paper but not in arrangement.

### Smaller observations, not cases

- `HEAD /` answers `501 Unsupported method` — GET and POST are implemented, HEAD is not. Only
  matters to health checks and proxies.
- A dry run prints the album artist *before* library harmonisation, so the preview can differ
  from what a real fetch writes (J5: `LORD OF THE LOST` previewed, `Lord of the Lost` written).
  *Fixed in P4: harmonisation runs in the dry-run path too.*
- A title carrying a bracket marker can zero out the lrclib search, so an instrumental track
  may end up with no length reference at all (J8).
