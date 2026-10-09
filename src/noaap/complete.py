"""Completing an album from its pinned release (DESIGN §9, slice 162; P118).

An album the user has pinned to a MusicBrainz release can say which of that release's tracks it
does not hold. Each missing **audio** track is looked for at a provider that can find single
recordings, by artist and title, and the closest hit is offered. Nothing here writes: the service
fetches what the user chose, into the slot the release gives it.

What it never does: use a release nobody pinned, count a title spelled differently as missing,
search for a video, or touch a file the album already holds.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .enrich import _GROUP, core, credit_phrase, near_enough, version_markers
from .models import AlbumPlan
from .text import key

# a medium that holds pictures, not sound: its tracks are reported, never searched (R-562, 2)
VIDEO_FORMATS = ("dvd", "blu-ray", "vhs", "video", "vcd", "umd")
# a hit that is another version of the song, unless the release's own title says so too
OTHER_VERSIONS = ("live", "unplugged", "acoustic", "remix", "cover", "karaoke", "instrumental",
                  "nightcore", "sped up", "slowed", "8d", "reaction", "tutorial", "piano")
CLOSE_LENGTH = 10.0     # seconds: as close as two masterings of one recording are
FAR_LENGTH = 30.0       # beyond this a hit is another recording, whatever it is called


@dataclass
class Slot:
    """One track of the release, as the album sees it."""

    disc: int
    number: int
    title: str
    artist: str
    recording: str | None = None
    length: float | None = None
    video: bool = False
    hits: list[dict[str, Any]] = field(default_factory=list)

    @property
    def name(self) -> str:
        """`1-06`: what the dry run shows and `--only` takes."""
        return f"{self.disc}-{self.number:02d}"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "name": self.name}


def comparable(title: str) -> str:
    return key(core(title or ""))


# what a bracket says beyond `version_markers`: German spellings and the words a sleeve uses for
# another take of the same song (`Schau in Mein Gesicht (Akkustik Version)` on `Heilig`)
OTHER_TAKES = re.compile(r"\b(version|mix|edit|akustik|akkustik|orchester|live)\b", re.I)


def versions(title: str) -> set[str]:
    return version_markers(title) | {m.lower() for g in _GROUP.findall(title or "") for m in OTHER_TAKES.findall(g)}


def same_song(ours: str, theirs: str) -> bool:
    """Two titles name one song: equal or a typo apart once brackets are gone — or one is the other
    with a prefix a release adds (`All the Works of Nature… - Vista` is `Vista`). **Not** when their
    brackets name different takes: the acoustic version of a song is a track of its own."""
    if versions(ours) != versions(theirs):
        return False
    a, b = comparable(ours), comparable(theirs)
    if a == b or near_enough(a, b):
        return True
    for long, short in ((theirs, ours), (ours, theirs)):
        tail = re.split(r"\s[–—-]\s", long or "")[-1]
        if tail != long and comparable(tail) and comparable(tail) == comparable(short):
            return True
    return False


def slots_of(release: dict[str, Any], albumartist: str) -> list[Slot]:
    out = []
    for n, medium in enumerate(release.get("media") or [], 1):
        video = any(v in str(medium.get("format") or "").casefold() for v in VIDEO_FORMATS)
        for t in medium.get("tracks") or []:
            rec = t.get("recording") or {}
            length = t.get("length") or rec.get("length")
            out.append(Slot(disc=int(medium.get("position") or n), number=int(t.get("position") or 0),
                            title=t.get("title") or rec.get("title") or "",
                            artist=credit_phrase(t.get("artist-credit") or rec.get("artist-credit") or []) or albumartist,
                            recording=rec.get("id"), length=length / 1000 if length else None,
                            video=video or bool(rec.get("video"))))
    return out


def gaps(plan: AlbumPlan, release: dict[str, Any]) -> tuple[list[Slot], list[str]]:
    """The release's tracks this album holds no file for, and the album's files the release does
    not list (another edition's tracks: reported, never acted on).

    A file answers for a slot by its recording id first, then by its title; each file answers once.
    """
    slots = slots_of(release, plan.albumartist)
    free = list(range(len(plan.tracks)))
    missing = []
    for slot in slots:
        found = next((i for i in free if slot.recording and plan.tracks[i].mbid == slot.recording), None)
        if found is None:
            found = next((i for i in free if same_song(plan.tracks[i].title, slot.title)), None)
        if found is None:
            missing.append(slot)
        else:
            free.remove(found)
    return missing, [plan.tracks[i].title for i in free]


def _cleaned(hit_title: str, artist: str, album: str) -> str:
    """A video's title with what is not the song's name taken out: the artist, the album, a track
    number, and the separators between them (`Letzte Instanz   Heilig   06   Fur Dich`)."""
    text = hit_title or ""
    for name in (artist, album):
        if name:
            text = re.sub(re.escape(name), " ", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<!\w)\d{1,2}(?!\w)", " ", text)
    return re.sub(r"^[\s|:–—-]+|[\s|:–—-]+$", "", re.sub(r"\s+", " ", text))


def score(slot: Slot, hit: dict[str, Any], album: str) -> float | None:
    """How well a hit answers for a slot, or None where it is not this recording at all."""
    title = hit.get("title") or ""
    low, want = title.casefold(), slot.title.casefold()
    if any(re.search(rf"\b{re.escape(w)}\b", low) and not re.search(rf"\b{re.escape(w)}\b", want)
           for w in OTHER_VERSIONS):
        return None
    cleaned = _cleaned(title, slot.artist, album)
    if comparable(cleaned) == comparable(slot.title):
        points = 3.0
    elif same_song(cleaned, slot.title):
        points = 2.0
    else:
        return None
    if slot.length and hit.get("length"):
        off = abs(float(hit["length"]) - slot.length)
        if off > FAR_LENGTH:
            return None
        points += 2.0 if off <= CLOSE_LENGTH else 0.0
    channel = key(str(hit.get("channel") or "").removesuffix(" - Topic"))
    if channel and channel == key(slot.artist):
        points += 1.0
    return points


def rank(slot: Slot, hits: list[dict[str, Any]], album: str) -> list[dict[str, Any]]:
    """The hits that could be this slot's recording, best first, each with its score and how far
    its length is from the release's."""
    out = []
    for hit in hits:
        if (s := score(slot, hit, album)) is None:
            continue
        off = abs(float(hit["length"]) - slot.length) if slot.length and hit.get("length") else None
        out.append({**hit, "score": s, "off": round(off, 1) if off is not None else None})
    return sorted(out, key=lambda h: (-h["score"], h["off"] if h["off"] is not None else 999))


def queries(slot: Slot, album: str) -> list[str]:
    return [f"{slot.artist} - {slot.title}", f"{slot.artist} {slot.title} {album}"]


# the start of the refusal when `--only` names a track the release does not have (R-564)
UNKNOWN_SLOT = "the release has no track"


def parse_only(text: str | None) -> set[str] | None:
    """`1-06,1-13` → {"1-06", "1-13"}; a bare `6` means disc 1. Anything else is a ValueError
    that says which part it could not read."""
    if not text:
        return None
    out = set()
    for part in re.split(r"[,\s]+", text.strip()):
        if not part:
            continue
        if not (m := re.fullmatch(r"(?:(\d{1,2})-)?(\d{1,3})", part)):
            raise ValueError(f"“{part}” is not a track — write it as the check names it, e.g. 1-06")
        out.add(f"{int(m[1] or 1)}-{int(m[2]):02d}")
    return out
