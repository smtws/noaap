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
import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import sources
from .download import COVER_STEM, PLAN_FILE, save_plan
from .lyrics import sidecar_path
from .models import AlbumPlan
from .plan import build_plan
from .service import _inside
from .tag import raw_tags, restore_tags
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
    plan.adopted = {"folder": plan.folder, "at": dt.date.today().isoformat(), "added": {}}

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
    return dict(raw_tags(audio, WRITTEN))


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
        remember_added(adoption.album_dir, adoption.plan)
        done["adopted"] += 1
        done["tracks"] += adoption.tracks
        log(f"  {adoption.plan.albumartist} — {adoption.plan.album} ({adoption.tracks} track(s))")
    return done


def albums_of(found: Survey) -> Iterable[AlbumPlan]:
    return (a.plan for a in found.taking if a.plan)


__all__ = ["Adoption", "Survey", "carry_out", "examine", "in_scope", "report", "survey"]


# -- giving it back ------------------------------------------------------------------------------


def added_by_us(album_dir: Path, plan: AlbumPlan) -> list[Path]:
    """Everything noaap put into this folder: the plan, the sidecars, a cover it saved.

    Not the audio and not one file that was here before — those are the owner's, and the only
    thing that ever displaces one is the bin (§9, slice 55).
    """
    out = [album_dir / PLAN_FILE]
    for track in plan.tracks:
        words = sidecar_path(album_dir, track.filename)
        if words.is_file():
            out.append(words)
    if plan.cover_fetched.get("sha1"):
        out += [p for p in sorted(album_dir.glob(f"{COVER_STEM}.*")) if _sha1(p) == plan.cover_fetched["sha1"]]
    return out


def _sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def remember_added(album_dir: Path, plan: AlbumPlan) -> None:
    """Record the fingerprint of everything noaap has put in this folder.

    An undo removes what noaap added — but only what is still as noaap wrote it. A sidecar the user
    has edited since is **theirs now**, whatever put it there, and it stays (R-189, ruling 3).
    """
    # not the plan: it is rewritten by every pass that touches the album, and an undo removes it
    # whatever it says. The fingerprints are for the files an undo has to think about.
    added = {p.name: _sha1(p) for p in added_by_us(album_dir, plan)
             if p.is_file() and p.name != PLAN_FILE}
    plan.adopted["added"] = added
    save_plan(plan, album_dir)


def give_back(album_dir: Path, plan: AlbumPlan, library: Path,
              log: Callable[[str], None] = lambda s: None) -> dict[str, int]:
    """Return an adopted album to the state it was in, and remove what noaap added.

    **Judged on what matters** (R-189, ruling 4): every file answers to `adopted_name`, every field
    in `adopted_tags` has the value it had, and the audio stream is the one that was there. The tag
    *block* does not come back byte-identical — mutagen rewrites it whole, and a writer that put
    the same values back cannot put the same padding back — so byte identity is not the promise and
    saying it was would be a lie.

    A file noaap added and the user has since changed is **kept**, and this says so: it is theirs
    now, whatever put it there.
    """
    done = {"renamed": 0, "restored": 0, "removed": 0, "kept": 0}
    if not plan.adopted:
        raise ValueError(f"{plan.album}: no record of an adoption to undo")

    for track in plan.tracks:
        if not track.adopted_name:
            raise ValueError(f"{plan.album}: {track.title} has no record of the name it had")
        here = _inside(album_dir, track.filename)
        want = album_dir / track.adopted_name
        if here and here != want and here.is_file():
            here.rename(want)
            done["renamed"] += 1
        if track.adopted_tags and want.is_file() and restore_tags(want, track.adopted_tags):
            done["restored"] += 1

    for path in added_by_us(album_dir, plan):
        if not path.is_file():
            continue
        if path.name != PLAN_FILE and _sha1(path) != (plan.adopted.get("added") or {}).get(path.name):
            log(f"  kept {path.name}: it has been edited since noaap wrote it")
            done["kept"] += 1
            continue
        path.unlink()
        done["removed"] += 1

    was = library / str(plan.adopted.get("folder") or plan.folder)
    if was != album_dir and not was.exists():
        was.parent.mkdir(parents=True, exist_ok=True)
        album_dir.rename(was)
    return done
