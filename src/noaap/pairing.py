"""Which track of one library is which track of another (DESIGN §9, slice 54).

Before anything can be compared it has to be paired, and the pairing is where a merge can do its
worst damage: one wrong pair replaces a recording with a different song. So the keys are few, they
are tried strongest first, and **a key that matches more than one track is not a pair** — it is
shown to a person, never guessed.

Measured on the reference material — 2000 intake tracks against 3942 in the library — the keys
produce 770 pairs (482 by recording id, 288 by artist and title), 192 ambiguous and 1038 tracks
that the other library simply does not have. Title alone was tried and dropped: it added 24 pairs
and 196 ambiguities, which is the wrong trade for a rule that replaces files.

**An audio digest cannot pair across libraries** and is not used here: two encodes of one recording
are different bytes, and 0 of the 770 pairs share one. It stays what P51 made it — an identity
*within* a source, for telling one file from the same file under another name.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .models import AlbumPlan, PlanTrack
from .text import key as text_key


class How(StrEnum):
    """Why two tracks are believed to be the same song. Strongest first."""

    RECORDING = "recording id"   # both matched the same MusicBrainz recording
    NAME = "artist and title"    # …or nothing did, and the names agree


@dataclass
class Side:
    """One track, and where it lives."""

    plan: AlbumPlan
    track: PlanTrack
    album_dir: Path

    @property
    def file(self) -> Path | None:
        return self.album_dir / self.track.filename if self.track.filename else None

    @property
    def present(self) -> bool:
        """Is the audio actually here? A plan can name a track that was never downloaded."""
        found = self.file
        return bool(found and found.is_file())


@dataclass
class Pair:
    new: Side
    old: Side
    how: How


@dataclass
class Pairing:
    """Everything the matcher concluded, including what it refused to conclude."""

    pairs: list[Pair] = field(default_factory=list)
    ambiguous: list[tuple[Side, list[Side]]] = field(default_factory=list)
    unpaired: list[Side] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        by_how = {how.value: sum(1 for p in self.pairs if p.how is how) for how in How}
        return {"pairs": len(self.pairs), **by_how,
                "ambiguous": len(self.ambiguous), "unpaired": len(self.unpaired)}


def sides(library: Iterable[tuple[Path, AlbumPlan]]) -> Iterator[Side]:
    for album_dir, plan in library:
        for track in plan.tracks:
            yield Side(plan, track, album_dir)


def _keys(side: Side) -> list[tuple]:
    """The keys this track can be found by, strongest first."""
    out: list[tuple] = []
    if side.track.mbid:
        out.append((How.RECORDING, side.track.mbid))
    artist, title = text_key(side.track.artist), text_key(side.track.title)
    if artist and title:
        out.append((How.NAME, artist, title))
    return out


def pair(incoming: Iterable[Side], existing: Iterable[Side]) -> Pairing:
    """Pair each incoming track with at most one track of the other library.

    A track is paired by its strongest available key. If that key names several tracks over there
    the pairing stops for this track — it does **not** fall through to a weaker key, because a
    weaker key cannot resolve what a stronger one found ambiguous, and picking one of several is
    the single most damaging thing a merge can do.
    """
    index: dict[tuple, list[Side]] = {}
    for side in existing:
        for k in _keys(side):
            index.setdefault(k, []).append(side)

    found = Pairing()
    for side in incoming:
        for k in _keys(side):
            hits = index.get(k)
            if not hits:
                continue
            if len(hits) > 1:
                found.ambiguous.append((side, hits))
            else:
                found.pairs.append(Pair(new=side, old=hits[0], how=k[0]))
            break
        else:
            found.unpaired.append(side)
    return found
