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
from .config import Refused
from .download import COVER_STEM, PLAN_FILE, save_plan
from .lyrics import rename_sidecar, sidecar_path
from .models import AlbumPlan
from .plan import build_plan, wanted_filename
from .service import _inside
from .tag import embedded_cover, raw_tags, restore_tags, tag_file
from .text import key as text_key


# what the plan would assert about a file, and therefore what an undo has to be able to give back
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

    # **where each file is, from the provider and never off the ref** (§9, slice 61). A source that
    # cannot say has files that are not inside this folder — a disc spelled as a sibling folder is the
    # case — and such an album is refused rather than adopted with a name that points somewhere else.
    where = {entry.video_id: entry.where for entry in collection.entries}
    plan = build_plan(collection, source=source)
    plan.provider = getattr(source, "name", sources.DEFAULT)
    plan.own_the_candidates()
    plan.keep_names = plan.keep_tags = True
    plan.folder = str(album_dir.relative_to(library))
    plan.adopted = {"folder": plan.folder, "at": dt.date.today().isoformat()}

    if outside := [t.title for t in plan.tracks if not where.get(t.video_id)]:
        return Adoption(album_dir, refused=f"{len(outside)} file(s) are not inside this folder — "
                                           "adopt the folder that holds them")
    for track in plan.tracks:
        # the file is here and finished: that is what adoption means. Its name is the owner's, and so
        # is the folder it is in — both of which `filename` now carries (§9, slice 61).
        track.state = "done"
        track.filename = track.adopted_name = where[track.video_id]
        track.adopted_tags = _was(album_dir / track.filename, plan, track)
    return Adoption(album_dir, plan=plan)


def _was(audio: Path, plan: AlbumPlan, track: Any) -> dict[str, Any]:
    """What the file says today, for every field noaap would ever write into it.

    Only those fields: an undo has to put back what a retag took away, and nothing else is ours to
    restore. A key the file does not carry is recorded as `None` — *absent* is a value too, and
    without it an undo would leave behind a tag the owner never had.
    """
    return dict(raw_tags(audio))


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


# -- giving it back ------------------------------------------------------------------------------


def cover_proofs(album_dir: Path, plan: AlbumPlan) -> tuple[str, ...]:
    """The fingerprints that would prove a cover file is one noaap wrote (§9, slice 64).

    **Two of them, and either is enough** (R-222, do 2). The first is the hash the pass that saved it
    recorded. The second is the picture the album's own tracks carry, because a cover noaap saved for a
    folder album *is* those bytes — the provider hands over the embedded picture and the file is written
    unchanged — and the record of it was lost by every version up to 1.6.0: `run` saves the plan before
    it fetches the cover and only again when a track changes something, which for an adopted album is
    never. Sixteen real covers in a 41 GB library had no record at all, and all sixteen are byte for
    byte a picture inside one of their album's own files.

    A cover whose bytes are neither stays, as it always did: it is the owner's.
    """
    proofs = [plan.cover_fetched.get("sha1") or ""]
    for track in plan.tracks:
        audio = _inside_album(album_dir, track.filename)
        if audio and audio.is_file() and (picture := embedded_cover(audio)):
            proofs.append(hashlib.sha1(picture).hexdigest())
    return tuple(dict.fromkeys(p for p in proofs if p))  # one picture in twelve files is one proof


def _inside_album(album_dir: Path, filename: str) -> Path | None:
    """`album_dir / filename` when that really is inside the album — a plan can name `../..`."""
    if not filename or Path(filename).is_absolute():
        return None
    root, path = album_dir.resolve(), (album_dir / filename).resolve()
    return path if path != root and root in path.parents else None


def added_by_us(album_dir: Path, plan: AlbumPlan) -> list[tuple[Path, tuple[str, ...]]]:
    """Everything noaap put into this folder, each with the fingerprints that would prove it is ours.

    **Every one of them already records its own**, and that is the point: a snapshot taken at
    adoption is stale the moment a later pass writes a sidecar, and the first live undo kept 80 of
    them as "edited" when noaap had written every one (found 2026-09-28). A sidecar answers to
    `lyrics_sha` — *the bytes we wrote; anything else is the user's* — and a cover to
    `cover_fetched.sha1` **or to the picture inside the album's own files** (see `cover_proofs`).

    The plan carries none: it is ours whatever it says, and an undo removes it.

    Not the audio and not one file that was here before — those are the owner's, and the only thing
    that ever displaces one is the bin (§9, slice 55).
    """
    out: list[tuple[Path, tuple[str, ...]]] = [(album_dir / PLAN_FILE, ())]
    for track in plan.tracks:
        words = sidecar_path(album_dir, track.filename)
        if words.is_file():
            out.append((words, (track.lyrics_sha,) if track.lyrics_sha else ()))
    covers = sorted(p for p in album_dir.glob(f"{COVER_STEM}.*") if p.is_file())
    proofs = cover_proofs(album_dir, plan) if covers else ()
    out.extend((cover, proofs) for cover in covers)
    return out


def ours_still(path: Path, proofs: tuple[str, ...] | str | None) -> bool:
    """Is this file still one noaap wrote? **Any one of the fingerprints proves it**; none answers no.

    `lyrics_sha` is a 16-character prefix, so a proof shorter than a digest is compared as one.
    """
    if isinstance(proofs, str) or proofs is None:
        proofs = (proofs,) if proofs else ()
    if not proofs:
        return False
    digest = hashlib.sha1(path.read_bytes()).hexdigest()
    return any(digest == proof or digest[:len(proof)] == proof for proof in proofs)


def give_back(album_dir: Path, plan: AlbumPlan, library: Path,
              log: Callable[[str], None] = lambda s: None) -> dict[str, int]:
    """Return an adopted album to the state it was in, and remove what noaap added.

    **It can be run again.** A file already back under its own name is not renamed, tags already
    restored are not rewritten, and an album whose plan is gone is already done and is not visited.
    One track that cannot be given back costs that track: the rest of the album is still restored,
    the plan is kept so a later run can finish it, and the caller is told.

    **Judged on what matters** (R-189, ruling 4): every file answers to `adopted_name`, every field
    in `adopted_tags` has the value it had, and the audio stream is the one that was there. The tag
    *block* does not come back byte-identical — mutagen rewrites it whole, and a writer that put
    the same values back cannot put the same padding back — so byte identity is not the promise and
    saying it was would be a lie.

    A file noaap added and the user has since changed is **kept**, and this says so: it is theirs
    now, whatever put it there.
    """
    done = {"renamed": 0, "restored": 0, "removed": 0, "kept": 0, "failed": 0}
    if not plan.adopted:
        raise Refused(f"{plan.album}: there is no record of an adoption to undo")

    for track in plan.tracks:
        try:
            if not track.adopted_name:
                raise Refused(f"{track.title} has no record of the name it had")
            here = _inside(album_dir, track.filename)
            want = album_dir / track.adopted_name
            if here and here != want and here.is_file():
                want.parent.mkdir(parents=True, exist_ok=True)  # its disc folder, if it is in one
                here.rename(want)
                done["renamed"] += 1
            # `is not None`, not truthiness: **an empty record is a record** — it says the file
            # had no tags at all, and those are exactly the files a retag adds the most to. The
            # same mistake in a second costume (§9, slice 58).
            if track.adopted_tags is not None and want.is_file() \
                    and restore_tags(want, track.adopted_tags):
                done["restored"] += 1
        except Exception as e:  # one file's trouble is not the album's, and not the pass's
            done["failed"] += 1
            log(f"  {track.adopted_name or track.title}: {e}")

    if done["failed"]:
        # **the plan stays.** It is the only record of what these files were, and throwing it away
        # because part of the undo failed would leave the rest of the album unrecoverable. Run the
        # undo again and it finishes what is left (found 2026-09-28: a crash mid-pass left 92 of
        # 132 albums adopted and three renamed albums with no way back but this).
        save_plan(plan, album_dir)
        return done

    for path, wrote in added_by_us(album_dir, plan):
        if not path.is_file():
            continue
        if path.name != PLAN_FILE and not ours_still(path, wrote):
            log(f"  kept {path.name}: it is not the file noaap wrote")
            done["kept"] += 1
            continue
        path.unlink()
        done["removed"] += 1

    was = library / str(plan.adopted.get("folder") or plan.folder)
    if was != album_dir and not was.exists():
        was.parent.mkdir(parents=True, exist_ok=True)
        album_dir.rename(was)
    return done


# -- and the two things a person may ask for afterwards --------------------------------------------


def undo_data_complete(plan: AlbumPlan) -> str:
    """Why this album may not be renamed or retagged, or "" when it may.

    Neither act runs without a way back (R-189, ruling 5). An album with no record is one noaap
    never adopted, or one whose record something has eaten; either way the answer is no, because
    the whole promise of adoption is that it can be given back.
    """
    if not plan.adopted:
        return "no record of an adoption"
    missing = [t.title for t in plan.tracks if not t.adopted_name or t.adopted_tags is None]
    if missing:
        return f"{len(missing)} track(s) have no record of what they were"
    return ""


def rename(album_dir: Path, plan: AlbumPlan, log: Callable[[str], None] = lambda s: None) -> int:
    """Give the album noaap's own file names. The folder stays where its owner put it.

    Moving the folder as well would be a second decision, and a collection laid out by hand is
    laid out that way on purpose — so this renames files and nothing else.
    """
    if why := undo_data_complete(plan):
        raise Refused(f"{plan.album}: {why}, so it cannot be renamed")
    moved = 0
    for track in plan.tracks:
        here = _inside(album_dir, track.filename)
        # **renamed where it stands** (§9, slice 61): a track in a disc sub-folder keeps that folder,
        # because the layout is the owner's as much as the names were, and this pass was asked for
        # the names. Nothing here moves a file between folders.
        want = str(Path(track.filename).parent / wanted_filename(plan, track))
        if here is None or not here.is_file() or track.filename == want:
            continue
        if (album_dir / want).exists():
            log(f"  not renaming {track.filename}: {want} is already there")
            continue
        here.rename(album_dir / want)
        rename_sidecar(album_dir, track.filename, want)
        track.filename = want
        moved += 1
    if moved:
        plan.keep_names = False  # from here on the names are noaap's, and passes may keep them so
    return moved


def retag(album_dir: Path, plan: AlbumPlan, cover: bytes | None = None,
          log: Callable[[str], None] = lambda s: None) -> int:
    """Write the plan's tags into the files, keeping everything this program does not model.

    `keep_unknown=True` is the whole difference from an ordinary retag: a collection somebody has
    been tagging for years holds replaygain, ISRC, composer, BPM and their own comment, and losing
    those would be the adoption destroying the thing it took in (§9, slice 53).
    """
    if why := undo_data_complete(plan):
        raise Refused(f"{plan.album}: {why}, so it cannot be retagged")
    written = 0
    for track in plan.tracks:
        audio = _inside(album_dir, track.filename)
        if audio is None or not audio.is_file():
            continue
        try:
            track.tagged = tag_file(audio, plan, track, cover, None, keep_unknown=True)
        except Exception as e:  # one unreadable file is not the album's problem
            log(f"  {track.filename}: {e}")
            continue
        written += 1
    if written:
        plan.keep_tags = False  # the tags are noaap's now; the undo still knows what they were
    return written
