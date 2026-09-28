"""A collection becomes a library where it already stands (DESIGN §9, slice 58).

Adoption writes **one file per album and nothing else**: the plan, beside the audio its owner
already arranged. Not a byte of that audio is read for anything but measuring it, no file is
renamed, no folder is moved, and no tag is written. Renaming and retagging are separate things a
person asks for afterwards, each recording what it would take away.

Dry by default, like `merge`: a bare `adopt` says what it would write and writes nothing.

**Why the plan says `keep_names` and `keep_tags`:** without them the next ordinary pass renames
every file into noaap's scheme — and, before slice 58, renamed an mp3 to `.opus` and could then no
longer read it. An album taken in where it stands is the collection's, not ours.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import sources
from .download import PLAN_FILE, save_plan
from .models import AlbumPlan
from .plan import build_plan
from .sources_folder import read_tags
from .text import key as text_key

# what the plan would assert about a file, and therefore what an undo has to be able to give back
WRITTEN = ("title", "artist", "albumartist", "album", "tracknumber", "discnumber", "date")


@dataclass
class Adoption:
    """One album folder, and what adopting it would come to."""

    album_dir: Path
    plan: AlbumPlan | None = None
    refused: str = ""

    @property
    def tracks(self) -> int:
        return len(self.plan.tracks) if self.plan else 0


@dataclass
class Survey:
    adoptions: list[Adoption] = field(default_factory=list)

    @property
    def taking(self) -> list[Adoption]:
        return [a for a in self.adoptions if a.plan is not None]

    @property
    def refused(self) -> list[Adoption]:
        return [a for a in self.adoptions if a.plan is None]

    def counts(self) -> dict[str, int]:
        return {"albums": len(self.taking), "tracks": sum(a.tracks for a in self.taking),
                "refused": len(self.refused)}


def examine(album_dir: Path, source: Any, library: Path) -> Adoption:
    """Read one folder and build the plan adoption would write. Touches nothing."""
    if (album_dir / PLAN_FILE).is_file():
        return Adoption(album_dir, refused="already a noaap album")
    try:
        collection = source.collection(str(album_dir))
    except sources.SourceError as e:
        return Adoption(album_dir, refused=str(e))

    albums = {text_key(a) for e in collection.entries if (a := e.music.album)}
    if len(albums) > 1:
        # the owner made this folder; deciding which files are one album is theirs, not ours
        return Adoption(album_dir, refused=f"{len(albums)} different albums by their own tags")

    plan = build_plan(collection, source=source)
    plan.provider = getattr(source, "name", sources.DEFAULT)
    plan.own_the_candidates()
    plan.keep_names = plan.keep_tags = True
    plan.folder = str(album_dir.relative_to(library))
    plan.adopted = {"folder": plan.folder, "at": dt.date.today().isoformat()}

    for track in plan.tracks:
        # the file is here and finished: that is what adoption means. Its name is the owner's.
        track.state = "done"
        track.filename = Path(track.video_id).name
        track.adopted_name = track.filename
        track.adopted_tags = _was(Path(track.video_id), plan, track)
    return Adoption(album_dir, plan=plan)


def _was(audio: Path, plan: AlbumPlan, track: Any) -> dict[str, Any]:
    """What the file says today, for every field noaap would ever write into it.

    Only those fields: an undo has to put back what a retag took away, and nothing else is ours to
    restore. A key the file does not carry is recorded as `None` — *absent* is a value too, and
    without it an undo would leave behind a tag the owner never had.
    """
    said = read_tags(audio)
    out: dict[str, Any] = {}
    for key in WRITTEN:
        mine = {"date": "year"}.get(key, key)
        out[key] = said.get(mine)
    return out


def survey(root: Path, library: Path, source: Any, artist: str | None = None,
           album: str | None = None, log: Callable[[str], None] = lambda s: None) -> Survey:
    """Every album under `root`, in the order a person would read them."""
    found = Survey()
    folders = [Path(ref.url) for ref in source.listing(str(root))]
    log(f"{len(folders)} album folder(s) under {root.name}")
    for n, album_dir in enumerate(sorted(folders), 1):
        if not in_scope(album_dir, root, artist, album):
            continue
        found.adoptions.append(examine(album_dir, source, library))
        if n % 25 == 0:
            log(f"  read {n}/{len(folders)}")
    return found


def in_scope(album_dir: Path, root: Path, artist: str | None, album: str | None) -> bool:
    """`--only` is the artist folder, `--album` the album folder — both by name, as they read."""
    parts = album_dir.relative_to(root).parts
    if artist and (len(parts) < 2 or text_key(parts[-2]) != text_key(artist)):
        return False
    return not (album and text_key(parts[-1]) != text_key(album))


def report(found: Survey, *, applying: bool = False) -> list[str]:
    counts = found.counts()
    lines = [f"{counts['albums']} album(s), {counts['tracks']} track(s)"]
    for refused in found.refused:
        lines.append(f"  left alone: {refused.album_dir.name} — {refused.refused}")
    lines.append("one plan per album is written, and nothing else: no file is renamed, "
                 "no folder moved, no tag written")
    lines.append("adopting." if applying else "nothing was written. `noaap adopt --apply` does it.")
    return lines


def carry_out(found: Survey, log: Callable[[str], None] = lambda s: None) -> dict[str, int]:
    """Write the plans. The only thing this creates is one file per album."""
    done = {"adopted": 0, "tracks": 0}
    for adoption in found.taking:
        save_plan(adoption.plan, adoption.album_dir)
        done["adopted"] += 1
        done["tracks"] += adoption.tracks
        log(f"  {adoption.plan.albumartist} — {adoption.plan.album} ({adoption.tracks} track(s))")
    return done


def albums_of(found: Survey) -> Iterable[AlbumPlan]:
    return (a.plan for a in found.taking if a.plan)


__all__ = ["Adoption", "Survey", "carry_out", "examine", "in_scope", "report", "survey"]
