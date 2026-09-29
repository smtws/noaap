"""The recycle bin: **noaap never removes audio, it only moves it here** (DESIGN §9, slice 49).

Everything that used to `unlink()` a track's audio now routes through `bin_track`. A bin entry is a
directory under `<library>/.recycle/` holding the audio, its sidecar, its kept original, and a
`bin.json` with enough to put all of it back — including the plan track exactly as it was.

Two decisions worth knowing about:

**At the library root, not inside the album.** An album folder can be deleted, and a bin inside it
would go with the very thing it exists to protect against.

**It never empties itself.** No age cap, no size cap, no quiet sweeping. The bin exists because the
program made a judgement the user may disagree with, and a bin that empties itself is one nobody can
rely on. `noaap recycle empty` is the only thing that removes an entry.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .download import portable, resolved
from .models import AlbumPlan, PlanTrack
from .tag import build_tags
from .trim import kept_originals

log = logging.getLogger(__name__)

RECYCLE = ".recycle"
BIN_FILE = "bin.json"
AUDIO_STEM = "audio"
WORDS = "words.lrc"
ORIGINAL_STEM = "original"
COVER_STEM = "cover"
PLAN_SNAPSHOT = "plan.json"

# why something was binned; the panel and `recycle list` show it back
REPLACED = "replaced by a better candidate"
PRUNED = "no longer in the source playlist"
DELETED = "deleted"


def bin_root(library: Path) -> Path:
    return library / RECYCLE


@dataclass
class Entry:
    """One binned thing, read back from its `bin.json`."""

    id: str
    path: Path
    when: str
    reason: str
    artist: str
    title: str
    album: str
    source_id: str
    video_id: str
    bytes: int
    data: dict[str, Any]

    @property
    def is_album(self) -> bool:
        """An album entry holds the plan and the cover, and names its tracks' entries."""
        return bool(self.data.get("plan"))

    @property
    def audio(self) -> Path | None:
        """The file this entry is about — a track's audio, or whatever else was binned."""
        for stem in (AUDIO_STEM, *(m for m in self.data.get("moved", []) if m not in ("words", "original"))):
            if found := next((p for p in sorted(self.path.glob(f"{stem}.*"))), None):
                return found
        return None

    @property
    def original(self) -> Path | None:
        return next((p for p in sorted(self.path.glob(f"{ORIGINAL_STEM}.*"))), None)

    @property
    def cover(self) -> Path | None:
        return next((p for p in sorted(self.path.glob(f"{COVER_STEM}.*"))), None)

    @property
    def words(self) -> Path | None:
        path = self.path / WORDS
        return path if path.is_file() else None

    def describe(self) -> str:
        return f"{self.artist} — {self.title} ({self.album}) · {self.reason} · {self.when[:16]}"


def _entry_id(source_id: str, video_id: str, when: datetime) -> str:
    short = hashlib.sha1(f"{source_id}/{video_id}".encode()).hexdigest()[:8]
    return f"{when.strftime('%Y%m%dT%H%M%S')}-{short}"


def bin_track(library: Path, album_dir: Path, plan: AlbumPlan, track: PlanTrack, reason: str,
              *, audio: Path | None, sidecar: Path | None = None,
              ranking: dict[str, Any] | None = None,
              evidence: dict[str, Any] | None = None) -> Path | None:
    """Move one track's files into the bin and record how to put them back.

    `audio` and `sidecar` are passed in already resolved, and **never derived from
    `track.filename` here**: a plan is a file on disk and a tampered one can name `../../something`.
    The caller checks with `_inside` and passes None when it does not like the answer. An earlier
    version of this function fell back to `album_dir / track.filename`, which reopened exactly the
    traversal `_inside` exists to close — and moving a file is worse than the unlink it replaced.

    Returns the entry directory, or None when there was nothing on disk to move — a track that was
    never downloaded leaves no entry, because an empty bin entry is only noise.
    """
    when = datetime.now(UTC)
    entry = bin_root(library) / _entry_id(plan.source_id, track.video_id, when)
    moved: list[str] = []
    entry.mkdir(parents=True, exist_ok=True)

    if audio and audio.is_file():
        shutil.move(str(audio), entry / f"{AUDIO_STEM}{audio.suffix}")
        moved.append("audio")
    # the kept original goes with the track: it is that track's audio too, and leaving it behind is
    # the inconsistency this replaces (docs/qa-catalog.md, phase 5)
    for kept in kept_originals(album_dir, track):
        if kept.is_file():
            shutil.move(str(kept), entry / f"{ORIGINAL_STEM}{kept.suffix}")
            moved.append("original")
            break
    if sidecar and sidecar.is_file():
        shutil.move(str(sidecar), entry / WORDS)
        moved.append("words")

    if not moved:
        entry.rmdir()
        return None

    # A bin entry holds a whole track and a ranking, and either can carry a path into the album
    # it came from. Written the way a plan writes one — relative to that album — so an entry
    # still restores after the library has moved (§9, slice 60, R-207 ruling 5).
    (entry / BIN_FILE).write_text(json.dumps(portable({
        "when": when.isoformat(timespec="seconds"),
        "reason": reason,
        "source_id": plan.source_id,
        "video_id": track.video_id,
        "album": plan.album,
        "albumartist": plan.albumartist,
        "artist": track.artist,
        "title": track.title,
        "folder": plan.folder,
        # the whole track, so a restore does not have to guess at anything
        "track": track.to_dict(),
        # what was written into the file, by the same function that wrote it
        "tags": build_tags(plan, track),
        "ranking": ranking or {},
        # why a pass was sure enough to move this file, in its own terms. A bin entry is what somebody
        # reads when they want to know what happened to a file of theirs (§9, slice 63).
        "evidence": evidence or {},
        "moved": moved,
    }, album_dir), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return entry


def bin_album(library: Path, plan: AlbumPlan, cover: Path | None, tracks: list[str],
              reason: str) -> Path:
    """The album itself: its plan, its cover, and which entries hold its tracks.

    Deleting an album is the largest decision a user can regret, and the source it came from may be
    gone by the time they regret it — so the plan is binned too, and a restore rebuilds the folder
    from it rather than telling them to fetch it again from something that no longer exists.
    """
    when = datetime.now(UTC)
    entry = bin_root(library) / _entry_id(plan.source_id, "album", when)
    entry.mkdir(parents=True, exist_ok=True)
    moved = ["plan"]
    (entry / PLAN_SNAPSHOT).write_text(
        json.dumps(portable(plan.to_dict(), library / plan.folder), indent=2,
                   ensure_ascii=False) + "\n", encoding="utf-8")
    if cover and cover.is_file():
        shutil.move(str(cover), entry / f"{COVER_STEM}{cover.suffix}")
        moved.append("cover")
    (entry / BIN_FILE).write_text(json.dumps(portable({
        "when": when.isoformat(timespec="seconds"),
        "reason": reason,
        "source_id": plan.source_id,
        "video_id": "",
        "album": plan.album,
        "albumartist": plan.albumartist,
        "artist": plan.albumartist,
        "title": f"(the whole album, {len(tracks)} track(s))",
        "folder": plan.folder,
        "track": {},
        "plan": plan.to_dict(),
        "tracks": tracks,
        "tags": {},
        "ranking": {},
        "moved": moved,
    }, library / plan.folder), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return entry


def album_entry(library: Path, source_id: str) -> Entry | None:
    """The newest binned album with this source id, if one is still there."""
    return next((e for e in entries(library) if e.is_album and e.source_id == source_id), None)


def _size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def entries(library: Path) -> list[Entry]:
    """Everything in the bin, newest first. A directory without a readable `bin.json` is skipped."""
    root = bin_root(library)
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        try:
            data = json.loads((path / BIN_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.debug("unreadable bin entry %s", path.name)
            continue
        # read back against the album it came from, wherever the library is now (§9, slice 60)
        data = resolved(data, library / str(data.get("folder") or ""))
        out.append(Entry(id=path.name, path=path, when=str(data.get("when", "")),
                         reason=str(data.get("reason", "")), artist=str(data.get("artist", "")),
                         title=str(data.get("title", "")), album=str(data.get("album", "")),
                         source_id=str(data.get("source_id", "")), video_id=str(data.get("video_id", "")),
                         bytes=_size(path), data=data))
    return out


def find(library: Path, entry_id: str) -> Entry | None:
    """By id, or by a unique prefix of one — nobody should have to type a whole timestamp."""
    got = [e for e in entries(library) if e.id == entry_id]
    if not got:
        got = [e for e in entries(library) if e.id.startswith(entry_id)]
    return got[0] if len(got) == 1 else None


def total(library: Path) -> tuple[int, int]:
    """(entries, bytes) — what `noaap config` and the settings panel report."""
    found = entries(library)
    return len(found), sum(e.bytes for e in found)


def empty(library: Path, older_than_days: float | None = None) -> tuple[int, int]:
    """Remove entries for good. Only ever called because somebody asked for it."""
    now = datetime.now(UTC)
    gone = freed = 0
    for entry in entries(library):
        if older_than_days is not None:
            try:
                age = (now - datetime.fromisoformat(entry.when)).total_seconds() / 86400
            except ValueError:
                continue                      # no readable date: leave it rather than guess
            if age < older_than_days:
                continue
        freed += entry.bytes
        shutil.rmtree(entry.path, ignore_errors=True)
        gone += 1
    root = bin_root(library)
    if root.is_dir() and not any(root.iterdir()):
        root.rmdir()
    return gone, freed
