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

*(filled in below from the run of 2026-10-01; see `docs/p81-notes.md` for the running account)*

## What it found

Six faults, in the precautions themselves, none of which the suite could see — every fixture was
Opus and the collection is mp3. They are written up where they belong: DESIGN §9 slices 99 and 101,
with the cases that now hold them in `tests/test_precautions.py`, `tests/test_intake.py` and
`tests/test_state_of_the_library.py`.
