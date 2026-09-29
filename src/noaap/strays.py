"""Removing what 1.4.0 and 1.5.0 wrongly wrote into an adopted album (DESIGN §9, slice 63).

Those versions recorded a track's file by its bare name, so for an album whose discs are sub-folders
`album_dir / filename` was not the file; the executor read that as a missing file and fetched it
again, which for a folder provider is a **copy** into the album's root under noaap's own naming
scheme. Slice 61 stopped it happening. This is the other half: the copies are still there, in
somebody's music folder, and noaap put them there.

**Nothing here is deleted and nothing is guessed.** A file is removed only when all four of these
hold, and a file that fails any one of them is reported and left exactly where it is:

1. it lies in the **root** of an adopted album whose discs are **sub-folders**;
2. its name is one **noaap's own scheme** would have written;
3. its **decoded audio** is identical to that of a file in one of those disc folders;
4. that disc file is one **the plan holds** — which the provider says, because a ref is opaque.

The third is the expensive one and the only one that can prove the file is a copy rather than
something of the owner's that merely looks like one. It is guarded by a length read first: a copy has
its original's duration, so only files that agree to a tenth of a second are ever decoded.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import AlbumPlan, PlanTrack
from .plan import wanted_filename
from .sources_folder import AUDIO
from .tag import audio_length, decoded_sha

CLOSE = 0.1  # seconds two files' durations may differ by and still be worth decoding

# what the provider does for this pass: read one folder and say what is in it
Reader = Callable[[Path], Any]


@dataclass(frozen=True)
class Stray:
    """One file noaap wrote that the owner never had, with the evidence for saying so."""

    where: str            # its path, relative to the album folder
    copy_of: str          # the file in a disc folder whose audio it holds, same terms
    digest: str           # the decoded audio both of them have
    length: float | None  # and how long that is
    named_by: str = "the plan"  # "the plan" or "noaap's scheme": which said this name could be ours
    tracks: list[str] = field(default_factory=list)  # the plan's tracks that pointed at it

    def evidence(self) -> dict[str, Any]:
        return {"copy_of": self.copy_of, "decoded_sha256": self.digest, "length": self.length,
                "named_by": self.named_by,
                "why": "noaap 1.4.0/1.5.0 copied this file here from the album's own disc folder "
                       "because the plan named it without that folder (§9, slice 61)"}


@dataclass(frozen=True)
class Left:
    """A file in the album's root that is **not** provably ours, and why it stays."""

    where: str
    why: str


def disc_files(album_dir: Path) -> list[Path]:
    """Audio in a sub-folder of the album — the owner's layout, which noaap never writes into."""
    return sorted(p for p in album_dir.rglob("*")
                  if p.is_file() and p.suffix.lower() in AUDIO and p.parent != album_dir
                  and not any(part.startswith(".") for part in p.relative_to(album_dir).parts))


def root_files(album_dir: Path) -> list[Path]:
    return sorted(p for p in album_dir.iterdir() if p.is_file() and p.suffix.lower() in AUDIO)


def our_names(plan: AlbumPlan) -> set[str]:
    """Every name noaap's own scheme would give a track of this album, in both its forms.

    The disc number is in the name when the album has more than one disc, and a plan damaged by 1.5.0
    can hold either form for the same track — so both count.
    """
    return {wanted_filename(plan, track, disc)
            for track in plan.tracks for disc in (None, track.disc)}


def named_by(plan: AlbumPlan) -> set[str]:
    """Bare names this plan itself gives a track's file — **the name a copy actually got.**

    R-220 put the second condition as "its name follows noaap's scheme", and on this collection that is
    *nearly* true, because ytalbum named most of it. It is not the mechanism. 1.5.0 copied the file to
    `album_dir / filename`, and its `filename` was **the owner's own bare name**; where the two agree,
    they agree by history. Three real files prove the difference: their ID3 title is clipped at 30
    characters (`Der Rattenfänger (Grave Digger`), so the owner's name carries a closing bracket the
    scheme would never derive, and all three would have been left behind in somebody's album root.
    So a name the plan itself points at counts too — and it is the stronger evidence of the two, since
    a file the owner happens to keep in the root is not one the plan names.
    """
    out = {t.filename for t in plan.tracks} | {t.adopted_name or "" for t in plan.tracks}
    return {name for name in out if name and "/" not in name}


def refs_of_discs(album_dir: Path, read: Reader) -> dict[str, str]:
    """Each file in a disc sub-folder → the ref the provider mints for it.

    **Asked of the provider, one disc folder at a time**, because a ref is opaque and the core may
    never turn one into a path (§9, slice 51). Reading the *album* does not answer this: once the
    copies are in its root the folder is no longer a disc split, so the provider reads it flat, groups
    each copy with the file it was copied from and names the group by whichever it ranks best — which
    for the real damage was the copy. Asking each disc folder for itself is the only reading in which
    every one of the owner's files is named.
    """
    out: dict[str, str] = {}
    for sub in sorted({p.parent for p in disc_files(album_dir)}):
        try:
            collection = read(sub)
        except Exception:  # a folder that cannot be read says nothing, and nothing is then provable
            continue
        for entry in collection.entries:
            if entry.where:
                out[str((sub / entry.where).relative_to(album_dir))] = entry.video_id
    return out


def held_by(plan: AlbumPlan, refs: dict[str, str]) -> dict[str, PlanTrack]:
    """The album-relative file of each track the plan holds → that track, by comparing refs only."""
    mine = {ref: track for track in plan.tracks
            for ref in (track.video_id, *(c.ref for c in track.candidates))}
    return {where: mine[ref] for where, ref in refs.items() if ref in mine}


def look(album_dir: Path, plan: AlbumPlan, read: Reader) -> tuple[list[Stray], list[Left]]:
    """What in this album's root is provably a copy noaap made, and what is not."""
    if not plan.keep_names:
        return [], []
    discs, roots = disc_files(album_dir), root_files(album_dir)
    if not discs or not roots:
        return [], []

    held = held_by(plan, refs_of_discs(album_dir, read))
    ours, named = our_names(plan), named_by(plan)
    lengths = {p: audio_length(p) for p in discs}
    digests: dict[Path, str | None] = {}

    def digest(path: Path) -> str | None:
        if path not in digests:
            digests[path] = decoded_sha(path)
        return digests[path]

    strays, left = [], []
    for path in roots:
        here = str(path.relative_to(album_dir))
        if path.name not in ours and path.name not in named:
            left.append(Left(here, "its name is neither one noaap would have written nor one this "
                                   "plan points at"))
            continue
        mine, length = digest(path), audio_length(path)
        if mine is None:
            left.append(Left(here, "its audio could not be decoded, so nothing is provable"))
            continue
        # the guard before the expensive test: a copy has its original's duration
        near = [d for d in discs if length is None or lengths[d] is None
                or abs(lengths[d] - length) <= CLOSE]
        twins = [d for d in near if digest(d) == mine]
        if not twins:
            left.append(Left(here, "no file in a disc folder has this audio"))
            continue
        # **which one it is a copy of has to be answerable.** An album can hold the same recording on
        # two discs — a live disc and a studio one, a bonus track repeated — and then the audio alone
        # does not say. The name does, because that is the mechanism: the copy took the name the plan
        # held. Without either, nothing is provable and the file stays.
        twin = next((d for d in twins if d.name == path.name), twins[0] if len(twins) == 1 else None)
        if twin is None:
            left.append(Left(here, f"{len(twins)} files in the disc folders have this audio and its "
                                   "name does not say which of them it is a copy of"))
            continue
        twin_where = str(twin.relative_to(album_dir))
        if twin_where not in held:
            left.append(Left(here, f"its copy “{twin_where}” is not a file this plan holds"))
            continue
        strays.append(Stray(where=here, copy_of=twin_where, digest=mine, length=length,
                            named_by="the plan" if path.name in named else "noaap's scheme",
                            tracks=[t.video_id for t in plan.tracks if t.filename == here]))
    return strays, left


def only_a_copy(plan: AlbumPlan, stray: Stray, held: dict[str, PlanTrack]) -> list[PlanTrack]:
    """The tracks of this plan that exist **only** because of this stray, and can go with it.

    A track whose own file is the stray and which has no record of ever having been the owner's —
    `adopted_name` unset — is one `update` appended when it read the copy as a new arrival. The
    owner's own track keeps its place and is pointed back at its file by `repair`'s own pass.
    """
    owner = held.get(stray.copy_of)
    return [t for t in plan.tracks
            if t.filename == stray.where and t is not owner and not t.adopted_name]
