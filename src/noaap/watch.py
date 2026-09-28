"""Noticing that something arrived, and nothing else (DESIGN §9, slice 59).

**The watcher never does the work.** It looks at a folder, waits until what arrived has stopped
moving, and hands an ordinary job to the lanes that already exist. What that job does is what
`fetch <folder>` and `adopt` already do, which is the whole reason this file is small.

It polls. Not because inotify is unavailable — it is, through ctypes, and this machine allows
254 776 watches against the collection's 226 folders — but because **a full walk of the reference
collection's 2153 files costs 0.01 s**, and because inotify cannot see what another client writes
on an NFS or SMB share, which is where a music collection often lives. The settle window below
means we deliberately wait seconds anyway, so sub-second notice would buy nothing (R-200).

Everything here is pure and takes its clock as an argument, so the cases drive time forward
instead of sleeping.
"""

from __future__ import annotations

import fnmatch
import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# What a file is called while it is still being written. A download client renames when it is
# finished, and the rename starts its own settle clock, so these are never half-taken.
IN_FLIGHT = ("*.part", "*.crdownload", "*.tmp", "*.temp", "*.!qb", "*.partial", "*.download", "~*")
# noaap's own working folders inside a library, which are not arrivals
OURS = (".recycle", ".parts", ".originals")

SETTLE = 20.0   # seconds a thing must sit still before it counts as arrived
INTERVAL = 10.0  # seconds between looks


@dataclass(frozen=True)
class Seen:
    """What one look learned about one file. Not its contents — a watcher never reads audio."""

    size: int
    mtime_ns: int


def skip(relative: Path) -> bool:
    """Is this path noaap's own, hidden, or still being written?"""
    parts = relative.parts
    if any(part.startswith(".") for part in parts) or any(part in OURS for part in parts):
        return True
    name = relative.name.lower()
    return any(fnmatch.fnmatch(name, pattern) for pattern in IN_FLIGHT)


def look(root: Path, audio: Iterable[str]) -> dict[str, Seen]:
    """One look: every audio file under `root`, by its path relative to it.

    A `stat` per file and nothing more — the same budget as the folder provider's cheap check.
    Measured over the reference collection: 2153 files in 0.01 s.
    """
    suffixes = {s.lower() for s in audio}
    out: dict[str, Seen] = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if skip(relative) or path.suffix.lower() not in suffixes:
            continue
        try:
            stat = path.stat()
        except OSError:  # it went away between the walk and the stat, which is not an error
            continue
        out[str(relative)] = Seen(stat.st_size, stat.st_mtime_ns)
    return out


@dataclass
class Folder:
    """One album folder's state between looks."""

    files: dict[str, Seen] = field(default_factory=dict)
    changed_at: float = 0.0   # when this folder last gained, lost or altered a file
    handed_over: float | None = None  # when it was last given to a job, if it has been


@dataclass
class Watcher:
    """What has been seen, and what is ready to be handed over.

    `step` is the whole of it: give it what a look found and the time it happened, and it answers
    with the album folders that have stopped moving since the last time it said so.
    """

    settle: float = SETTLE
    folders: dict[str, Folder] = field(default_factory=dict)

    def step(self, files: dict[str, Seen], now: float) -> list[str]:
        """The folders that are ready, as relative paths. Empty is the ordinary answer."""
        seen: dict[str, dict[str, Seen]] = {}
        for name, mark in files.items():
            seen.setdefault(str(Path(name).parent), {})[name] = mark

        for where, found in seen.items():
            folder = self.folders.setdefault(where, Folder(changed_at=now))
            if found != folder.files:
                # **anything at all: a new file, one that grew, one that went.** The window starts
                # again, which is what carries an album arriving file by file (§9, slice 59).
                folder.files, folder.changed_at = found, now
                folder.handed_over = None

        gone = [where for where in self.folders if where not in seen]
        for where in gone:
            folder = self.folders[where]
            if folder.files:
                folder.files, folder.changed_at, folder.handed_over = {}, now, None

        ready = []
        for where, folder in sorted(self.folders.items()):
            if not folder.files or folder.handed_over is not None:
                continue
            if now - folder.changed_at < self.settle:
                continue
            folder.handed_over = now
            ready.append(where)
        return ready

    def forget(self, where: str) -> None:
        """Let this folder be handed over again — after a job failed, or on a retry."""
        if folder := self.folders.get(where):
            folder.handed_over = None

    def state(self) -> dict[str, Any]:
        """What survives a restart: what was seen and what was already handed over."""
        return {where: {"files": {n: [s.size, s.mtime_ns] for n, s in f.files.items()},
                        "handed_over": f.handed_over is not None}
                for where, f in self.folders.items() if f.files}

    @classmethod
    def restored(cls, state: dict[str, Any], settle: float = SETTLE, now: float | None = None) -> Watcher:
        """A watcher that knows what the last one knew.

        **What was already handed over is not handed over again**, and what was seen but not yet
        settled starts its window afresh — a restart is not a reason to re-import a library, and
        not a reason to lose an arrival that was still being written when we stopped.
        """
        now = time.monotonic() if now is None else now
        watcher = cls(settle=settle)
        for where, kept in (state or {}).items():
            files = {n: Seen(int(v[0]), int(v[1])) for n, v in (kept.get("files") or {}).items()}
            watcher.folders[where] = Folder(files=files, changed_at=now,
                                            handed_over=now if kept.get("handed_over") else None)
        return watcher


def album_of(relative: str) -> str:
    """The folder an arrival belongs to. A loose file at the root belongs to no album."""
    return str(Path(relative).parent)


def loose(ready: Iterable[str]) -> list[str]:
    """Folders that are the watched root itself — a loose file is not an album (R-200).

    Reported and left alone: guessing which loose files belong together is the decision the owner
    makes by putting them in a folder, and P51 refused to make it for them.
    """
    return [where for where in ready if where == "."]


def albums(ready: Iterable[str]) -> list[str]:
    return [where for where in ready if where != "."]




# -- what survives a restart (§9, slice 59) ---------------------------------------------------------
#
# Beside the config, mode 600, and **no path out of it ever reaches a log line the page shows**
# (R-200, ruling 5): a watched folder is named by its configured name, and an arrival by its path
# relative to that folder.

STATE_NAME = "watch-state.json"


def state_path() -> Path:
    from .config import config_path

    return config_path().parent / STATE_NAME


def read_state(path: Path | None = None) -> dict[str, Any]:
    try:
        return json.loads((path or state_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(state: dict[str, Any], path: Path | None = None) -> Path:
    """Written with the mode set before anything is in it: a watcher's notes are the shape of
    somebody's music collection, and that is theirs."""
    path = path or state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path




# -- the process (§9, slice 59) --------------------------------------------------------------------
#
# Its own process and its own unit, not a thread in the web service: that service is
# socket-activated and stops itself after fifteen idle minutes, and a watcher inside it would count
# as busy and keep it alive for ever, which defeats the socket. A separate watcher costs one idle
# process that stats a tree every ten seconds — and when it has something to say, **asking the
# service wakes it through that same socket**, exactly as a browser request does (R-200, ruling 4).


@dataclass
class Run:
    """One watched folder, between looks. Knows nothing about jobs and nothing about paths but its
    own root's."""

    name: str
    folder: Path
    shape: str
    watcher: Watcher
    trouble: int = 0          # consecutive looks that could not be taken
    said_missing: bool = False
    waiting: dict[str, int] = field(default_factory=dict)  # album -> attempts so far


TRIES = 3  # how often an arrival is offered again before it is left alone and said out loud


def once(run: Run, audio: Iterable[str], now: float, ask: Callable[[str, str], bool],
         log: Callable[[str], None] = lambda s: None) -> list[str]:
    """One look at one folder, and whatever it leads to. Returns the albums handed over.

    `ask(name, album)` does the asking and answers whether it was accepted; it is a parameter so
    that the cases never open a socket.
    """
    if not run.folder.is_dir():
        if not run.said_missing:
            log(f"{run.name}: the folder is not there")
            run.said_missing = True
        return []
    if run.said_missing:
        log(f"{run.name}: the folder is back")
        run.said_missing = False

    try:
        files = look(run.folder, audio)
    except OSError as e:
        run.trouble += 1
        log(f"{run.name}: could not look ({e.strerror or e})")
        return []
    run.trouble = 0

    handed: list[str] = []
    ready = run.watcher.step(files, now)
    for where in loose(ready):
        # a loose file at the root is not an album, and guessing which ones belong together is the
        # decision its owner makes by putting them in a folder (R-200)
        log(f"{run.name}: loose file(s) at the top, left alone — put them in a folder")
        run.watcher.forget(where)
    for album in albums(ready):
        tries = run.waiting.get(album, 0)
        if tries >= TRIES:
            continue  # said once, and not again until something about it changes
        if ask(run.name, album):
            run.waiting.pop(album, None)
            handed.append(album)
            continue
        run.waiting[album] = tries + 1
        run.watcher.forget(album)   # so the next look offers it again
        if run.waiting[album] >= TRIES:
            log(f"{run.name}: {album} was not taken after {TRIES} tries, leaving it")
    return handed




def runs(cfg: Any, now: float | None = None) -> list[Run]:
    """One `Run` per configured folder, knowing what the last watcher knew."""
    now = time.monotonic() if now is None else now
    state = read_state()
    out = []
    for row in cfg.watches:
        kept = state.get(row.name, {})
        out.append(Run(name=row.name, folder=row.folder, shape=row.shape,
                       watcher=Watcher.restored(kept.get("folders") or {}, now=now)))
    return out


def keep(runs: Iterable[Run], looked: str) -> dict[str, Any]:
    """What to write down, named by the watch and never by a path on the disk."""
    return {run.name: {"folders": run.watcher.state(), "looked": looked,
                       "waiting": dict(run.waiting)}
            for run in runs}


__all__ = [
    "INTERVAL",
    "SETTLE",
    "TRIES",
    "Folder",
    "Run",
    "Seen",
    "Watcher",
    "album_of",
    "albums",
    "keep", "look",
    "loose",
    "once",
    "read_state", "runs",
    "skip",
    "state_path",
    "write_state",
]
