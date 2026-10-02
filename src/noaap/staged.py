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
5. **remove** the staging copy, and record the batch as finished — a marker of its own, written
   only now, because nothing on the share can say a batch came back (§9, slice 109).

`--keep-originals` is not needed and is not offered: the share holds the untouched original of every
file until step 4 has verified its replacement, which is a stronger way back than a copy of it.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import shutil
import socket
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import intake, precautions, sources
from .download import PLAN_FILE
from .precautions import bytes_sha

STAGED = "noaap-staged.json"     # the index of this pass: one record per batch, beside the staging
NAME_LIMIT = 48                  # of the first album's folder name, kept in a snapshot's name
PART = ".noaap-incoming"         # the suffix a file being copied back wears until it is verified
ASIDE = "noaap-originals"       # beside the collection: every file the copy back replaces
LOCK = "take-in.lock"            # in the store: one staged pass per collection at a time
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
class Recorded:
    """One batch of a pass, as the index knows it: its snapshot, its albums, and whether it is done.

    **`done` is written only after the copy back is verified** (R-374, ruling b), and it is the only
    thing that says a batch is finished. What is on the share cannot say it: a plan file beside an
    album means a pass got that far, not that the batch it belonged to ever came back — and reading
    it as "already taken in" is what let a resume walk past the batch a SIGTERM had interrupted and
    overwrite the only record of it.
    """

    key: str
    snapshot: str                                        # the file name, in the staging folder
    albums: list[str] = field(default_factory=list)      # relative to the root, as planned
    became: list[str] = field(default_factory=list)      # and the folders the pass made for them
    done: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "snapshot": self.snapshot, "albums": self.albums,
                "became": self.became, "done": self.done}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Recorded:
        return cls(key=str(d.get("key") or ""), snapshot=str(d.get("snapshot") or ""),
                   albums=[str(x) for x in (d.get("albums") or [])],
                   became=[str(x) for x in (d.get("became") or [])],
                   done=bool(d.get("done")))


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
    changed_meanwhile: list[str] = field(default_factory=list)   # somebody else had it since
    aside: list[str] = field(default_factory=list)       # and what was moved into the store for it
    moved_aside: int = 0
    unverified: list[str] = field(default_factory=list)   # a copy back whose digest did not match
    peak_staged: int = 0       # the most this machine held at once
    done: list[str] = field(default_factory=list)
    would: list[str] = field(default_factory=list)
    # **why the run stopped, in one line a person can act on** (R-378). Set and the run is over; the
    # share is untouched by the batch that could not be staged, and the same command resumes.
    stopped: str = ""
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


def batch_key(root: Path, batch: Batch) -> str:
    """What this batch *is*: the folders it holds, under the root, in one short name.

    **A batch's identity is its content, not its position** (R-374, ruling a). The snapshot used to
    be `batch-<n>-…`, and a resume numbers its batches from 1 again — so the resume's first batch
    overwrote the record of the batch a SIGTERM had interrupted, and the restore then had no entry
    for the twelve files that pass had already rewritten. It reported clean.
    """
    names = sorted(str(album.relative_to(root)) for album in batch.albums)
    return hashlib.sha256("\n".join(names).encode()).hexdigest()[:12]


def snapshot_for(staging: Path, root: Path, batch: Batch) -> Path:
    """Where this batch's way back is written: its key, and its first album for a person to read."""
    first = batch.albums[0].name[:NAME_LIMIT] if batch.albums else root.name[:NAME_LIMIT]
    safe = "".join(ch if ch.isalnum() or ch in " -_." else "_" for ch in first).strip(" .") or "batch"
    return staging / f"batch-{batch_key(root, batch)}-{safe}-snapshot.jsonl"


def read_index(path: Path, root: Path | None = None) -> dict[str, Recorded]:
    """Every batch of the pass, by key, in the order they were recorded. Another root's is not ours."""
    if not path.is_file():
        return {}
    try:
        got = json.loads(path.read_text())
    except (ValueError, OSError):
        return {}
    if root is not None and got.get("root") not in (None, str(root), str(root.resolve())):
        return {}
    out: dict[str, Recorded] = {}
    for row in got.get("batches") or []:
        if isinstance(row, dict) and row.get("key"):
            one = Recorded.from_dict(row)
            out[one.key] = one
    return out


def write_index(path: Path, root: Path, batches: dict[str, Recorded]) -> None:
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps({"root": str(root), "at": datetime.now(UTC).isoformat(timespec="seconds"),
                               "batches": [one.to_dict() for one in batches.values()]},
                              ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def snapshots_of(staging: Path, root: Path | None = None) -> list[Path]:
    """Every snapshot of the pass, **oldest record last** (R-374, ruling c).

    A restore walks them in this order so that the oldest record of a file has the last word: a
    snapshot a resume wrote may have recorded files an interrupted run had already replaced, and
    those are not originals. Where there is no index — a single `--restore` of one file — the caller
    passes that file and this is not asked.
    """
    where = staging if staging.is_dir() else staging.parent
    index = where / STAGED if staging.is_dir() else staging
    known = read_index(index, root)
    out = [where / one.snapshot for one in known.values() if (where / one.snapshot).is_file()]
    return list(reversed(out))


def orphans_in_store(root: Path, named: Iterable[str]) -> list[str]:
    """Files in the store of originals that no snapshot names — the last net, checked.

    The store is what makes a restore byte for byte, and `restore` only ever looks in it for a file
    some snapshot recorded. So a file that is in there and in no snapshot is a file whose way back
    exists and cannot be found: exactly what the interrupted batch left behind (I-237). It is
    reported, and a restore that finds one does not report clean.
    """
    store = aside_for(root)
    if not store.is_dir():
        return []
    folding = precautions.folds_case(store)
    known = {name.casefold() if folding else name for name in named}
    out = []
    for path in sorted(p for p in store.rglob("*") if p.is_file()):
        name = str(path.relative_to(store))
        if (name.casefold() if folding else name) not in known:
            out.append(name)
    return out


SLOW = 20.0                      # seconds with no progress before the run says it is waiting


class Waiting:
    """Say when the collection has gone quiet, measured by **progress** and not by one call.

    A `soft` CIFS mount retries for about three and a half minutes before it gives up — measured
    twice on the user's NAS — and the pass printed nothing in that time, so a dead share looked like
    a run that had wedged.

    **The first version wrapped three calls and that was not enough** (R-402). The reviewer blocked
    the packets for 59 s while a copy back was in flight and the run said nothing: of the fourteen
    things a copy back asks of the share — `mkdir`, `is_file`, the snapshot's `stat`, the move aside's
    rename, the write, the `os.replace` into place, the read back for the digest, the superseded
    `unlink`, the empty-folder `rmdir` — only the write itself was wrapped. A block that lands in any
    of the others is silent, and the window where a `.noaap-incoming` file is on the share runs from
    the write's start to the rename, so `os.replace` and the digest read are exactly where their
    block most likely fell.

    So this watches the work rather than a call: the loop says `beat()` when a file is done, and if
    nothing has beaten for `SLOW` seconds the line goes out **once**, naming the last thing it was
    working on. The next beat re-arms it, so a share that goes quiet twice says so twice and one that
    is merely slow says it once. Nothing is interrupted: what is slow is the kernel's business.
    """

    def __init__(self, log: Callable[[str], None], after: float | None = None) -> None:
        self.log = log
        self.after = SLOW if after is None else after
        self.what: Path | str = ""
        self.last = time.monotonic()
        self.said = False
        self.done = threading.Event()
        self.thread: threading.Thread | None = None

    def beat(self, what: Path | str | None = None) -> None:
        """A file finished: the share is answering."""
        if what is not None:
            self.what = what
        self.last = time.monotonic()
        self.said = False

    def _watch(self) -> None:
        tick = min(self.after / 4, 1.0)
        while not self.done.wait(tick):
            if self.said or time.monotonic() - self.last < self.after:
                continue
            self.said = True
            self.log(f"  … still waiting for {self.what or 'the collection'} "
                     f"({self.after:.0f}s with no answer). It may be gone; the run carries on by "
                     "itself when the share answers or gives up.")

    def __enter__(self) -> Waiting:
        self.last = time.monotonic()
        self.thread = threading.Thread(target=self._watch, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.done.set()
        if self.thread is not None:
            self.thread.join(timeout=1)


class Held(Exception):
    """Another pass is working on this collection — said in one line, never as a traceback.

    **Nothing used to stop two passes on one root** (I-265, R-390), and two staging folders never
    see each other's index, so the only place a lock means anything is the collection itself. Both
    would copy the same albums out, write back over each other, and each move the other's result
    aside into the store; the one thing that saved the owner's file was the store refusing to
    overwrite an entry it already had.
    """


def lock_path(root: Path) -> Path:
    """Where the one lock for this collection lives: in its own store of originals."""
    return aside_for(root) / LOCK


def who_holds(root: Path) -> dict[str, Any]:
    """Whose pass holds this collection, as the lock says. Empty when nobody does."""
    where = lock_path(root)
    if not where.is_file():
        return {}
    try:
        got = json.loads(where.read_text())
    except (ValueError, OSError):
        return {}
    return got if isinstance(got, dict) else {}


def _alive(pid: int) -> bool:
    """Whether a process of this host is still there. Asked of the kernel, not guessed."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True             # somebody else's process, or one we may not signal: it exists
    return True


def take_the_lock(root: Path, log: Callable[[str], None] = lambda s: None) -> Path:
    """Hold this collection for one staged pass, or refuse in a line a person can act on.

    Stale is decided by asking the kernel, and only about this host: a pid on another machine means
    nothing here, so a lock from elsewhere is refused and the line says which file to remove by hand
    if that pass is known to be dead. A signal leaves the lock behind, which is what the stale rule
    is for.
    """
    where = lock_path(root)
    held = who_holds(root)
    if held:
        mine = held.get("host") == socket.gethostname()
        if mine and not _alive(int(held.get("pid") or 0)):
            log(f"the pass that held this collection (pid {held.get('pid')}, started "
                f"{held.get('at')}) is gone; taking it over")
        elif mine:
            raise Held(f"another take-in is working on {root} right now (pid {held.get('pid')}, "
                       f"started {held.get('at')}). One pass per collection: let it finish, or stop "
                       "it and run this again.")
        else:
            raise Held(f"another take-in is working on {root} right now, from "
                       f"{held.get('host')} (pid {held.get('pid')}, started {held.get('at')}). One "
                       f"pass per collection. If that pass is dead, remove {where} by hand and run "
                       "this again.")
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(json.dumps({"host": socket.gethostname(), "pid": os.getpid(),
                                 "at": datetime.now(UTC).isoformat(timespec="seconds"),
                                 "root": str(root)}, ensure_ascii=False, indent=1))
    return where


def let_the_lock_go(root: Path) -> None:
    """Only ours, and only while it is still ours."""
    held = who_holds(root)
    if held.get("host") == socket.gethostname() and held.get("pid") == os.getpid():
        with contextlib.suppress(OSError):
            lock_path(root).unlink()


def refuse_while_held(root: Path) -> None:
    """A restore does not take the lock and does not run under a live one (R-390)."""
    held = who_holds(root)
    if not held:
        return
    if held.get("host") == socket.gethostname() and not _alive(int(held.get("pid") or 0)):
        return
    raise Held(f"a take-in is working on {root} right now (from {held.get('host')}, pid "
               f"{held.get('pid')}, started {held.get('at')}). A restore while it writes would "
               "undo what it is doing: let it finish or stop it, then restore.")


class Stopped(Exception):
    """The run cannot go on — said in one line, never as a traceback.

    The gate's disk-full scenario (R-378): the pass did stop clean and left the share exactly as it
    was, but what a person read was twelve `[Errno 28]` tuples inside a `shutil.Error` and a stack
    trace. A copy out that cannot finish is an ordinary answer to an ordinary question — is there
    room — and the answer belongs in a sentence.

    **And the share can go away too** (I-253, the gate's unmount scenario). The first thing a copy
    back does is ask the share what it thinks a name is, which writes a probe folder; on an
    unmounted mount point that is `Errno 13`, and it reached the user as a stack trace through two
    modules. A collection that is not there is the same kind of answer as a disk that is full.
    """


# the name this was born under, when it only meant the staging disk
NoRoom = Stopped


def in_bytes(many: int) -> str:
    """Bytes a person can read: GB for a batch of music, MB when the number would round to 0.00."""
    return f"{many / 1e9:.2f} GB" if many >= 1e8 else f"{many / 1e6:.1f} MB"


def says_no_room(batch: Batch, free: int, staging: Path, where: str) -> str:
    return (f"{where} has {in_bytes(free)} free and this batch needs {in_bytes(batch.bytes)} "
            f"in {staging}. Nothing was copied and nothing on the share was touched. Free some "
            "space and run the same command again — it carries on where it stopped.")


def _only_in_the_store(staging: Path, root: Path,
                       known: dict[str, Recorded]) -> dict[str, list[str]]:
    """Which recorded files of an unfinished batch the share has lost, by album (R-388)."""
    store = aside_for(root)
    if not store.is_dir():
        return {}
    out: dict[str, list[str]] = {}
    for one in known.values():
        if one.done:
            continue
        snapshot = staging / one.snapshot
        if not snapshot.is_file():
            continue
        with contextlib.suppress(OSError, ValueError):
            for record in precautions.read(snapshot).files:
                name = record.path
                if not (root / name).exists() and (store / name).is_file():
                    out.setdefault(str(Path(name).parent), []).append(name)
    return {album: sorted(names) for album, names in out.items()}


def _owed_folders(staging: Path, root: Path, known: dict[str, Recorded]) -> set[str]:
    """Every folder a batch that did not finish is still owed, under the root."""
    out: set[str] = set()
    for one in known.values():
        if one.done:
            continue
        out.update(one.albums)
        out.update(one.became)
        snapshot = staging / one.snapshot
        if snapshot.is_file():
            with contextlib.suppress(OSError, ValueError):
                out.update(territory(precautions.read(snapshot),
                                     intake.read_made(intake.made_path(snapshot), root,
                                                      folders=True)))
    return out


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

    where = staging / STAGED
    # **the index is read for every run, and obeyed only by a resuming one.** A dry run writes
    # nothing and skips nothing, but it is the natural thing to run when a collection looks wrong,
    # so it must be able to say what an unfinished batch left behind (R-388).
    seen = read_index(where, root)
    known = dict(seen) if resume and not dry_run else {}
    # **what a batch that did not finish is still owed**, read before anything is skipped. Its own
    # albums, the folders the pass made for them, and every folder its snapshot recorded a file in —
    # so a half-renamed album is covered whichever of its two names the share now shows.
    owed = _owed_folders(staging, root, seen)
    if owed:
        log(f"  {len(owed)} folder(s) are owed a copy back by a batch that did not finish")
    # **and what is only in the store is said out loud, dry run or not** (R-388). A pass stopped
    # between a file's move-aside and its replacement leaves that file only in the store, and until
    # now the only way to learn that was to resume or to restore and compare. A person looking at
    # their collection and wondering why an album is short gets the answer from the run itself.
    for album, names in sorted(_only_in_the_store(staging, root, seen).items()):
        log(f"  {len(names)} file(s) of {album} are only in the store of originals; resuming or "
            f"restoring puts them back: {', '.join(Path(n).name for n in names[:3])}"
            + (" …" if len(names) > 3 else ""))

    sized = album_sizes(root, source)
    # **an album that already holds a plan was taken in by an earlier pass**, and the way to bring it
    # to changed settings is a repair, which needs no staging. Skipping it here is what makes a
    # staged run idempotent across the renames it does itself: a batch's name cannot survive its own
    # albums being moved into the scheme, so the record alone would copy everything out a second time.
    # **But a plan file may not say a batch is finished** (R-374, ruling b). Only the index says
    # that, and a folder a half-done batch is owed is never skipped — reading the share as the answer
    # is what walked a resume past the batch a SIGTERM had interrupted and left its twelve rewritten
    # files with no record at all (I-237).
    if taken := [album for album, _ in sized if (album / PLAN_FILE).is_file()
                 and str(album.relative_to(root)) not in owed]:
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

    if finished := [one for one in known.values() if one.done]:
        log(f"  {len(finished)} batch(es) were done by an earlier run and are skipped")

    if dry_run:
        for n, batch in enumerate(made, 1):
            log(f"batch {n} —")
            kept = _as_if(service, root, batch, choices, log)
            done.tracks += kept
        # **what the store would hold**, which is the disk this asks of the share: every file the
        # pass writes has its original moved there first, so at most one copy of what it touches.
        would = sum(size for _, size in sized)
        log(f"the store of your own originals would hold up to {would / 1e9:.2f} GB in "
            f"{aside_for(root)} — a rename on the share, so nothing crosses the network, and it is "
            "what makes a restore byte for byte")
        log("nothing was written, here or on the share. `--apply` does it.")
        return done

    staging.mkdir(parents=True, exist_ok=True)
    # **one staged pass per collection** (R-390). A dry run has returned above without
    # taking anything: it writes nothing and so cannot be in anybody's way.
    take_the_lock(root, log)
    try:
        for n, batch in enumerate(made, 1):
            key = batch_key(root, batch)
            if (already := known.get(key)) and already.done:
                continue
            service.check()
            log(f"batch {n} of {len(made)}: {len(batch.albums)} album(s), {batch.bytes / 1e9:.2f} GB")
            snapshot = snapshot_for(staging, root, batch)
            # **the record of this batch goes in before its copy back, saying it is not done.** That is
            # what a run stopped mid-flight leaves behind, and it is what the next one reads.
            known[key] = Recorded(key=key, snapshot=snapshot.name,
                                  albums=[str(album.relative_to(root)) for album in batch.albums])
            write_index(where, root, known)
            try:
                _one_batch(service, root, batch, choices, staging, done, log, snapshot=snapshot)
            except Stopped as e:
                # the one line, and the run is over. No traceback: there is nothing here a stack says
                # that the sentence does not.
                done.stopped = str(e)
                log(f"⚠ {done.stopped}")
                return done
            known[key] = replace(known[key], done=True, became=sorted(
                intake.read_made(intake.made_path(snapshot), root, folders=True)))
            write_index(where, root, known)
            done.done.append(str(batch.albums[0].relative_to(root)))
    finally:
        let_the_lock_go(root)
    log(f"{len(done.done)} batch(es) taken in; {done.tracks} track(s); "
        f"{done.copied_out / 1e9:.1f} GB from the share, {done.copied_back / 1e9:.1f} GB back")
    if done.superseded:
        log(f"{len(done.superseded)} file(s) the scheme replaced were removed from the share")
    if done.changed_meanwhile:
        # the run's total, said once (R-379), with the names, because this is the one thing in the
        # whole pass that somebody else's hand caused and the owner may want to look at it
        log(f"{len(done.changed_meanwhile)} file(s) were changed on the share by something else "
            "while this ran and were left exactly as they are: "
            + ", ".join(done.changed_meanwhile[:3])
            + (" …" if len(done.changed_meanwhile) > 3 else ""))
    if done.unverified:
        log(f"⚠ {len(done.unverified)} file(s) could not be verified after the copy back — "
            "the share's own file was left in place")
    log(f"this machine held at most {done.peak_staged / 1e9:.2f} GB at once")
    if done.aside:
        log(f"{len(done.aside)} file(s) of yours moved aside into {aside_for(root)} "
            f"({done.moved_aside / 1e9:.2f} GB) — that is the way back, byte for byte, and it stays "
            "until you remove it")
    if done.snapshots:
        log(f"the way back is {len(done.snapshots)} snapshot(s) in {staging}, one per batch: "
            f"`noaap take-in {root} --restore {staging} --apply` puts the whole collection back, "
            "and one snapshot of them puts one batch back")
    return done


@dataclass
class Restored:
    """What a restore of a whole staged pass came to."""

    snapshots: list[str] = field(default_factory=list)
    files: int = 0
    missing: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    swept: list[str] = field(default_factory=list)      # the pass's own in-flight files, removed

    @property
    def clean(self) -> bool:
        return not (self.changed or self.lost or self.orphans)


def restore_all(root: Path, where: Path, *, apply: bool = False,
                log: Callable[[str], None] = lambda s: None) -> Restored:
    """Put the collection back from **every** snapshot of the pass, and then check the store.

    `where` is the staging folder, or the index in it. The snapshots are walked **oldest record
    last** (`snapshots_of`), so the oldest record of a file has the last word: a snapshot a resume
    wrote may hold files an interrupted copy back had already replaced, and those are not originals.

    Then the store of originals is read (R-374, ruling c). `precautions.restore` only ever looks in
    it for a file some snapshot recorded, so a file in there that no snapshot names is a file whose
    way back exists and cannot be found — which is exactly what the interrupted batch left (I-237).
    It is named, and the restore does not come back clean.
    """
    # **not while a pass is writing** (R-390): a restore then would undo what it is doing, file by
    # file, and neither would know. The restore takes no lock of its own — it is the thing a person
    # reaches for when something went wrong — but it refuses to run under a live one.
    refuse_while_held(root)
    out = Restored()
    kept = aside_for(root)
    named: set[str] = set()
    for snapshot in snapshots_of(where, root):
        snap = precautions.read(snapshot)
        husks = intake.read_made(intake.made_path(snapshot), root, folders=True)
        got = precautions.restore(
            snap, root, apply=apply, kept=kept if kept.is_dir() else None,
            made=intake.read_made(intake.made_path(snapshot), root), folders=husks,
            pictures=precautions.pictures_for(snapshot),
            within=territory(snap, husks), log=log)
        out.snapshots.append(snapshot.name)
        out.files += len(snap.files)
        out.missing += got.missing
        out.changed += list(got.changed)
        out.lost += list(got.lost)
        named.update(r.path for r in snap.files)
        # **and the pass's own debris goes** (I-264, R-389). A signal killed mid-copy leaves a file
        # wearing the in-flight suffix, and somebody who restores instead of resuming would be left
        # with it. Only inside this batch's own territory — the folders its snapshot recorded a file
        # in and the folders the pass made for them — never a walk of the root (§9, slice 107).
        for folder in territory(snap, husks):
            with contextlib.suppress(OSError):
                for q in sorted((root / folder).iterdir()):
                    if q.is_file() and q.name.endswith(PART):
                        name = str(q.relative_to(root))
                        if apply:
                            q.unlink()
                        out.swept.append(name)
                        log(f"  {name} was left behind by a run that stopped mid-copy and "
                            + ("is gone" if apply else "would be removed"))
        log(f"  {snapshot.name}: {len(snap.files)} file(s) recorded")
    out.orphans = orphans_in_store(root, named)
    for name in out.orphans:
        log(f"  ⚠ {name} is in {aside_for(root)} and no snapshot of this pass names it — "
            "its way back is there and nothing can find it")
    return out


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
               done: Staged, log: Callable[[str], None], snapshot: Path | None = None) -> None:
    """Copy out, write down, take in, copy back, remove. Nothing else runs while this does."""
    here = staging / "batch"
    if here.exists():
        shutil.rmtree(here)       # our own staging folder from a run that stopped; the share is intact
    # **asked before the first copy, every batch** (R-378, ruling 2). The filesystem is measured once
    # at the start of the run and a batch size may be given by hand, and neither knows what is free
    # *now* — something else on the machine may have filled the disk since, and an explicit
    # `--batch-size` is a wish rather than a measurement. A batch that cannot fit is refused rather
    # than attempted: the alternative is the copy out dying partway, which is what it did.
    free, where = free_space(staging)
    if batch.bytes > free:
        raise Stopped(says_no_room(batch, free, staging, where))
    waiting = Waiting(log)
    for album in batch.albums:
        inside = here / album.relative_to(root)
        inside.parent.mkdir(parents=True, exist_ok=True)
        try:
            with waiting:
                waiting.beat(album)
                shutil.copytree(album, inside)
        except (OSError, shutil.Error) as e:
            # one line, not a tuple per file. ENOSPC is the one this was built for; anything else the
            # copy out can raise — a share that went away, a permission — gets its own text and the
            # same promise, because the batch has not been written back and the share is untouched.
            left, said = free_space(staging)
            if _out_of_space(e):
                raise Stopped(says_no_room(batch, left, staging, said)) from e
            raise Stopped(
                f"the batch could not be copied to {staging}: {_first_reason(e)}. Nothing was "
                "written back and nothing on the share was touched. The same command resumes."
            ) from e
        done.copied_out += sum(p.stat().st_size for p in inside.rglob("*") if p.is_file())
    # **this pass's own debris goes before anything else happens** (I-264, R-389). A signal does not
    # run a `finally`, so a file killed mid-copy stays on the share under the name it wore in flight
    # — and it stayed through the resume, through the restore, to the end, leaving the owner's album
    # holding a piece of a file nobody named. `PART` is a suffix nothing but this pass uses, so a
    # file wearing it inside a folder of this batch is ours and nobody else's.
    # **Both sides, and in this order.** The copy out has just taken that piece into the staged copy
    # along with the album, so removing it only from the share would have the copy back write it
    # straight out again — measured, in the case below.
    for left in sorted(_in_flight(root, here)):
        for q in (here / left, root / left):
            with contextlib.suppress(OSError):
                q.unlink()
        log(f"  removed {left}, left behind by a run that stopped mid-copy")
    held = sum(p.stat().st_size for p in here.rglob("*") if p.is_file())
    done.peak_staged = max(done.peak_staged, held)
    log(f"  copied {held / 1e9:.2f} GB to {here}")

    try:
        _the_batch(service, root, batch, choices, staging, done, log, snapshot, here)
    except Stopped:
        raise
    except (OSError, shutil.Error) as e:
        # **the share can go away mid batch** (I-253). Everything from here on touches it — the
        # folding probe, the snapshot's own reads, every write back — and a mount point that is gone
        # answers with an errno, which used to reach the user as a stack trace through two modules.
        # The batch has not been recorded as done, so the same command resumes it; what the copy
        # back had already written and verified is on the share and is in the snapshot, which is
        # what a restore needs.
        raise Stopped(
            f"the collection at {root} could not be reached: {_first_reason(e)}. The batch it was "
            "working on is not recorded as finished, so the same command resumes it when the "
            "collection is there again."
        ) from e


def _the_batch(service: Any, root: Path, batch: Batch, choices: intake.Choices, staging: Path,
               done: Staged, log: Callable[[str], None], snapshot: Path | None, here: Path) -> None:
    """The rest of one batch: write it down, take it in, copy it back, and remove the staged copy."""
    from .service import Service

    snapshot = snapshot or snapshot_for(staging, root, batch)
    # **a snapshot this batch already has is authoritative and is not written again** (R-374, ruling
    # a). A first version deleted it and took it afresh. On a resume the share may already hold the
    # files an interrupted copy back replaced, so the staging copy is of *those* — and recording
    # them would call them the originals, which is the one thing the way back must never say.
    if snapshot.is_file():
        was = {r.path: r for r in precautions.read(snapshot).files}
        log(f"  the way back for this batch was already written down: {snapshot.name}, "
            f"{len(was)} file(s)")
        if back := _from_the_store(root, here, was):
            log(f"  {len(back)} file(s) were only in the store of originals and are back in the "
                f"batch: {', '.join(back[:3])}" + (" …" if len(back) > 3 else ""))
    else:
        precautions.take(here, snapshot, log=lambda s: None)
        was = {r.path: r for r in precautions.read(snapshot).files}
        log(f"  wrote down {len(was)} file(s)")

    # **what an earlier run of this batch already recorded as its own, read before the pass runs.**
    # `intake.take_in` writes its own record over this file, so after it there is nothing left of the
    # interrupted run's — and that record is the only thing that knows about the plan file its copy
    # back put on the share.
    was_made = intake.read_made(intake.made_path(snapshot), root)
    was_husks = intake.read_made(intake.made_path(snapshot), root, folders=True)

    # the pass, unchanged, with the staging copy as its library so the scheme may move folders
    cfg = replace(service.cfg, library_root=here)
    staged_service = Service(cfg, here, log=lambda s: None, on_track=service.on_track,
                             mb=service.mb, lrclib=service.lrclib)
    got = intake.take_in(staged_service, here, choices, dry_run=False, snapshot=snapshot,
                         keep=None, resume=False, log=lambda s: None)
    done.tracks += got.tracks
    log(f"  taken in: {got.adopted} album(s), {got.tracks} track(s), "
        f"{got.renamed} renamed, {got.retagged} rewritten")

    # **the record of what the pass made is rewritten in the share's terms** (R-354 defect 2). The
    # pass wrote it against the staging copy, and `read_made` checks the root a record was written
    # for — rightly, since a record of another root is not this one's — so a restore pointed at the
    # share read nothing and left every plan and cover the pass had made. The paths are already
    # relative and mean the same thing on either side; only the root they are stamped with was wrong.
    # **Before the copy back, not after it** (R-374). Between the two is where a SIGTERM leaves the
    # share holding files the pass made and no record of them: the restore put the owner's originals
    # back from the store and left the plan and the renamed folder standing beside them. A record
    # naming a file that never reached the share costs nothing — a restore removes what is there.
    # **and it is added to, never replaced** — the same reason the snapshot is not re-recorded. A
    # batch that is run again inherits what the interrupted run's copy back already put on the
    # share: the plan file is *there* when the album is copied out, so the pass does not create one,
    # its record of what it made is empty, and rewriting the record with that emptiness left the
    # owner's album with a plan file nothing would take away. Found by the gate's own SIGTERM run —
    # the one file in 52 that the oracle still called a difference.
    intake.write_made(intake.made_path(snapshot), root,
                      [*was_made, *intake.read_made(intake.made_path(snapshot), here)],
                      [*was_husks, *intake.read_made(intake.made_path(snapshot), here, folders=True)])
    _copy_back(root, here, was, done, log, sweep=bool(service.cfg.remove_empty_folders),
               snapshot=snapshot)
    shutil.rmtree(here)
    # **the batch's snapshot stays, and so does the record of what the pass made.** The share's own
    # untouched file is the way back only until its replacement is verified and the old one removed;
    # after that these two are the only way back there is, and they cost half a megabyte a batch. A
    # first version of this deleted them with the staging copy, which left a finished run with no way
    # back at all — the one thing the whole package is for.
    intake.state_path(snapshot).unlink(missing_ok=True)      # the resume file, which is spent
    done.snapshots.append(str(snapshot))
    log(f"  the way back for this batch: {snapshot.name} (and {intake.made_path(snapshot).name})")


def territory(snapshot: precautions.Snapshot, made_folders: Iterable[str]) -> list[str]:
    """How far a restore of this batch may look for a renamed file: the batch's own territory.

    The folders the snapshot recorded a file in, and the folders the pass made for them. Nothing
    else: a batch's snapshot does not record the other batches, so without this the search for a
    recording reached into another batch's album and renamed its file away (round 2 of the gate).
    """
    out = {str(Path(r.path).parent) for r in snapshot.files if str(Path(r.path).parent) != "."}
    return sorted(out | {name for name in made_folders})


def aside_for(root: Path) -> Path:
    """Where a file the copy back replaces is moved to: beside the collection, on the share itself.

    **A rename, so nothing crosses the wire** (R-364). The share's own file used to be the way back
    only until its replacement was verified and it was deleted; after that a restore could put the
    names, the tags and the pictures back but not the **bytes**, because a tag round-trip through
    mutagen is not byte-identical and in a staged run nothing else keeps the original. Round 1 of the
    gate found 40 files back with identical tags, identical pictures and different bytes.
    So the original is not deleted, it is moved — and `restore(kept=…)` already knows how to copy
    from such a store, which is the only way back that is byte for byte. It stays until the user
    removes it.

    **Named after the collection** (R-370, ruling 1). `noaap-originals` alone, beside the root, is
    one store for every collection under that parent — and two of them can hold the same artist and
    album, so one would quietly keep the other's file instead of its own. The same shape as the one
    fixed snapshot name that served two collections, found in P81b.
    """
    return root.parent / ASIDE / root.name


def wrote_path(snapshot: Path) -> Path:
    """Where a batch records what its copy back has written and verified, beside its snapshot."""
    return snapshot.with_suffix(".wrote.jsonl")


def read_wrote(snapshot: Path) -> dict[str, str]:
    """What an earlier run of this batch put on the share and verified: path -> the file's digest."""
    where = wrote_path(snapshot)
    if not where.is_file():
        return {}
    out: dict[str, str] = {}
    for line in where.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("path") and row.get("bytes"):
            out[str(row["path"])] = str(row["bytes"])
    return out


def note_wrote(snapshot: Path, name: str, digest: str) -> None:
    """One line per file, appended the moment it is verified — an interruption loses nothing.

    **This is how the pass tells its own earlier work from somebody else's** (R-387). The guard that
    refuses to write over a file which has changed since the snapshot compares against the
    *pristine* record, so after a stopped run its own writes looked like a third party's hand: the
    resume named three files it had written itself as "changed on the share" and refused to touch
    them. On the user's collection that would be noise in the one place a person must be able to
    trust.
    """
    with wrote_path(snapshot).open("a") as fh:
        fh.write(json.dumps({"path": name, "bytes": digest}, ensure_ascii=False) + "\n")


def _in_flight(root: Path, here: Path) -> set[str]:
    """Names wearing the in-flight suffix in this batch's folders, on the share and in the copy."""
    out: set[str] = set()
    for album in sorted({p.parent for p in here.rglob("*") if p.is_file()}):
        inside = album.relative_to(here)
        for where in (album, root / inside):
            with contextlib.suppress(OSError):
                out |= {str(inside / q.name) for q in where.iterdir()
                        if q.is_file() and q.name.endswith(PART)}
    return out


def _from_the_store(root: Path, here: Path, was: dict[str, Any]) -> list[str]:
    """Put back into the staged copy every recorded file the share has lost but the store still has.

    **The gate's own finding (I-260, R-387).** A pass stopped between a file's move-aside and its
    replacement leaves that file *only* in the store. A resume takes the batch from what the share
    holds now, which no longer includes it, so the batch is written back one file short and the
    owner's album stays short a track until somebody restores — and a resume is what somebody does
    instead of restoring. Measured on the real share: the first pass copied out 0.077 GB of an
    album, the resume 0.071, the difference being one 6.3 MB track sitting in the store.

    The store is the authority for exactly these paths: `_move_aside` only ever puts an original
    there, under the name the snapshot knows it by.
    """
    store = aside_for(root)
    if not store.is_dir():
        return []
    out = []
    for name in sorted(was):
        if (here / name).exists() or not (store / name).is_file():
            continue
        target = here / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(store / name, target)
        out.append(name)
    return out


def _moved_on(path: Path, recorded: Any) -> bool:
    """Whether the share's copy is no longer the one the snapshot wrote down.

    Size and mtime, which a snapshot records for every file and which cost a `stat`. A digest would
    be surer and would read every file of every batch over the wire again; this catches anything that
    has been written to, which is the question being asked.
    """
    if recorded is None or not hasattr(recorded, "size"):
        return False
    try:
        st = path.stat()
    except OSError:
        return False
    return st.st_size != recorded.size or st.st_mtime_ns != recorded.mtime_ns


def _move_aside(path: Path, name: str, root: Path, done: Staged) -> bool:
    """Move one file into the store under the path the snapshot knows it by. A rename, once."""
    where = aside_for(root) / name
    if where.exists():
        return False                 # an earlier batch or run already kept this one
    where.parent.mkdir(parents=True, exist_ok=True)
    carried = path.stat().st_size
    try:
        os.replace(path, where)      # atomic, and within the share so it costs no bytes
    except OSError:
        shutil.move(str(path), str(where))   # a share that will not rename across directories
    done.moved_aside += carried
    return True


def _copy_back(root: Path, here: Path, was: dict[str, precautions.Recorded], done: Staged,
               log: Callable[[str], None], sweep: bool = False, snapshot: Path | None = None) -> None:
    """Put the batch back on the share, with one watchdog over the whole of it (R-402).

    The watchdog is opened here rather than inside, because the work below returns early when a file
    cannot be verified and a thread left running would go on talking about a share that is no longer
    being asked anything.
    """
    with Waiting(log) as waiting:
        _writing_back(root, here, was, done, log, waiting, sweep=sweep, snapshot=snapshot)


def _writing_back(root: Path, here: Path, was: dict[str, precautions.Recorded], done: Staged,
                  log: Callable[[str], None], waiting: Waiting, sweep: bool = False,
                  snapshot: Path | None = None) -> None:
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
    # **what this batch refused to write over**, kept so the superseded loop cannot claim it. A file
    # somebody else changed while the pass ran is skipped and named — and it was then moved off the
    # share by that loop, because a skipped file is not a verified one and the loop takes every
    # recorded path that is not. So the pass promised "nothing of theirs was replaced" and removed
    # their file from the album; the store had it, and only a restore put it back (I-247, R-380).
    theirs: set[str] = set()
    # **and this batch's own list, so the line this batch prints is about this batch** (R-379).
    # `done` carries the whole run, so counting it there made every later batch repeat "1 file(s)
    # were changed on the share while this ran" about a file of the first batch: four batches, four
    # identical lines, one file. The run's total is said once, at the end.
    mine: list[str] = []
    # **what an earlier run of this batch wrote and verified** (R-387), so the pass can tell its own
    # hand from a third party's. Empty for a batch running for the first time.
    wrote = read_wrote(snapshot) if snapshot else {}
    # **the share decides what "the same name" means, so it is asked once and used everywhere here.**
    # Looking a recorded name up exactly is wrong on a folding share: `Der W/III/x` is not a key of a
    # snapshot that wrote `Der W/iii/x`, and the file about to be written over was therefore not moved
    # aside — twelve files of one album in round 1, overwritten with nothing kept.
    # **one watchdog over the whole of it, beating on progress** (R-402): a share that goes quiet in
    # the rename, the read back, the move aside or the empty-folder sweep is as silent as one that
    # goes quiet in the write, and the first version only watched the write.
    waiting.beat(root)
    folding = precautions.folds_case(root)
    recorded_by = {name.casefold() if folding else name: name for name in was}

    def recorded(name: str) -> str | None:
        return recorded_by.get(name.casefold() if folding else name)

    for album in sorted({p.parent for p in here.rglob("*") if p.is_file()}):
        inside = album.relative_to(here)
        for path in sorted(p for p in album.iterdir() if p.is_file()):
            name = str(inside / path.name)
            target = root / name
            waiting.beat(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            # **the owner's own file is moved aside before it is written over** (R-364). Where the
            # pass did not rename it, the new file lands on the old one and there would be nothing
            # left to go back to; the superseded loop below only ever sees the names that *did*
            # change.
            # **a file somebody changed while this ran is not written over** (R-365, point 2). The
            # snapshot says what it was when the batch was copied out; if the share's copy no longer
            # matches it in size or mtime, somebody has had it since and their change is not ours to
            # throw away. Named, skipped, and the rest of the batch goes on.
            known = recorded(name)
            if target.is_file() and _moved_on(target, was.get(known) if known else None):
                # **its own earlier result is not somebody else's change** (R-387). The share's file
                # differs from the pristine snapshot because a stopped run of this very batch wrote
                # it; its digest is on record, so this is answered rather than assumed.
                if wrote.get(name) and bytes_sha(target) == wrote[name]:
                    verified.add(name)
                    continue
                done.changed_meanwhile.append(name)
                mine.append(name)
                # by both spellings: the name the staged copy wants and the name the snapshot
                # recorded, which on a folding share can differ in case
                theirs.add(name)
                if known:
                    theirs.add(known)
                log(f"  ⚠ {name} changed on the share since this batch was copied out — "
                    "left exactly as it is")
                continue
            if known and target.is_file() and _move_aside(target, known, root, done):
                done.aside.append(known)
            tmp = target.with_name(target.name + PART)
            try:
                shutil.copy2(path, tmp)
            except BaseException:
                # a share that died mid-write leaves nothing of ours behind, even before a resume
                with contextlib.suppress(OSError):
                    tmp.unlink()
                raise
            os.replace(tmp, target)          # atomic within the share
            done.copied_back += path.stat().st_size
            if (digest := bytes_sha(target)) == bytes_sha(path):
                verified.add(name)
                if snapshot:
                    note_wrote(snapshot, name, digest)
            else:
                done.unverified.append(name)
            waiting.beat(target)       # this one is through: the share is answering
        log(f"  {inside}: back on the share")
    if mine:
        log(f"  {len(mine)} file(s) of this batch were changed on the share while it ran and were "
            "left exactly as they are; nothing of theirs was replaced")
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
    # **the share decides what "the same name" means, and it is asked rather than assumed.** This
    # compared `Path.samefile`, and CIFS hands out a different inode for each spelling of one file
    # (130595 against 130597, measured) — so the guard answered "a different file" every time and
    # fourteen files of an album were deleted after being verified. The case fixture passed because
    # it used a symlink on ext4, where the inode really is shared.
    written = sorted(verified)
    emptied: set[Path] = set()
    for name in sorted(set(was) - verified - theirs):
        old = root / name
        if not old.is_file():
            continue
        if any(precautions.same_name(name, kept, folding) for kept in written):
            continue
        if any(_same(old, root / kept) for kept in written):
            continue        # a second line, for a filesystem whose inodes do mean something
        waiting.beat(old)
        if _move_aside(old, name, root, done):
            done.aside.append(name)
        else:
            old.unlink()             # already kept from an earlier run: this copy is spare
        emptied.add(old.parent)
        done.superseded.append(name)
    if done.superseded:
        log(f"  {len(done.superseded)} file(s) the scheme replaced removed: "
            f"{', '.join(done.superseded[:3])}" + (" …" if len(done.superseded) > 3 else ""))
    # **only the folders this pass emptied**, unless the library is set to clear the owner's too.
    for folder in precautions.empty_under(root, emptied, everything=sweep):
        waiting.beat(folder)
        with contextlib.suppress(OSError):
            folder.rmdir()
        log(f"  removed the empty folder {folder.relative_to(root)}")


def _out_of_space(e: BaseException) -> bool:
    """Whether this is the disk being full, however the copy wrapped it."""
    if isinstance(e, OSError) and e.errno == errno.ENOSPC:
        return True
    return isinstance(e, shutil.Error) and any(
        f"[Errno {errno.ENOSPC}]" in str(part) for row in e.args for part in (row if
                                                                              isinstance(row, (list, tuple))
                                                                              else [row]))


def _first_reason(e: BaseException) -> str:
    """One reason out of an exception that may carry one per file."""
    if isinstance(e, shutil.Error) and e.args and e.args[0]:
        first = e.args[0][0] if isinstance(e.args[0], (list, tuple)) else e.args[0]
        if isinstance(first, (list, tuple)) and len(first) >= 3:
            return str(first[2])
        return str(first)
    return str(e) or e.__class__.__name__


def _same(one: Path, two: Path) -> bool:
    """Whether two paths are the same file, whatever the filesystem thinks a name is."""
    try:
        return one.samefile(two)
    except OSError:
        return False


__all__ = [
    "LOCK",
    "PART",
    "SLOW",
    "STAGED",
    "Batch",
    "Held",
    "NoRoom",
    "Recorded",
    "Restored",
    "Staged",
    "Stopped",
    "album_sizes",
    "aside_for",
    "batch_key",
    "batches",
    "free_space",
    "in_bytes",
    "lock_path",
    "orphans_in_store",
    "read_index",
    "read_wrote",
    "restore_all",
    "says_room",
    "snapshot_for",
    "snapshots_of",
    "take_in_staged",
    "take_the_lock",
    "territory",
    "who_holds",
    "write_index",
]
