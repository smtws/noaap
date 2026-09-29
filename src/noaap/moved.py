"""Finding a track's file again by what it holds (DESIGN §9, slice 66).

`find_again` (slice 61) asks the provider where a file is, which answers the one case where the plan
recorded a name without its folder. This answers the other cases: a file **renamed** by its owner, one
**moved** into a disc folder or another album, or a whole **intake folder** that moved. For those the
name is gone and only the audio is left to go on.

**It never guesses.** An identity that matches exactly one unclaimed file re-attaches that track; an
identity that matches two, or none, is *named and left*. And it never writes into an audio file and
never moves one (R-227, ruling 2): what changes is what the plan says.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from . import identity
from .models import AlbumPlan, PlanTrack
from .sources_folder import AUDIO


@dataclass(frozen=True)
class Found:
    """One track whose file was somewhere else, and where."""

    track: str          # the track's ref, which is how a plan names one
    title: str
    was: str            # what the plan said, relative to the album
    now: str            # where the file is, in the same terms


@dataclass(frozen=True)
class Undecided:
    """One track that was not re-attached, and the reason in the owner's terms."""

    track: str
    title: str
    was: str
    why: str


@dataclass
class Outcome:
    found: list[Found] = field(default_factory=list)
    left: list[Undecided] = field(default_factory=list)
    decoded: int = 0     # files decoded in this album, which is what the pass costs


def audio_under(root: Path, skip: Iterable[Path] = ()) -> list[Path]:
    """Every audio file under `root`, ours and hidden folders left out."""
    away = {p.resolve() for p in skip}
    return sorted(p for p in root.rglob("*")
                  if p.is_file() and p.suffix.lower() in AUDIO
                  and p.resolve() not in away
                  and not any(part.startswith(".") for part in p.relative_to(root).parts))


def claimed(plan: AlbumPlan, album_dir: Path) -> set[Path]:
    """The files this plan's other tracks already answer for. One file is one track's."""
    out = set()
    for track in plan.tracks:
        if track.filename and (path := album_dir / track.filename).is_file():
            out.add(path.resolve())
    return out


def look(album_dir: Path, plan: AlbumPlan, library: Path | None = None,
         lost: Iterable[PlanTrack] = (),
         also: Callable[[], list[Path]] | None = None) -> Outcome:
    """Where each lost track's file is now, when that can be said.

    Looks in the album first — a rename or a move into a disc folder is much the commonest case and
    costs one album's files — then, only for what is still missing, wherever `also` says to look.
    """
    out = Outcome()
    taken = claimed(plan, album_dir)
    here = audio_under(album_dir, skip=taken)
    wider: list[Path] | None = None

    for track in lost:
        want = identity.identity_of(track, album_dir / track.filename)
        cheap = next((c.stream_sha for c in track.candidates
                      if c.ref == track.effective_id and c.stream_sha), None)
        if want is None and cheap is None:
            out.left.append(Undecided(track.video_id, track.title, track.filename,
                                      "nothing was ever measured about this track's file, and its own "
                                      "file is gone, so there is nothing to look for"))
            continue
        length = track.file_length or track.duration
        hits, decoded = identity.matches(want, length, here, cheap=cheap)
        out.decoded += decoded
        if not hits and also is not None:
            if wider is None:
                wider = [p for p in also() if p.resolve() not in taken]
            hits, decoded = identity.matches(want, length, wider, cheap=cheap)
            out.decoded += decoded
        if len(hits) == 1:
            where = hits[0]
            inside = where.resolve().is_relative_to(album_dir.resolve())
            root = album_dir if inside else (library or album_dir)
            out.found.append(Found(track.video_id, track.title, track.filename,
                                   str(where.resolve().relative_to(root.resolve()))))
            taken.add(where.resolve())
            here = [p for p in here if p.resolve() != where.resolve()]
            continue
        out.left.append(Undecided(
            track.video_id, track.title, track.filename,
            "nothing in the library holds this recording" if not hits
            else f"{len(hits)} files hold this recording and nothing says which is this track's"))
    return out


def re_attach(plan: AlbumPlan, album_dir: Path, found: Iterable[Found]) -> tuple[int, int]:
    """Say where the files are, and measure what was attached. **Nothing on disk is touched.**

    An album that keeps its own names keeps the name the file has now — that is what the owner called
    it — so `adopted_name` follows `filename` where it held the old one (R-227, ruling 2).

    **And the file that was just found gets its identity written down**, one decode each. A rename is
    answered by the packet digest for nothing, but only until somebody re-tags the file; measuring it now
    is what makes the *next* question answerable. Returns (tracks, files decoded).
    """
    by_ref = {track.video_id: track for track in plan.tracks}
    done = decoded = 0
    for one in found:
        track = by_ref.get(one.track)
        if track is None:
            continue
        if track.adopted_name == track.filename:
            track.adopted_name = one.now
        track.filename = one.now
        track.state = "done"
        done += 1
        here = album_dir / one.now
        if here.is_file() and (mine := identity.of(here)) is not None:
            decoded += 1
            for copy in track.candidates:
                if copy.ref == track.effective_id:
                    identity.remember(copy, mine)
    return done, decoded





# -- and the whole folder an album was taken in from (§9, slice 66) ----------------------------------
#
# An album adopted from an intake folder keeps its own files in the library and its *refs* over there.
# When that folder moves, nothing about the album is broken until somebody asks for its source again —
# and then every ref names a path that is gone. The album's own files are still here, which is what
# makes this answerable: their identities are measurable, and the same recordings under the new folder
# say where it went. **The provider mints the new refs**; the core only says which file is which.


@dataclass(frozen=True)
class Resourced:
    """One album whose source folder was found again somewhere else."""

    was: str                     # the source the plan named
    now: str                     # the folder it is now, as the provider addresses it
    refs: dict[str, str]         # old ref -> new ref, one per track
    decoded: int


def source_of(plan: AlbumPlan) -> str:
    return plan.source_id or plan.source_url


def look_for_source(album_dir: Path, plan: AlbumPlan, root: Path,
                    read: Callable[[Path], object]) -> tuple[Resourced | None, str]:
    """Where this album's source folder went, or why that cannot be said.

    Every track is matched by what its **own file in the library** holds, so this works for an album
    whose source has been gone for months. All of them must be found, in one folder: a provider names a
    collection by a folder, and half an album is not one.
    """
    from .plan import build_plan

    candidates = audio_under(root)
    if not candidates:
        return None, f"no audio under {root.name}"
    taken: set[Path] = set()
    where: dict[str, Path] = {}
    decoded = 0
    for track in plan.tracks:
        if not track.filename:
            continue
        mine = album_dir / track.filename
        want = identity.identity_of(track, mine)
        cheap = next((c.stream_sha for c in track.candidates
                      if c.ref == track.effective_id and c.stream_sha), None)
        if want is None and cheap is None:
            return None, f"“{track.title}” has nothing measured about it to look for"
        hits, cost = identity.matches(want, track.file_length or track.duration,
                                      [p for p in candidates if p not in taken], cheap=cheap)
        decoded += cost
        if len(hits) != 1:
            return None, (f"nothing under {root.name} holds “{track.title}”" if not hits
                          else f"{len(hits)} files under {root.name} hold “{track.title}”")
        where[track.video_id] = hits[0]
        taken.add(hits[0])
    if not where:
        return None, "this album has no track with a file"
    folders = {p.parent for p in where.values()}
    if len(folders) != 1:
        return None, f"its files are spread over {len(folders)} folders under {root.name}"

    folder = folders.pop()
    collection = read(folder)
    fresh = build_plan(collection, source=None)
    by_file = {}
    for entry in collection.entries:            # type: ignore[attr-defined]
        if entry.where:
            by_file[(folder / entry.where).resolve()] = entry.video_id
    refs = {}
    for old, path in where.items():
        if (new := by_file.get(path.resolve())) is None:
            return None, f"the provider does not name {path.name} in that folder"
        refs[old] = new
    return Resourced(source_of(plan), fresh.source_id or str(folder), refs, decoded), ""


def re_source(plan: AlbumPlan, found: Resourced, address: str) -> int:
    """Point the album at the folder it is in now. Its own files are not touched.

    The refs are the provider's, read from its own listing of that folder; `address` is what the plan
    now calls its source, which is the same thing the provider was asked about.
    """
    changed = 0
    for track in plan.tracks:
        if (new := found.refs.get(track.video_id)) is None:
            continue
        old = track.video_id
        for copy in track.candidates:
            if copy.ref == old:
                copy.ref = new          # the same file's measurements, under the name its owner minted
        if track.source_override == old:
            track.source_override = new
        track.video_id = new
        track.sync_candidates()
        changed += 1
    if plan.source_url == source_of(plan):
        plan.source_url = address
    if plan.cover_url == source_of(plan):
        plan.cover_url = address
    plan.source_id = address
    return changed


__all__ = ["Found", "Outcome", "Resourced", "Undecided", "audio_under", "claimed", "look",
           "look_for_source", "re_attach", "re_source", "source_of"]
