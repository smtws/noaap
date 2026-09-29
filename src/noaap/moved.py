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


__all__ = ["Found", "Outcome", "Undecided", "audio_under", "claimed", "look", "re_attach"]
