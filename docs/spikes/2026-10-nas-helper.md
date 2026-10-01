# A helper on the NAS — what the box can do, and whether it is worth it (P82, 2026-10-01)

R-349, for the user's twenty-year collection on a Zyxel NAS540. Two questions: what can that box
actually do, and what should run there. Everything below was measured on the box itself, inside the
`music` share and nowhere else.

## The box

Zyxel NAS540: **armv7l, 2 cores** (Comcerto 2000, Cortex-A9 class), **1 GB RAM** (75 MB free,
204 MB in buffers), kernel **3.2.54**, **BusyBox 1.19.4** (2011), `/bin/sh` is ash. Share
`/i-data/5c72062c/music` on `/dev/md2`, 10.8 T with 5.4 T free, `drwxrwxrwx`, empty.

**It has more applets than the brief assumed.** All of these run as bare commands:
`md5sum` (`/sbin/md5sum`), `sha256sum`, `sha512sum`, `cksum`, `dd`, `cp`, `mv`, `ln`, `find`, `tar`,
`awk`, `sed`, `sort`, `flock`, `timeout`, `readlink`, `which`, `du`, `df`, `wget`, `xargs`, `sync`.
**Absent**: `stat`, `sha1sum`, `python`, `command -v` — and a first attempt at this list reported
everything absent because it used `command -v`, which is itself one of the missing ones.

**A static armhf build runs.** `ffmpeg 7.0.2-armhf-static` (johnvansickle.com, 31.8 MB, ELF 32-bit
ARM EABI5, static, built for Linux 3.2.0) copied into the share: `ffmpeg -version` exits 0 with the
full feature list. It is still in the share, as the one binary R-349 allows there.

## What it costs the box to do things (measured)

Rates per **file byte**, which is the number to compare against the wire. Digests over 5 runs of one
mp3 (5.04 MB, 4:23 of audio) and one flac (39.49 MB); the sequential figures on a 512 MB file.

| | rate | against a 30 MB/s wire |
|---|---|---|
| disk read, sequential | **128 MB/s** | 4× faster |
| disk write, sequential | **72.8 MB/s** | 2.4× faster |
| `md5sum` | **25–49 MB/s** | about even |
| `cksum` (CRC32) | 64 MB/s | 2× — but 32 bits is not an identity |
| `sha256sum` | 22 MB/s | slower |
| ffmpeg **packet** md5 | **12.6 MB/s** (mp3), 16.5 (flac) | half the wire |
| ffmpeg **decoded** md5 | **0.97 MB/s** (mp3), 8.2 (flac) | **3–30× slower** |

**The decoded digest is out.** It is what a snapshot records and what a careful write proves
(§9, slice 102), and at 1 MB/s for mp3 — which is most of the collection — 225 GB would take
**about 62 hours** on the NAS against 2.1 h of reading it over the wire. It belongs on the laptop,
always.

**And ffmpeg must not retag there.** `ffmpeg -c copy -metadata …` on the probe mp3 produced a file
whose **decoded digest differs from the original's** (`a95ebfb2…` against `6ea51ed5…`, and 392 bytes
longer): the mp3 muxer rewrites the Xing/LAME header, which changes the decoded output at the edges.
By noaap's own definition that is not the same recording, so the answer to R-349's conditional —
*tag writes only if ffmpeg can do them without re-encoding* — is **no**. Nothing writes into an audio
file but mutagen on the laptop.

## So what would the helper do

Only the two things the box is genuinely good at, both of which touch **no network at all**:

- **copy a file within the share** (128 MB/s read, 72.8 MB/s write) — the kept original, and the
  careful write's copy-beside.
- **rename within the share** (atomic, same filesystem) — the careful write's last step.

and two cheap conveniences: `find` for the file list, and `md5sum` of the **non-audio** files, whose
bytes *are* their identity (153 files, 14 MB in the reference collection — so this is tidiness, not a
saving).

It would **not** digest audio, would **not** write tags, and would **not** be asked anything in
parallel: one file at a time, as R-349 requires.

### Shape

A single `sh` script for BusyBox ash, no dependencies beyond the applets above, copied into the share
by noaap. Before each run noaap asks the NAS for `sha256sum` of it and compares with the digest it
holds; a mismatch refuses the run. One SSH invocation per operation, batched per album (one call
carrying an album's copies, rather than one call per file) to keep the round trips down — about 15
calls per album instead of 15 × 3.

### What crosses the network, per audio file

| step | now | with the helper |
|---|---|---|
| keep the original | read + write over the wire | **nothing** (NAS-side copy) |
| copy beside, for the careful write | read + write | **nothing** (NAS-side copy) |
| write the tags into the copy | read + write | read + write |
| prove the recording survived | read | read |
| atomic replace | rename | **nothing** (NAS-side) |
| | **3 reads + 2 writes** | **2 reads + 1 write** |

### When the NAS goes away mid-pass

Nothing new is needed, and that is the point: every helper call is one SSH command, a failure raises,
and the pass already stops on an exception with its resume file naming the albums that finished. The
careful write's invariant is unchanged because the atomic replace is still the last step — if the box
goes away before it, the file that was there is still there and a temporary copy beside it is the
whole of the mess. The one new rule: the helper's own temporary file is named so that the next run
recognises and removes it, as `safely` already does locally.

## Whether to build it

**On the numbers, no — and the alternative is three times better.**

For 11,000 tracks (225 GB) at 30 MB/s, taking the collection in *over the share*:

| | wire | plus NAS-local | total |
|---|---|---|---|
| as it stands (3 reads + 1–2 writes) | 900 GB → 8.3 h | — | **8.3 h** |
| with the helper (2 reads + 1 write) | 675 GB → 6.2 h | 450 GB at 72 MB/s, serial per file | **~7.1 h** |

So the helper is worth **about 1.2 hours out of 8.3**, for a new moving part on a 2011 BusyBox box
that has to be verified by digest before every run and that cannot be asked to do the two things the
pass most wants done (identity and tags).

Against that, the thing that needs no new code at all: **copy the collection to the laptop, take it
in there, copy it back.** Two transfers at 30 MB/s is 4.2 h of wire, and everything in between runs at
local speed — the whole pass measured 650 s on 41 GB, so about 1 h for 225 GB. **~5.2 h, against 8.3
over the share**, and every precaution keeps its full strength because the decoded digests and the tag
writes happen where they are fast. The laptop has 386 GB free and the collection is ~225 GB, so it
fits, once.

**Recommendation**: do not build the helper. Spend the same effort on making the round trip safe —
the copy out, the take-in, and a copy back that can only add and replace, never delete — because that
is where the hours are, and it keeps every guarantee the precautions already make. If the helper is
wanted anyway, the design above is the whole of it, and the only part I would still argue for is the
NAS-side copy for `--keep-originals`, which turns 2.1 h of wire writes into 0.

## What is in the share now

`ffmpeg` (31.8 MB, the one binary R-349 permits). The two probe files are removed, and the 512 MB
`.probe` that the write-rate figure came from is removed. The laptop mount `/mnt/speicherzwerg-music`
is SMB 3.0, `soft`, uid 1000, **not in fstab — it is gone after a reboot**.
