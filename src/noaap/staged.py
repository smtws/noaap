"""Taking in a collection that lives on another filesystem, a batch at a time (§9, slice 103).

The user's collection is on a NAS, and the measurement that settled how to do it is in
`docs/spikes/2026-10-nas-helper.md`: working over the share costs ~8.3 h for 11,000 tracks, and
copying to this machine, taking it in here and copying back costs ~5.2 h with every precaution at
full strength — because the decoded digests and the tag writes happen where they are fast.

But 225 GB staged in one go would fill the disk and block whatever else the person is doing, so it
goes in **batches**. The user: *"think about a configurable batch size from the start (maybe default
to 1/10th of any given running-box's free disk space) (careful, dont count mounted nas shares into
it like many filemanagers do)"*.

Per batch, in this order, and nothing in the next batch starts until this one is finished:

1. **copy** the batch's album folders from the share to a staging folder on this machine, whole —
   the files that are not audio too, because they are the owner's and the snapshot records them;
2. **write down** what was copied (`precautions.take`), which is the way back for this batch;
3. **take it in** with the ordinary pass, unchanged, against the staging folder as its library;
4. **copy back**, album by album: every file written beside its target and renamed into place,
   verified by digest, and only then are the files the scheme superseded removed from the share;
5. **remove** the staging copy, and record the batch as finished.

`--keep-originals` is not needed and is not offered: the share holds the untouched original of every
file until step 4 has verified its replacement, which is a stronger way back than a copy of it.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import intake, precautions, sources
from .download import PLAN_FILE
from .precautions import bytes_sha

STAGED = "noaap-staged.json"     # which batches are finished, beside the staging folder
PART = ".noaap-incoming"         # the suffix a file being copied back wears until it is verified
SHARE = 10                       # the default batch is a tenth of the free space


@dataclass
class Batch:
    """One group of albums: what to copy, and how big it is."""

    albums: list[Path]
    bytes: int

    @property
    def alone(self) -> bool:
        return len(self.albums) == 1


@dataclass
class Staged:
    """What a staged run came to."""

    filesystem: str = ""
    free: int = 0
    limit: int = 0
    batches: int = 0
    albums: int = 0
    tracks: int = 0
    copied_out: int = 0        # bytes read from the share
    copied_back: int = 0       # bytes written to the share
    superseded: list[str] = field(default_factory=list)   # what the scheme replaced, by its old name
    unverified: list[str] = field(default_factory=list)   # a copy back whose digest did not match
    peak_staged: int = 0       # the most this machine held at once
    done: list[str] = field(default_factory=list)
    would: list[str] = field(default_factory=list)
    snapshots: list[str] = field(default_factory=list)   # the way back, one per batch, kept


def free_space(path: Path) -> tuple[int, str]:
    """Bytes free on the filesystem `path` is on, and the device that filesystem is.

    **`statvfs` of that one path and nothing else.** A mounted share is a different filesystem with
    its own free space, and counting it — which is what a file manager showing one number for "disk"
    does — would size a batch against 5.4 TB of NAS and fill this machine's disk. The path is walked
    up to the nearest directory that exists, so a staging folder that has not been made yet still
    answers for the filesystem it would be made on.
    """
    where = path
    while not where.exists() and where != where.parent:
        where = where.parent
    st = os.statvfs(where)
    return st.f_bavail * st.f_frsize, _device(where)


def _device(path: Path) -> str:
    """Which filesystem a path is on, by name where /proc says so and by device number otherwise."""
    try:
        marker = os.stat(path).st_dev
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            parts = line.split()
            if len(parts) > 4 and os.path.ismount(parts[4]):
                try:
                    if os.stat(parts[4]).st_dev == marker:
                        source = parts[parts.index("-") + 2] if "-" in parts else "?"
                        return f"{source} on {parts[4]}"
                except OSError:
                    continue
    except OSError:
        pass
    return str(path)


def says_room(staging: Path, limit: int, free: int, where: str) -> str:
    return (f"staging on {where}: {free / 1e9:.1f} GB free, "
            f"batches of at most {limit / 1e9:.1f} GB")


def album_sizes(root: Path, source: Any) -> list[tuple[Path, int]]:
    """Every album folder under the root with the bytes it holds, in the order a pass would take them."""
    out = []
    for album in intake.albums_under(root, source):
        out.append((album, sum(p.stat().st_size for p in album.rglob("*") if p.is_file())))
    return out


def batches(albums: Iterable[tuple[Path, int]], limit: int) -> list[Batch]:
    """Albums grouped so that no batch is larger than `limit` — **except one album that is**.

    In the listing's own order, so that a run which stops and starts again makes the same batches and
    the resume file still means what it says. An album bigger than a whole batch is its own batch and
    is named as such rather than refused: a 12 GB live set is still somebody's album.
    """
    made: list[Batch] = []
    now: list[Path] = []
    held = 0
    for album, size in albums:
        if now and held + size > limit:
            made.append(Batch(now, held))
            now, held = [], 0
        now.append(album)
        held += size
        if held >= limit:
            made.append(Batch(now, held))
            now, held = [], 0
    if now:
        made.append(Batch(now, held))
    return made


def read_done(path: Path, root: Path | None = None) -> set[str]:
    """Which batches are finished, by the first album in each. Another root's record is not ours."""
    if not path.is_file():
        return set()
    try:
        got = json.loads(path.read_text())
    except (ValueError, OSError):
        return set()
    if root is not None and got.get("root") not in (None, str(root), str(root.resolve())):
        return set()
    return set(got.get("done") or [])


def write_done(path: Path, root: Path, done: set[str]) -> None:
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps({"root": str(root), "at": datetime.now(UTC).isoformat(timespec="seconds"),
                               "done": sorted(done)}, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def take_in_staged(service: Any, root: Path, choices: intake.Choices | None = None, *,
                   staging: Path, batch_size: int | None = None, dry_run: bool = True,
                   resume: bool = True, log: Callable[[str], None] = lambda s: None) -> Staged:
    """Take in a collection that is somewhere else, a batch at a time.

    `root` is the collection — a mounted share. `staging` is a folder on **this** machine's own disk,
    and the batch size is measured against that filesystem alone.
    """

    choices = choices or intake.Choices()
    source = sources.get("folder", service.cfg)
    source.digests = False
    done = Staged()
    done.free, done.filesystem = free_space(staging)
    done.limit = batch_size or max(done.free // SHARE, 1)
    log(says_room(staging, done.limit, done.free, done.filesystem))

    sized = album_sizes(root, source)
    # **an album that already holds a plan has been taken in**, and the way to bring it to changed
    # settings is a repair, which needs no staging. Skipping it here is what makes a staged run
    # idempotent across the renames it does itself: a batch's name cannot survive its own albums
    # being moved into the scheme, so the record alone would copy everything out a second time.
    if taken := [album for album, _ in sized if (album / PLAN_FILE).is_file()]:
        log(f"  {len(taken)} album(s) are already taken in and are skipped")
        sized = [(album, size) for album, size in sized if album not in set(taken)]
    made = batches(sized, done.limit)
    done.batches, done.albums = len(made), len(sized)
    log(f"{len(sized)} album folder(s), {sum(s for _, s in sized) / 1e9:.1f} GB, "
        f"in {len(made)} batch(es)")
    for n, batch in enumerate(made, 1):
        over = batch.alone and batch.bytes > done.limit
        line = (f"  batch {n}: {len(batch.albums)} album(s), {batch.bytes / 1e9:.2f} GB"
                + (" — one album, larger than a batch, so it is a batch of its own" if over else ""))
        log(line)
        done.would.append(line)

    where = staging / STAGED
    finished = read_done(where, root) if resume and not dry_run else set()
    if finished:
        log(f"  {len(finished)} batch(es) were done by an earlier run and are skipped")

    if dry_run:
        for n, batch in enumerate(made, 1):
            log(f"batch {n} —")
            kept = _as_if(service, root, batch, choices, log)
            done.tracks += kept
        log("nothing was written, here or on the share. `--apply` does it.")
        return done

    staging.mkdir(parents=True, exist_ok=True)
    for n, batch in enumerate(made, 1):
        name = str(batch.albums[0].relative_to(root))
        if name in finished:
            continue
        service.check()
        log(f"batch {n} of {len(made)}: {len(batch.albums)} album(s), {batch.bytes / 1e9:.2f} GB")
        _one_batch(service, root, batch, choices, staging, done, log)
        finished.add(name)
        write_done(where, root, finished)
        done.done.append(name)
    log(f"{len(done.done)} batch(es) taken in; {done.tracks} track(s); "
        f"{done.copied_out / 1e9:.1f} GB from the share, {done.copied_back / 1e9:.1f} GB back")
    if done.superseded:
        log(f"{len(done.superseded)} file(s) the scheme replaced were removed from the share")
    if done.unverified:
        log(f"⚠ {len(done.unverified)} file(s) could not be verified after the copy back — "
            "the share's own file was left in place")
    log(f"this machine held at most {done.peak_staged / 1e9:.2f} GB at once")
    if done.snapshots:
        log(f"the way back is {len(done.snapshots)} snapshot(s) in {staging}, one per batch: "
            f"`noaap take-in {root} --restore <one of them> --apply`")
    return done


def _as_if(service: Any, root: Path, batch: Batch, choices: intake.Choices,
           log: Callable[[str], None]) -> int:
    """The dry run of one batch: the pass's own lines, read off the share, writing nothing anywhere."""
    kept = 0
    for album in batch.albums:
        plan = intake._adopted(album, root, sources.get("folder", service.cfg), log=log)
        if plan is None:
            continue
        kept += len(plan.tracks)
        for line in intake._would(service, plan, album, root, choices,
                                  choices.as_treatment()):
            log(f"  {line}")
    return kept


def _one_batch(service: Any, root: Path, batch: Batch, choices: intake.Choices, staging: Path,
               done: Staged, log: Callable[[str], None]) -> None:
    """Copy out, write down, take in, copy back, remove. Nothing else runs while this does."""
    from .service import Service

    here = staging / "batch"
    if here.exists():
        shutil.rmtree(here)       # our own staging folder from a run that stopped; the share is intact
    for album in batch.albums:
        inside = here / album.relative_to(root)
        inside.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(album, inside)
        done.copied_out += sum(p.stat().st_size for p in inside.rglob("*") if p.is_file())
    held = sum(p.stat().st_size for p in here.rglob("*") if p.is_file())
    done.peak_staged = max(done.peak_staged, held)
    log(f"  copied {held / 1e9:.2f} GB to {here}")

    snapshot = staging / f"batch-{len(done.done) + 1}-{root.name}-snapshot.jsonl"
    if snapshot.exists():
        snapshot.unlink()       # ours, from a batch that did not finish; the share is intact
    precautions.take(here, snapshot, log=lambda s: None)
    was = {r.path: r for r in precautions.read(snapshot).files}
    log(f"  wrote down {len(was)} file(s)")

    # the pass, unchanged, with the staging copy as its library so the scheme may move folders
    cfg = replace(service.cfg, library_root=here)
    staged_service = Service(cfg, here, log=lambda s: None, on_track=service.on_track,
                             mb=service.mb, lrclib=service.lrclib)
    got = intake.take_in(staged_service, here, choices, dry_run=False, snapshot=snapshot,
                         keep=None, resume=False, log=lambda s: None)
    done.tracks += got.tracks
    log(f"  taken in: {got.adopted} album(s), {got.tracks} track(s), "
        f"{got.renamed} renamed, {got.retagged} rewritten")

    _copy_back(root, here, was, done, log)
    shutil.rmtree(here)
    # **the batch's snapshot stays, and so does the record of what the pass made.** The share's own
    # untouched file is the way back only until its replacement is verified and the old one removed;
    # after that these two are the only way back there is, and they cost half a megabyte a batch. A
    # first version of this deleted them with the staging copy, which left a finished run with no way
    # back at all — the one thing the whole package is for.
    intake.state_path(snapshot).unlink(missing_ok=True)      # the resume file, which is spent
    done.snapshots.append(str(snapshot))
    log(f"  the way back for this batch: {snapshot.name} (and {intake.made_path(snapshot).name})")


def _copy_back(root: Path, here: Path, was: dict[str, precautions.Recorded], done: Staged,
               log: Callable[[str], None]) -> None:
    """Put the batch back on the share: **add and replace only, and remove nothing until all of it is
    verified.**

    Every file is written to a name beside its target and renamed into place, which is atomic inside
    the share's own filesystem, and its digest is then compared with the staged file's. Only when
    **every** file of the batch is on the share and verified are the paths the scheme superseded
    removed — the ones the snapshot recorded and the staged copy no longer has, which is the whole
    rule, because an album whose folder was renamed shares no path with its old self. If one file
    cannot be verified, nothing at all is removed and the share keeps every original, which is the
    way back.
    """
    verified: set[str] = set()
    for album in sorted({p.parent for p in here.rglob("*") if p.is_file()}):
        inside = album.relative_to(here)
        for path in sorted(p for p in album.iterdir() if p.is_file()):
            name = str(inside / path.name)
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + PART)
            shutil.copy2(path, tmp)
            os.replace(tmp, target)          # atomic within the share
            done.copied_back += path.stat().st_size
            if bytes_sha(target) == bytes_sha(path):
                verified.add(name)
            else:
                done.unverified.append(name)
        log(f"  {inside}: back on the share")
    if done.unverified:
        log(f"  ⚠ {len(done.unverified)} file(s) did not match after the copy — nothing of the "
            "share's own was removed, and every original is still there")
        return
    # **a superseded path that is the same file as one just written is not superseded.** The share is
    # case-insensitive and this machine is not, so an album whose folder differs from the scheme only
    # in its case — `iii` against `III` — comes back to the very same directory, and where the file
    # names were already the scheme's, the old path *is* the new path. Measured on the real share:
    # 14 files written, verified, and then deleted again by this loop, which is as close to losing
    # somebody's music as this program has come. `samefile` answers it without knowing anything
    # about how a filesystem folds names.
    written = [root / name for name in sorted(verified)]
    emptied: set[Path] = set()
    for name in sorted(set(was) - verified):
        old = root / name
        if not old.is_file():
            continue
        if any(_same(old, kept) for kept in written):
            continue
        old.unlink()
        emptied.add(old.parent)
        done.superseded.append(name)
    if done.superseded:
        log(f"  {len(done.superseded)} file(s) the scheme replaced removed: "
            f"{', '.join(done.superseded[:3])}" + (" …" if len(done.superseded) > 3 else ""))
    # **only the folders this pass emptied**, deepest first. Sweeping the whole root for empty
    # directories removed one the owner had and we never touched — `Der W/Autonomie`, empty in their
    # own collection — which is deleting something of theirs under cover of tidying up.
    for folder in sorted(emptied | {f.parent for f in emptied}, key=lambda p: -len(p.parts)):
        if folder == root or root not in folder.parents:
            continue
        try:
            if not any(folder.iterdir()):
                folder.rmdir()
        except OSError:
            pass


def _same(one: Path, two: Path) -> bool:
    """Whether two paths are the same file, whatever the filesystem thinks a name is."""
    try:
        return one.samefile(two)
    except OSError:
        return False


__all__ = [
    "PART",
    "STAGED",
    "Batch",
    "Staged",
    "album_sizes",
    "batches",
    "free_space",
    "read_done",
    "says_room",
    "take_in_staged",
    "write_done",
]
