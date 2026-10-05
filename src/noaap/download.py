"""Stages 6-7: execute an AlbumPlan. The plan file doubles as the progress manifest.

Running a plan is idempotent: finished tracks are only renamed/retagged when the plan
changed, missing ones are downloaded, and the plan is saved after every track.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from mutagen import MutagenError

from . import captions
from .cover import square_if_padded
from .lyrics import (
    LyricsAPI,
    read_sidecar,
    reconcile,
    rename_sidecar,
    status_of,
    update_track,
    write_sidecar,
)
from .models import AlbumPlan, Candidate, Failure, PlanTrack, Provenance
from .plan import clashing_names, refresh_derived, wanted_filename, wanted_folder
from .precautions import Unsafe, empty_under, put_aside, safely
from .sources import Blocked, NoAudio, Source, SourceError
from .sources_folder import is_hidden_name
from .tag import (
    audio_quality,
    build_tags,
    comments_in,
    differences,
    embedded_cover,
    ends_with_an_id3v1_tail,
    image_mime,
    kept_from_the_file,
    measure,
    only_padding,
    signature,
    tag_file,
    tags_in,
    wm_frames_in,
    would_write,
)
from .treatment import Treatment, drops_comments, drops_wm_frames, renames, retags
from .trim import apply as apply_trim
from .trim import original_path, starts_before_zero
from .trim import signature as trim_signature

log = logging.getLogger(__name__)

PLAN_FILE = ".noaap.json"
# what the plan was called while this program was ytalbum. **Nothing reads it** (R-419): it is here
# so `noaap migrate` can find one and rename it, and so a pass can say that a folder holds one.
OLD_PLAN_FILE = ".ytalbum.json"
PARTS_DIR = ".parts"
COVER_STEM = "cover"
#: the index a staged pass keeps beside its staging folder. Named here rather than imported from
#: `staged`, which imports this module (§9, slice 125).
STAGED_INDEX = "noaap-staged.json"
ATTEMPTS = 2  # YouTube sporadically answers 403 for a stream URL; a fresh extraction usually works
RETRY_DELAY = 5


# -- plan files ------------------------------------------------------------------------


# A path inside the album's own folder is written as `./name`, and read back as the folder it is
# in (§9, slice 60). **One marker and one rule**: `./` cannot be a YouTube id, a SoundCloud id, a
# URL or a bare file name, so nothing has to guess which strings are paths. A path that is not
# inside this album — an intake folder somewhere else — stays exactly as it is, because it is not
# this library's to rewrite.
#
# In memory a plan holds what it always held, so no reader changes. This is a property of the file.
HERE = "./"


def portable(value: Any, album_dir: Path) -> Any:
    """What is written: a path inside this album becomes `./…`, everything else is untouched.

    **Keys as well as values**, because a plan holds maps keyed by a ref — `copies_extra` is one — and
    a ref can be a path. Missing that left an undecided copy's own fields keyed by a path the load had
    already turned back into an absolute one, so they were dropped on the next read (§9, slice 62).
    """
    if isinstance(value, dict):
        return {portable(k, album_dir): portable(v, album_dir) for k, v in value.items()}
    if isinstance(value, list):
        return [portable(v, album_dir) for v in value]
    if not isinstance(value, str) or not value.startswith("/"):
        return value
    where = Path(value)
    if where == album_dir:
        return HERE
    return f"{HERE}{where.relative_to(album_dir)}" if album_dir in where.parents else value


def resolved(value: Any, album_dir: Path) -> Any:
    """What is read: `./…` becomes the file it names in this folder — keys too, as above."""
    if isinstance(value, dict):
        return {resolved(k, album_dir): resolved(v, album_dir) for k, v in value.items()}
    if isinstance(value, list):
        return [resolved(v, album_dir) for v in value]
    if not isinstance(value, str) or not value.startswith(HERE):
        return value
    rest = value[len(HERE):]
    return str(album_dir / rest) if rest else str(album_dir)


def read_plan(path: Path, album_dir: Path) -> AlbumPlan:
    """**The one place that decides what a load reads** (§9, slice 62).

    There were two — this and `iter_plans` — and the day a save started writing a candidate's newer
    fields beside it, the page that counts undecided copies read 0 where there were 2, because only one
    of the two put them back. The same lesson as `written`, learned the same way: a second copy of the
    rule is a second thing to get wrong.
    """
    return AlbumPlan.from_dict(widen_candidates(resolved(json.loads(path.read_text()), album_dir)))


def load_plan(album_dir: Path) -> AlbumPlan | None:
    path = album_dir / PLAN_FILE
    if not path.exists():
        return None
    return read_plan(path, album_dir)


# The ten fields **ytalbum 0.9.1** knows a candidate by. They stay inside the candidate; everything
# noaap has added since is written beside it, at track level, keyed by the candidate's ref — where an
# older reader carries it through untouched instead of refusing the plan (§9, slice 62).
#
# This list is 0.9.1's `Candidate` dataclass, field for field. It is a constant and not a computation
# because it describes **another program's** class, which no longer changes; the fields to move out are
# derived from it, so a field added to `Candidate` tomorrow goes to the compatible place on its own.
CANDIDATE_1_0 = ("ref", "provider", "length", "codec", "bitrate", "sample_rate", "channels",
                 "added_by", "why", "when")
COPIES_EXTRA = "copies_extra"


def _default_candidate() -> dict[str, Any]:
    from .models import Candidate

    return Candidate(ref="").to_dict()


def narrow_candidates(data: dict[str, Any]) -> dict[str, Any]:
    """What a save writes: each candidate in the shape 0.9.1 knows, the rest beside it (§9, slice 62).

    Only a value that is not the default is written out, so an album from before any of these fields
    existed keeps the file it had, and `copies_extra` appears on a track only when it says something.

    **The provider goes beside it too**, though 0.9.1 knows that field: it is the one field an older
    reader is known to *rewrite*. On load 0.9.1 claims every candidate named by `video_id` or
    `source_override` for the album's own provider, so a save by it turned a folder copy inside a
    YouTube album into a YouTube one — 29 candidates in 15 albums of the real library — after which a
    re-fetch would ask YouTube for a path (R-215). Only a provider that **differs from the album's** is
    recorded, which is exactly the case that claiming would destroy: where they agree, claiming changes
    nothing, and a candidate synthesised from `video_id` agrees by definition once it has been loaded.
    """
    default = _default_candidate()
    album = data.get("provider") or "youtube"
    for track in data.get("tracks") or []:
        copies = track.get("candidates") or []
        here = {copy.get("ref", "") for copy in copies}
        # **what is already beside the track is kept** — for the copies that are still there. Starting
        # from nothing made this destructive when it was handed a file that had already been written
        # this way: the second pass found no fields inside the candidates and removed the record of
        # them. A rewrite of what is already right must be a no-op (§9, slice 62).
        extra = {ref: dict(kept) for ref, kept in (track.get(COPIES_EXTRA) or {}).items()
                 if ref in here}
        for copy in copies:
            ref = copy.get("ref", "")
            mine = {k: copy.pop(k) for k in list(copy) if k not in CANDIDATE_1_0}
            mine = {k: v for k, v in mine.items() if v != default.get(k)}
            kept = extra.get(ref, {})
            kept.pop("provider", None)  # never a stale one: it is decided here, every save
            if (whose := copy.get("provider")) and whose != album:
                mine["provider"] = whose
            if mine or kept:  # memory is the newer truth where both say something
                extra[ref] = {**kept, **mine}
            elif ref in extra:
                del extra[ref]
        if extra:
            track[COPIES_EXTRA] = extra
        else:
            track.pop(COPIES_EXTRA, None)
    return data


def widen_candidates(data: dict[str, Any]) -> dict[str, Any]:
    """What a load reads: the fields beside a candidate put back on it, so **memory never changes**.

    A ref `copies_extra` no longer names is dropped — 0.9.1 carries the key through whole, including
    an entry for a copy it removed, and the next save writes only what is still there.
    """
    for track in data.get("tracks") or []:
        extra = track.pop(COPIES_EXTRA, None)
        if not isinstance(extra, dict):
            continue
        for copy in track.get("candidates") or []:
            # **what is beside it wins.** It is written from memory on every save, so it is never the
            # staler of the two — and for `provider` that is the whole point: whatever an older reader
            # did to the field inside the candidate is undone by the next load here (R-215).
            copy.update(extra.get(copy.get("ref", "")) or {})
    return data


# What a signed address looks like, whoever signed it: Patreon's own `token-hash`/`token-time`, and
# the CloudFront and S3 parameters every other CDN uses. A guard in the suite greps a written plan for
# these, because the rule is not "we remembered to strip the cover" but "nothing of the session is in
# the file" (§9, slice 73).
SIGNED = re.compile(r"[?&](token-hash|token-time|token|policy|signature|key-pair-id|expires|"
                    r"x-amz-[a-z-]+)=", re.I)
# the fields that hold an address of the collection rather than of the program's own making
ADDRESS_FIELDS = ("cover_url", "cover_fallback_url", "thumbnail", "art_url")


def kept_private(data: dict[str, Any], provider: str | None) -> dict[str, Any]:
    """Drop what a private source's plan may not carry (§9, slice 73).

    **Decision: none, rather than the address without its query.** A signed address is a piece of the
    session and is never written; a *de-signed* one is worse than nothing — it answers 403 for ever,
    looks like a live address to every pass that reads the field, and would be retried on every run.
    The only honest source of a private album's cover is the provider, at the moment the cover is
    wanted, from a read it makes then. So the field is empty and the fetch asks again.

    It runs on **every** save, so a plan written before this rule existed is cleaned the next time
    anything writes it — which is what `repair` does to a whole library in one pass.
    """
    from . import sources

    if not sources.private(provider):
        return data
    for name in ADDRESS_FIELDS:
        if data.get(name):
            data[name] = None
    # and a belt for anything holding one that nobody thought of: a value is dropped, never trimmed
    def sweep(value: Any) -> Any:
        if isinstance(value, str):
            return None if SIGNED.search(value) else value
        if isinstance(value, dict):
            return {k: sweep(v) for k, v in value.items()}
        if isinstance(value, list):
            return [sweep(v) for v in value]
        return value

    return {k: sweep(v) for k, v in data.items()}


def written(plan: AlbumPlan, album_dir: Path) -> dict[str, Any]:
    """The exact object a save puts in the file — **the one place that decides it** (§9, slice 60).

    `plan --verify` has to compare a file against what a save would write, and `repair` has to tell
    whether a save would change it; both ask here rather than rebuilding the rule, because a second
    copy of it is a second thing to get wrong.
    """
    out = kept_private(narrow_candidates(portable(plan.to_dict(), album_dir)), plan.provider)
    # **only a plan that really holds one says so.** Every YouTube and SoundCloud album keeps
    # schema 1 and stays byte-for-byte what it was, readable by ytalbum 0.9.1 and by every noaap
    # up to 1.5.0 (R-207, ruling 1).
    out["schema"] = 2 if _has_relative(out) else 1
    return out


def as_saved(value: dict[str, Any]) -> str:
    """What the file holds, byte for byte, for the object `written` returned."""
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def save_plan(plan: AlbumPlan, album_dir: Path) -> Path:
    album_dir.mkdir(parents=True, exist_ok=True)
    path = album_dir / PLAN_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(as_saved(written(plan, album_dir)))
    os.replace(tmp, path)
    return path


def rewritten(album_dir: Path, plan: AlbumPlan) -> bool:
    """Would saving this plan change the file it came from?

    The question `repair` asks of every album: a plan whose paths are still absolute answers yes, and
    that is how one command converts a whole library (§9, slice 60, R-207 ruling 2).
    """
    path = album_dir / PLAN_FILE
    if not path.is_file():
        return True
    try:
        return written(plan, album_dir) != json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True


def _has_relative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_relative(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_relative(v) for v in value)
    return isinstance(value, str) and value.startswith(HERE)


def lost_files(album_dir: Path, plan: AlbumPlan) -> list[str]:
    """Finished tracks whose own file is not where the plan says (§9, slice 60).

    **Its own file**, and nothing else. A track whose *source* has gone — an album taken in from a
    folder that is no longer mounted — is not this: the file is here, the track is complete, and
    saying otherwise would tell a user their library is broken when only a re-fetch would be
    (R-207, ruling 3).
    """
    return [t.filename for t in plan.tracks
            if t.state == "done" and t.filename and not (album_dir / t.filename).is_file()]


def lost_albums(library: Path) -> list[tuple[Path, list[str]]]:
    """Every album holding a finished track whose own file is not where the plan says (§9, slice 60).

    **One place counts it** (R-212): `noaap config` says it, and `adopt --apply`, `update` and `repair`
    each end with it. Slice 60 built this check and slice 61 proved what it is worth on its own — the
    answer was right and printed nowhere a person was looking, so 67 tracks of somebody's collection
    were copied instead. A check nobody is made to look at has found nothing.
    """
    return [(album_dir, lost) for album_dir, plan in iter_plans(library)
            if (lost := lost_files(album_dir, plan))]


def lost_sentence(found: list[tuple[Path, list[str]]]) -> str | None:
    """The one line, or `None` when there is nothing to say — which is the ordinary case."""
    if not found:
        return None
    tracks = sum(len(lost) for _, lost in found)
    return f"{tracks} track(s) in {len(found)} album(s) are not where their plan says"


def plans_of_the_old_name(library: Path | None) -> list[Path]:
    """Album folders holding `.ytalbum.json` and no `.noaap.json` (R-419, point 3).

    Nothing reads the old name since 1.30.0, so such an album is one this program cannot see. It is
    never read and never guessed at — it is said out loud, with the command that ends it.
    """
    if library is None or not Path(library).is_dir():
        return []
    return sorted(path.parent for path in Path(library).rglob(OLD_PLAN_FILE)
                  if not (path.parent / PLAN_FILE).exists())


def says_the_old_name(library: Path | None) -> str:
    """The one line a pass prints for them, or nothing at all."""
    found = plans_of_the_old_name(library)
    if not found:
        return ""
    first = ", ".join(str(p.name) for p in found[:3]) + (" …" if len(found) > 3 else "")
    return (f"⚠ {len(found)} album(s) still hold {OLD_PLAN_FILE} and no {PLAN_FILE} ({first}). "
            f"Nothing reads that name any more: `noaap migrate --apply` renames them.")


def iter_plans(library: Path) -> Iterator[tuple[Path, AlbumPlan]]:
    """Every album folder in the library (<library>/<artist>/<album>/.noaap.json)."""
    for path in sorted(library.glob(f"*/*/{PLAN_FILE}")):
        try:
            yield path.parent, read_plan(path, path.parent)
        except (ValueError, KeyError, TypeError) as e:
            log.warning("ignoring unreadable plan %s: %s", path, e)


def artists_without_albums(library: Path) -> dict[Path, tuple[int, int]]:
    """Artist folders that hold no album but hold files of the owner's (§9, slice 132).

    What a pass that moves albums leaves behind. When the user's library went to one spelling per
    artist, `Umbra Et Imago`'s four albums moved to `Umbra et Imago` — and the folder stayed, because
    a `logo.jpg` and a `.thumb` of the owner's were still in it. `empty_under` cannot see it: it is
    not empty. So nothing removed it and nothing said it was there.

    Seven of them on the user's share, in three shapes: five are a logo left by a spelling move
    (`asp`, `emma`, `Umbra Et Imago`, `J.B.O.`, `Oomph! feat. Lame Immortelle`), one is an artist
    folder with two album folders in it that hold a `cover.jpg` and no plan (`omd`), and one —
    `Spotify` — is **33 audio files of the user's that no pass has ever taken in**. That last is why
    the count says how many are audio: a stray logo and thirty-three unfiled recordings are not the
    same news.

    Each folder maps to (files, of which audio). **Named, never removed**: the files are the owner's,
    and nobody asked for them to be moved or binned.
    """
    out: dict[Path, tuple[int, int]] = {}
    try:
        artists = sorted(p for p in library.iterdir() if p.is_dir())
    except OSError:
        return out
    for artist in artists:
        if is_hidden_name(artist.name):
            continue
        with contextlib.suppress(OSError):
            if any((album / PLAN_FILE).is_file() for album in artist.iterdir() if album.is_dir()):
                continue
        files = [f for f in artist.rglob("*") if f.is_file() and f.name != PLAN_FILE]
        if files:
            out[artist] = (len(files), sum(1 for f in files if f.suffix.lower() in AUDIO))
    return out


def find_plan(library: Path, source_id: str) -> tuple[Path, AlbumPlan] | None:
    """The album folder already made from this source, wherever the user renamed it to."""
    return next(((d, p) for d, p in iter_plans(library) if p.source_id == source_id), None)


def relocate(album_dir: Path, plan: AlbumPlan, library: Path, want: Treatment | None = None) -> Path:
    """Move the album folder to where the (edited) plan says it belongs. Never overwrites.

    An album that keeps its names stays where its owner put it (§9, slice 58) — unless the library is
    set to rename adopted albums and this one is not excepted from that (§9, slice 100).
    """
    if not renames(plan, want):
        return album_dir
    target = library / wanted_folder(plan)
    if not album_dir.exists() or album_dir.resolve() == target.resolve():
        plan.folder = wanted_folder(plan)
        return target
    if target.exists():
        log.warning("not moving %s: %s already exists", album_dir, target)
        return album_dir
    target.parent.mkdir(parents=True, exist_ok=True)
    album_dir.rename(target)
    plan.folder = wanted_folder(plan)
    try:
        album_dir.parent.rmdir()  # the old artist folder, only if now empty
    except OSError:
        pass
    return target


# -- running a plan ----------------------------------------------------------------------


def _measure_candidate(track: PlanTrack, path: Path) -> None:
    """Record what the chosen candidate actually sounds like, from the file we now have."""
    if candidate := track.candidate(track.effective_id):
        # never trade a number for an absence: a provider that measured this file before handing
        # it over knows more than a header that will not answer (§9, slice 53)
        candidate.length = track.file_length or candidate.length
        for name, value in audio_quality(path).items():
            setattr(candidate, name, value)
        if candidate.from_video:
            # **audio that came out of a video earns one decode** (§9, slice 72). Every other
            # download is trusted to be what its source called it; this one is a stream copied out
            # of a picture nobody kept, so where it stops is written down at the moment the file
            # exists — it is the number the next pass would otherwise have to guess at.
            from .spectrum import measure as spectrum_of

            band = spectrum_of(path)
            candidate.cutoff_khz, candidate.full_band = band.cutoff, band.full


def library_of(album_dir: Path, plan: AlbumPlan) -> Path | None:
    """The library root this album sits in, or None if it does not sit in one.

    `album_dir` is `library / plan.folder` by construction everywhere in the program, so the root
    is the folder's own depth above it. It is derived rather than passed because **every** caller of
    `run` has to obey the rule that follows from it, including the ones that never expected to move
    a file — and a parameter eight call sites can forget is not a rule (§9, slice 55).
    """
    depth = len(Path(plan.folder).parts)
    root = album_dir
    for _ in range(depth):
        root = root.parent
    return root if root / plan.folder == album_dir else None


# every container a track's audio can arrive in. A provider hands over what it has, and the same
# song can therefore sit in the folder twice under two suffixes — which is what this is for.
AUDIO = (".opus", ".m4a", ".mp4", ".mp3", ".flac", ".ogg", ".oga", ".wav", ".aac", ".webm")


def _put_away(album_dir: Path, plan: AlbumPlan, track: PlanTrack, final: Path) -> None:
    """Move audio the new file has displaced into the bin, with the usual entry.

    **An album folder holds no audio its plan does not name.** Before this, a fetch that landed in
    another container — `merge`'s "take this one", an `audio_choice` switch, any candidate in
    another format — wrote the new file and left the old one beside it: nothing removed, and nothing
    put away either, so a player scanning the folder saw the song twice (found 2026-09-28, taking a
    FLAC over an Opus through the panel).

    **The displaced file is found by the name, not by watching the extension change.** Those are not
    the same thing, and that is the whole difficulty: `merge` renames as the file arrives, an
    `audio_choice` switch renames in the plan before the fetch is even asked for, and by the time
    the download runs neither of them has left a trace of what was there. A switch never changes a
    track's *name*, only its container — so the same stem in another suffix is this track's previous
    file, whoever renamed it and whenever.

    The entry is written while the track carries that old name: it says which file was taken away,
    and a restore puts that one back.
    """
    from .recycle import bin_track  # here, not at import time: recycle reads plans this module writes

    library = library_of(album_dir, plan)
    if library is None:
        return
    for path in sorted(final.parent.iterdir()):  # beside the new file, which need not be the root
        if not path.is_file() or path == final or path.suffix.lower() not in AUDIO \
                or path.stem != final.stem:
            continue
        now, track.filename = track.filename, str(path.relative_to(album_dir))
        try:
            bin_track(library, album_dir, plan, track, reason=f"replaced by {now}", audio=path)
        finally:
            track.filename = now


def transfer_line(provider: Any) -> str | None:
    """What the last download moved, when a provider has an answer and the two numbers differ.

    Optional everywhere (§9, slice 73): asked with `getattr`, and silent when the provider does not
    keep the number or when nothing was thrown away. It exists because a download that fetches 52 MB
    to keep 17 is a fact somebody should see once, and the first live run of such a fetch could not
    report it — the numbers were written to a log that was not turned on.
    """
    moved = getattr(provider, "last_transfer", None)
    if not isinstance(moved, dict):
        return None
    got, kept = moved.get("downloaded"), moved.get("kept")
    if not got or not kept or got <= kept:
        return None
    away = moved.get("thrown_away") or "the rest"
    return (f"{got / 1e6:.1f} MB fetched, {kept / 1e6:.1f} MB kept "
            f"({away} was thrown away, {100 - kept * 100 // got}% of it)")


def _words_from_source(provider: Any, plan: AlbumPlan, track: PlanTrack,
                       album_dir: Path) -> str | None:
    """Write the captions a provider found beside this track, or say why there are none.

    **A sidecar and nothing else** (§9, slice 74): the words are the creator's text, they are marked
    as theirs, and `tag.py` refuses to put words marked that way into the audio file — so no pass,
    retag or repair can carry them into the file later. Returns the one line to say, once.
    """
    found = getattr(provider, "last_captions", None)
    if not isinstance(found, dict):
        return None
    cost = _captions_cost(found)
    if refused := found.get("refused"):
        return f"no words beside this track: {refused}{cost}"
    # a provider may hand over a file to read or the lines it already joined out of several
    cues = found.get("cues") or captions.read(found.get("file") or b"")
    if not cues:
        return f"no words beside this track: its caption file holds no lines{cost}"
    whose = str(found.get("by") or "the source")
    text = captions.as_lrc(cues)
    # the marks go on before the sidecar is written, because `write_sidecar` records what the words
    # were written against and the panel reads the marks to decide what it may offer
    track.provenance["lyrics"] = Provenance.SOURCE
    track.lyrics_words_by = whose
    track.lyrics_timed_by = whose
    track.lyrics = status_of(text)
    track.lyrics_id = None       # they are nobody's entry anywhere, and never become one
    write_sidecar(album_dir, track, text)
    return (f"{len(cues)} line(s) of {whose}'s own captions{cost}, kept beside the track "
            "and nowhere else")


def _captions_cost(found: dict[str, Any]) -> str:
    """What the captions cost and what they turned out to be — said, not left to be inferred.

    The first live run of a caption playlist could only *guess* which timestamp-map convention it had
    met, from the shape of the result, and could not say how many bytes the segments were although
    the number had been counted for the cap (§9, slice 76, R-266). Both are said here and neither is
    written into a plan: they describe the transfer, not the words.
    """
    asked, segments = found.get("requests"), found.get("segments")
    size, convention = found.get("bytes"), found.get("map")
    parts = []
    if asked:
        parts.append(f"{asked} request{'s' if asked != 1 else ''}")
    if segments:
        parts.append(f"{segments} segment{'s' if segments != 1 else ''}")
    if size:
        parts.append(f"{size / 1000:.1f} kB")
    if convention:
        parts.append(str(convention))
    return f" ({', '.join(parts)})" if parts else ""


#: how much of a tag value a report shows before it cuts it off
SHOWN = 60


#: the one line `would_do` ends with that promises nothing — the picture may not be in there at all.
#: A caller deciding whether an album needs a pass at all has to be able to tell it from the rest
#: (§9, slice 124), or an album with no cover beside it is never skipped again.
MAYBE_COVER = " if a picture can be found in its files"


def would_do(plan: AlbumPlan, album_dir: Path, cover: bytes | None = None,
             library: Path | None = None, want: Treatment | None = None) -> list[str]:
    """Everything `run(..., download=False)` would change about this album, one line each.

    **The dry run's source of truth** (§9, slice 85). It asks the same three questions the pass itself
    asks — the wanted name, the trim signature, the tag signature — so that a dry run cannot leave out
    something the real run then does. That is not hypothetical: `repair --dry-run` reported lengths and
    albums and said nothing about tags, and the run that followed rewrote 376 audio files, after the
    user had been told on the dry run's word that no audio file would be touched (R-288).

    Reads files; writes nothing, and asks nobody anything.
    """
    said: list[str] = []
    # what the library is set to want, narrowed by this album's own exceptions (§9, slice 100)
    rename, retag = renames(plan, want), retags(plan, want)
    drop = drops_comments(plan, want)
    drop_wm = drops_wm_frames(plan, want)
    # **two tracks that would get one name**: the album keeps its own names, and says which (R-410)
    if rename and (clashes := clashing_names(plan)):
        for name, tracks in sorted(clashes.items()):
            said.append(f"⚠ {', '.join(str(t.number) for t in tracks)} would all be called {name} "
                        "— the album keeps its own names")
        rename = False
    # **a cover beside the album is a line of its own** (§9, slice 101). Whether one can be found is
    # the same work as finding it — for an adopted album, reading its files until a picture turns up —
    # so the dry run says what would be asked and does not promise the answer.
    if want is not None and want.cover_beside and cover is None:
        if address := _cover_addresses(plan, album_dir):
            said.append("a cover would be saved beside the album"
                        + (MAYBE_COVER if address[0] == str(album_dir)
                           else f" from {address[0]}"))
    if want is not None and not want.cover_embedded:
        cover = None
    if library is not None and rename:
        target = library / wanted_folder(plan)
        if album_dir.exists() and album_dir.resolve() != target.resolve():
            said.append(f"the album folder would move to {wanted_folder(plan)}"
                        + (" — but something is already there, so it would stay" if target.exists() else ""))
    for track in plan.tracks:
        if track.state != "done":
            continue
        wanted = wanted_filename(plan, track) if rename else track.filename
        here = album_dir / track.filename
        if track.filename != wanted:
            said.append(f"{track.number:02d} would be renamed: {track.filename} → {wanted}")
        final = album_dir / wanted
        if not final.exists() and not here.exists():
            continue
        if trim_signature(track) != (track.trimmed or ""):
            said.append(f"{track.number:02d} would be cut to its trim points"
                        if trim_signature(track) else f"{track.number:02d} would be put back untrimmed")
        elif trim_signature(track) and (before := starts_before_zero(final if final.exists() else here)) is not None:
            # cut to the right points by a version that left the clock before zero (§9, slice 86)
            kept = original_path(album_dir, track)
            said.append(f"{track.number:02d} would be cut again: its clock starts at {before:.3f} s"
                        if kept.exists() else
                        f"{track.number:02d} starts at {before:.3f} s and cannot be cut again: "
                        "no untouched original is kept beside it")
        path = final if final.exists() else here
        # **a file 1.31.2 wrote still has its ID3v1 tail** (R-434), and nothing else can see it: the
        # signature is of what the plan asked for, and that is exactly what the file holds. The
        # block goes wherever this program wrote the file's tags — `tagged` is set only then — so a
        # library that leaves adopted albums alone still gets back the state its own writer leaves.
        tail = ends_with_an_id3v1_tail(path) and (retag or track.tagged is not None)
        # **and the comment the library asks to be rid of** (R-438, ruling 1), which the signature
        # cannot see either: the plan never asserted a comment, so a file holding one looks done.
        comments = comments_in(path) if drop else 0
        wm = wm_frames_in(path) if drop_wm else 0
        if not retag and not tail:
            continue   # nothing is written into this file: adopted, and the library does not ask
        text = read_sidecar(album_dir, track)
        if want is not None and not want.lyrics_embedded:
            text = None   # the words stay beside the track, not in it
        if track.tagged == signature(plan, track, cover, text) and not tail and not comments \
                and not wm:
            continue
        try:
            have = tags_in(path)
        except (MutagenError, OSError) as e:
            said.append(f"{track.number:02d} would be retagged — the file cannot be read: {e}")
            continue
        wanted_tags = build_tags(plan, track, text)
        kept = kept_from_the_file(plan)
        for key in kept:                       # what the file already answers better than the plan
            if have.get(key):
                wanted_tags.pop(key, None)
        diffs = differences(have, wanted_tags)
        changed = [_change(key, have.get(key), wanted_tags[key]) for key in diffs]
        # an adopted album's other fields are left alone, so the dry run must not claim they go
        gone = ([] if plan.adopted else
                [key for key in have if key not in wanted_tags and key not in kept])
        if gone or (diffs and not only_padding(have, wanted_tags, diffs)):
            said.append(f"{track.number:02d} would be retagged: "
                        + "; ".join(changed + [f"{key} would be dropped" for key in gone]
                                    + ([f"{comments} comment(s) would be dropped"] if comments else [])
                                    + ([f"{wm} Windows Media frame(s) would be dropped"] if wm else [])))
        elif cover is not None and embedded_cover(path) != cover:
            # the tags are right and the picture is not: one line, because it is one write
            said.append(f"{track.number:02d} would get the album's picture")
        elif tail or comments or wm:
            why = ([f"{comments} comment(s) would be dropped"] if comments else []) \
                + ([f"{wm} Windows Media frame(s) would be dropped"] if wm else []) \
                + (["without its ID3v1 tail"] if tail else [])
            said.append(f"{track.number:02d} would be rewritten: " + ", ".join(why))
        # **and otherwise nothing is said, because nothing is written** (R-410, ruling 7). This used
        # to report "would be rewritten with the same tag values (the plan's record of them is out
        # of date)" and the pass then rewrote the file for the record's sake — 455 files of the
        # user's collection, each read, copied, replaced and verified to end up as it already was.
    return said


def points_elsewhere(plan: AlbumPlan, album_dir: Path) -> bool:
    """Whether this folder-sourced plan says its album, or one of its files, is somewhere it is not.

    The album's own address (§9, slice 125) **and its tracks'** (§9, slice 130) — one question, so
    that one `point_at` settles both and the skip above it cannot be true while either is wrong.
    """
    return _source_points_elsewhere(plan, album_dir) or bool(tracks_elsewhere(plan, album_dir))


def tracks_elsewhere(plan: AlbumPlan, album_dir: Path) -> list[PlanTrack]:
    """The folder-sourced tracks whose id names a file that is gone or a staging copy's (slice 130).

    A folder provider's ref **is** the source file's path, so it is the track's `video_id` — and when
    the album moves, or the staging copy it was read from is deleted, that id names nothing while the
    file itself sits beside the plan under `filename`. 33 tracks of two of the user's albums were in
    that state after their folders moved, and a re-read of the folder then reports *every* track new
    and every known track gone: `13 new, 13 no longer in the source`. An apply on that report would
    rewrite the plan as all-new tracks and mark the thirty-three that are there as missing.

    The same question as the album's, asked per track — **gone, or under a staged index** — because
    an album taken in from a folder that is still where the plan says must keep pointing at it.
    """
    if plan.provider != "folder":
        return []
    out: list[PlanTrack] = []
    for track in plan.tracks:
        said = str(track.video_id or "")
        if not said.startswith("/") or not track.filename:
            continue
        here = album_dir / track.filename
        if not here.is_file() or Path(said) == here:
            continue    # nothing to point at, or already pointing at it
        where = Path(said)
        if not where.exists() or _under_a_staging_folder(where):
            out.append(track)
    return out


def _point_track_at(track: PlanTrack, file: Path) -> bool:
    """Give one track the path its file is really at — candidate, override and refusals with it.

    `video_id` is not alone: `sync_candidates` keeps a `Candidate` per ref, so changing the id without
    its candidate would leave the stale ref in the list and add a second one beside it.
    """
    was, now = str(track.video_id), str(file)
    if was == now:
        return False
    for candidate in track.candidates:
        if candidate.ref == was:
            candidate.ref = now
    # one candidate per ref: where a candidate for the file's real path was already there, renaming
    # the stale one onto it would leave two, and `candidate(ref)` answers with whichever comes first
    kept: list[Candidate] = []
    for candidate in track.candidates:
        if not any(other.ref == candidate.ref for other in kept):
            kept.append(candidate)
    track.candidates = kept
    if track.source_override == was:
        track.source_override = now
    track.refused_candidates = [now if ref == was else ref for ref in track.refused_candidates]
    track.video_id = now
    track.sync_candidates()
    return True


def _source_points_elsewhere(plan: AlbumPlan, album_dir: Path) -> bool:
    """Whether this folder-sourced plan says its album is somewhere it is not (§9, slice 125).

    A staged take-in runs the ordinary pass against the **staging copy**, so the folder provider
    recorded that copy's path: 154 plans on the user's share name
    `~/noaap-nas-run/staging/batch/<album>` as their `source_id`, `source_url` and `cover_url`, and
    that folder was deleted when the run ended. The cover is beside the album anyway, so nothing was
    lost — but a cover address that cannot be read keeps its album out of every skip for ever (slice
    124), and a source that names a folder nobody will ever see again is simply not true.

    Only ever a folder-sourced plan, and only where the path **is gone or is a staging folder's**: a
    fetched album's `source_url` is a URL, and an album taken in from a folder somewhere else keeps
    pointing at that folder — `repair --find-moved` exists to follow it when it moves, and a first
    version of this overwrote what that had just found.
    """
    if plan.provider != "folder":
        return False
    here = str(album_dir)
    for value in (plan.source_id, plan.source_url, plan.cover_url):
        said = str(value or "")
        if not said.startswith("/") or said.startswith(here):
            continue
        where = Path(said)
        if not where.exists() or _under_a_staging_folder(where):
            return True
    return False


def _under_a_staging_folder(path: Path) -> bool:
    """Whether a staged pass's own index sits at or above this path (§9, slice 125).

    The copy a staged run takes in from lives beside its index, so the index is what says "this is a
    staging folder" — no name is assumed, and a folder of the owner's called `batch` is not one.
    """
    for folder in (path, *path.parents):
        if (folder / STAGED_INDEX).is_file():
            return True
    return False


def point_at(plan: AlbumPlan, album_dir: Path) -> bool:
    """Say where this album and its files are. True when anything changed.

    What the plain pass records, which is what a staged one should have recorded (§9, slice 125) —
    and, since slice 130, the tracks' own ids too. **The two halves are gated apart**: a plan whose
    source really is a folder elsewhere that is still there keeps that address even while a track of
    it needs re-pointing, and the other way round.
    """
    changed = False
    if _source_points_elsewhere(plan, album_dir):
        was = (plan.source_id, plan.source_url, plan.cover_url)
        plan.source_id = plan.source_url = str(album_dir)
        if str(plan.cover_url or "").startswith("/"):
            # beside the album if it is there; otherwise the folder itself, which is the address the
            # provider gives for "the picture is inside one of the files"
            beside = next((q for q in sorted(album_dir.glob(f"{COVER_STEM}.*"))), None)
            plan.cover_url = str(beside or album_dir)
        changed = (plan.source_id, plan.source_url, plan.cover_url) != was
    moved: dict[str, str] = {}
    for track in tracks_elsewhere(plan, album_dir):
        was_id = str(track.video_id)
        if _point_track_at(track, album_dir / track.filename):
            moved[was_id] = str(track.video_id)
            changed = True
    if moved and (ids := (plan.source_state or {}).get("ids")):
        # what the source looked like last time, so the cheap skip above still recognises it
        plan.source_state["ids"] = [moved.get(str(one), one) for one in ids]
    return changed


def tails_to_drop(plan: AlbumPlan, album_dir: Path, want: Treatment | None = None) -> int:
    """How many of this album's files still end in an ID3v1 block this program would now drop.

    Asked by `repair` **before** it skips an album nothing else is wrong with (R-434). 1.31.2 wrote
    those blocks rather than dropping them — 175 of the 187 mp3s of one batch on the share — and
    such an album is otherwise perfectly tidy: every track done, every tag what the plan asked for.
    Without this question no pass would ever look at those files again. 128 bytes per mp3, nothing
    at all for any other format, and nothing where the album's files are not this program's to write.
    """
    retag = retags(plan, want)
    return sum(1 for t in plan.tracks
               if t.state == "done" and (retag or t.tagged is not None)
               and ends_with_an_id3v1_tail(album_dir / t.filename))


def comments_to_drop(plan: AlbumPlan, album_dir: Path, want: Treatment | None = None) -> int:
    """How many comment fields a write would take out of this album's files, or 0 (R-438, ruling 1).

    Asked by `repair` before it skips an album, for the same reason as `tails_to_drop`: the plan
    never asserted a comment, so an album full of somebody else's `ripped by` is otherwise perfectly
    tidy and nothing would look at it again. One tag read per track, and only where the setting is on.
    """
    if not drops_comments(plan, want):
        return 0
    return sum(comments_in(album_dir / t.filename) for t in plan.tracks if t.state == "done")


def wm_frames_to_drop(plan: AlbumPlan, album_dir: Path, want: Treatment | None = None) -> int:
    """How many Windows-Media `PRIV` frames a write would take out of this album's files, or 0."""
    if not drops_wm_frames(plan, want):
        return 0
    return sum(wm_frames_in(album_dir / t.filename) for t in plan.tracks if t.state == "done")


def _already_right(plan: AlbumPlan, track: PlanTrack, path: Path, cover: bytes | None,
                   text: str | None, theirs: bool, drop: bool = False,
                   drop_wm: bool = False) -> bool:
    """Whether writing this file would leave it exactly as it is (R-410, ruling 7).

    Asked in the same words as the dry run's, so what one promises the other does: every value the
    writer would put there is already there — bar a number written with a leading zero, which is
    noaap's own spelling and not worth a rewrite — and the picture inside the file is the one the
    album carries. The picture is read only where the tags already agree, so a file that is going
    to be rewritten anyway is not read twice.
    """
    # **the tail is a change too** (R-434), and so is a comment the library asks to be rid of
    # (R-438): the writer would take either away, so a file that still holds one is not already
    # right, however exactly its tags agree.
    if ends_with_an_id3v1_tail(path) or (drop and comments_in(path)) \
            or (drop_wm and wm_frames_in(path)):
        return False
    try:
        have = tags_in(path)
    except (MutagenError, OSError):
        return False
    wanted = would_write(plan, track, text, theirs, have)
    diffs = differences(have, wanted)
    if diffs and not only_padding(have, wanted, diffs):
        return False
    # **a key the writer would take away is a change too.** Without `keep_unknown` the writer clears
    # the file and puts `wanted` back, so anything else in there goes — the deleted sidecar whose
    # `lyrics` tag must follow it is exactly that case, and treating the key as harmless here left
    # the words in the file after the pass claimed to have removed them.
    if not theirs and any(key not in wanted for key in have):
        return False
    return cover is None or embedded_cover(path) == cover


def needs_a_recut(plan: AlbumPlan, album_dir: Path) -> list[PlanTrack]:
    """The tracks whose file is cut to the right points but whose clock starts before zero.

    Cheap enough to ask about every album (one ffprobe per *cut* track, and most tracks are not cut),
    and it has to be asked before the pass decides to skip an album: the plan of such an album is
    perfectly tidy, so nothing else would ever look at its files again (§9, slice 86).
    """
    out = []
    for track in plan.tracks:
        if track.state != "done" or not trim_signature(track) \
                or trim_signature(track) != (track.trimmed or ""):
            continue
        path = album_dir / track.filename
        if path.exists() and starts_before_zero(path) is not None:
            out.append(track)
    return out


def _change(key: str, old: object, new: object) -> str:
    """One tag value that would change, old → new, in a line somebody can read.

    **Where they differ, not where they start.** A lyric is two thousand characters and the change may
    be its last one — the first version of this report showed the first sixty of each and printed the
    same text twice (measured on the user's own albums), which is worse than saying nothing.
    """
    before, after = ("" if old is None else str(old)), ("" if new is None else str(new))
    if len(before) <= SHOWN and len(after) <= SHOWN:
        return f"{key} {_short(before, empty='nothing')} → {_short(after, empty='nothing')}"
    same = len(_prefix(before, after))
    if same >= SHOWN:   # they agree for longer than a line: say where they stop agreeing
        return (f"{key} differs from character {same} of {len(before)} → {len(after)}: "
                f"{_short(before[same:], empty='the end of it')} → {_short(after[same:], empty='the end of it')}")
    return f"{key} {_short(before, empty='nothing')} → {_short(after, empty='nothing')}"


def _prefix(one: str, other: str) -> str:
    """How much of two values is the same, from the start."""
    for i, (a, b) in enumerate(zip(one, other, strict=False)):
        if a != b:
            return one[:i]
    return one[:min(len(one), len(other))]


def _short(value: object, empty: str = "nothing") -> str:
    """One tag value for a report: quoted, on one line, and cut where it stops being useful."""
    if value is None or value == "":
        return empty
    text = str(value).replace("\n", "⏎").replace("\t", " ")
    shown = f"{text[:SHOWN]}…" if len(text) > SHOWN else text
    return f"“{shown}”"


def run(
    plan: AlbumPlan,
    album_dir: Path,
    source: Source,
    # **what the library is set to want** (§9, slice 100), narrowed by this album's exceptions. None
    # means what every pass meant before it existed: rename and retag unless the album is adopted.
    want: Treatment | None = None,
    # **files that cannot be fetched again are written the careful way** (§9, slice 99): to a copy
    # beside them, proved to hold the same recording, then an atomic replace. It costs a copy and a
    # digest per write, which is why noaap's own downloads do not pay it.
    careful: bool = False,
    # where the untouched originals are kept, and what they are kept relative to (§9, slice 99).
    # Only a take-in of somebody's own collection asks for this; it costs a copy of every file.
    keep: Path | None = None,
    keep_root: Path | None = None,
    # the folder the snapshot knows this album by, where that is not the folder it is in now: a pass
    # that moves an album into noaap's scheme does it before the first write (§9, slice 99). What is
    # filed is `keep_as` plus the track's own place inside the album, so a disc subfolder is kept too.
    keep_as: str | None = None,
    # the digest a snapshot already recorded for a file, by its name in the album: it is what makes a
    # careful write read one file instead of two (§9, slice 102, R-342 ruling 4).
    expect: Callable[[str], str | None] | None = None,
    # A track's audio comes from its chosen candidate, which carries its own provider: one album can
    # hold tracks from two of them (§9, slice 50). `source` stays for what belongs to the collection
    # — the cover — and this answers for a track.
    track_source: Callable[[PlanTrack], Source] | None = None,
    on_track: Callable[[PlanTrack, str], None] = lambda t, what: None,
    check: Callable[[], None] = lambda: None,
    download: bool = True,
    lyrics: LyricsAPI | None = None,
    say: Callable[[str], None] = lambda line: None,
) -> AlbumPlan:
    """Download, tag and place every track that is not done yet; rename/retag finished ones.

    `check()` is called between tracks and may raise to stop (cancel); the plan is always saved.
    With a `lyrics` client, tracks that were never looked up get their `.lrc` sidecar here.
    """
    refresh_derived(plan)
    save_plan(plan, album_dir)
    whose = track_source or (lambda _t: source)
    recorded = dict(plan.cover_fetched)
    # **whether this album may be given a cover at all is the library's state** (§9, slice 100), not
    # a property of the pass. Before that, only a download fetched one, so `repair` could never add
    # the cover a setting asks for — and a take-in's own switch for it did nothing at all.
    cover = _cover(plan, album_dir, source,
                   fetch=want.cover_beside if want is not None else download)
    if plan.cover_fetched != recorded:
        # **the record of a cover we just wrote is saved now, not when a track happens to change**
        # (§9, slice 64). The plan is written above and then only again by a track that did something,
        # which for an adopted album is never — so every version up to 1.6.0 left a cover file on disk
        # with no record that noaap had written it, and an undo then kept it as the owner's. Sixteen
        # of them in one real library.
        save_plan(plan, album_dir)
    parts = album_dir / PARTS_DIR

    rename, retag = renames(plan, want), retags(plan, want)
    drop, drop_wm = drops_comments(plan, want), drops_wm_frames(plan, want)
    # **the same answer the dry run gave** (R-410, ruling 1): where two tracks want one name the
    # album is not renamed at all, rather than renaming whichever the loop reaches first and
    # recording a name the other file never got.
    if rename and clashing_names(plan):
        rename = False
    if want is not None and not want.cover_embedded:
        cover = None          # the picture stays beside the album, not inside every file
    emptied: set[Path] = set()   # disc folders the renames below take the last file out of
    for track in plan.tracks:
        check()
        # **the original goes aside before anything happens to the file** (§9, slice 99), under the
        # name it has now — which is the name the snapshot wrote down. Putting it aside after the
        # rename filed it under noaap's name instead, and a restore then could not find it.
        # **both of these are asked before the rename**, while the file still has the name the
        # snapshot wrote down: where the original is kept, and what it is expected to hold.
        recorded = expect(track.filename) if expect else None
        if keep is not None and track.state == "done":
            here_now = album_dir / track.filename
            if here_now.exists():
                put_aside(here_now, keep_root or album_dir, keep,
                          as_path=f"{keep_as}/{track.filename}" if keep_as else None)
        wanted = wanted_filename(plan, track) if rename else track.filename
        if track.state == "done" and track.filename != wanted:
            old, new = album_dir / track.filename, album_dir / wanted
            if old.parent != album_dir:
                emptied.add(old.parent)     # a disc folder this pass is taking the files out of
            if old.exists() and not new.exists():
                old.rename(new)
                on_track(track, "renamed")
            rename_sidecar(album_dir, track.filename, wanted)  # the lyrics follow the audio
            track.filename = wanted
            save_plan(plan, album_dir)
        final = album_dir / track.filename

        if track.state == "done" and final.exists():
            cut = failed_trim = False
            try:
                if cut := apply_trim(album_dir, track, final):
                    track.tagged = None  # the new file needs its tags again
                    track.error = None  # a trim that was refused before has now gone through
                    if track.lyrics is not None and lyrics is not None:
                        track.lyrics = None  # the file is a different length: match it again
                    on_track(track, "trimmed")
            except RuntimeError as e:
                # the trim points stay: they are what the user asked for, and the next run tries
                # again. What must not happen is losing the reason it did not happen this time.
                track.error, failed_trim = str(e), True
                log.warning("%s: %s", track.filename, e)
                on_track(track, "trim failed")
            measured = cut or track.file_length is None  # measured once, then only when it changes
            if measured:
                track.file_length, track.file_length_by = measure(final)
                _measure_candidate(track, final)
            # whose words are beside this track, and does the plan still agree with the disk?
            # Asked before the lookup, so an edited sidecar is known to be the user's by the
            # time anything would overwrite it (a trim clears the status and asks again).
            text, reconciled = reconcile(album_dir, track, final)
            looked_up = lyrics is not None and track.lyrics is None
            if looked_up:
                text = update_track(lyrics, plan, track, album_dir, final)
            if want is not None and not want.lyrics_embedded:
                text = None   # …and the words stay beside the track
            # the ID3v1 block this program used to rewrite, where this program wrote the tags
            # (R-434), and the comment the library asks to be rid of (R-438). Neither is in the
            # signature, so without these two questions a file that holds one is never looked at.
            tail = ends_with_an_id3v1_tail(final) and (retag or track.tagged is not None)
            comments = comments_in(final) if drop else 0
            wm = wm_frames_in(final) if drop_wm else 0
            try:
                if not retag and not tail:
                    # nothing is written into this file by any pass (§9, slice 58). The words still
                    # arrive as a sidecar beside it; the tag inside the file is the owner's.
                    if looked_up or measured or failed_trim or reconciled:
                        save_plan(plan, album_dir)
                elif track.tagged != signature(plan, track, cover, text) or tail or comments or wm:
                    # **an album that came in from somebody's folder keeps the fields we do not
                    # model** (§9, slice 100): replaygain, ISRC, their own comment. Until the library
                    # could be told to retag such an album this never arose — `keep_tags` meant
                    # nothing was written at all — and the first case that turned retagging on lost
                    # a `comment` that had been in the file since 2006.
                    theirs = bool(plan.adopted)
                    # **a file that already says what the plan wants is not rewritten for the sake
                    # of the record** (R-410, ruling 7). The record is what was stale — a freshly
                    # adopted album has none at all — and a careful write of an unchanged file is a
                    # read, a copy, a replace and a digest to end up where it started.
                    if _already_right(plan, track, final, cover, text, theirs, drop, drop_wm):
                        track.tagged = signature(plan, track, cover, text)
                        save_plan(plan, album_dir)
                        on_track(track, "left")
                    else:
                        write = lambda f: tag_file(f, plan, track, cover, text, keep_unknown=theirs,  # noqa: E731
                                                   drop_comments=drop, drop_wm_frames=drop_wm)
                        track.tagged = (safely(final, write, expect=recorded, log=say) if careful
                                        else write(final))
                        save_plan(plan, album_dir)
                        on_track(track, f"lyrics ({track.lyrics})" if looked_up and text else "retagged")
                elif looked_up or measured or failed_trim or reconciled:
                    save_plan(plan, album_dir)  # the lookup, the length, the owner, or why the trim did not happen
            except Unsafe as e:
                # **a refused write leaves one file, never the pass** (R-433, ruling 3). The
                # precaution has already done its work — the file on disk is untouched — so the one
                # thing left to decide is what happens next, and ending a six-batch run in a
                # traceback is not it. The track says why, the plan records it, and the pass goes
                # on; `left untouched` is counted and named where the run reports.
                track.error = str(e)
                save_plan(plan, album_dir)
                log.warning("%s", e)
                on_track(track, "left untouched")
            except (MutagenError, OSError) as e:
                # this file is not what its name says, so nothing can be written to it. One bad
                # file fails its own track; the rest of the album still runs.
                track.state, track.error, track.tagged = "failed", f"cannot be tagged: {e}", None
                save_plan(plan, album_dir)
                log.warning("%s: %s", track.filename, e)
                on_track(track, "failed")
            continue
        if not track.in_source or not download:
            continue  # gone from the playlist, or we are only tidying up files
        if plan.keep_names and track.state == "done":
            # **a finished track of an adopted album is never fetched again** (§9, slice 61). Its
            # source *is* this folder, so "fetching" it copies a file the library already holds to a
            # second name inside the same album — which is exactly what happened: 67 tracks of three
            # real albums, copied into their album roots because their plans named the file without
            # the disc sub-folder it sits in. Whatever the plan says, the answer to a file that is not
            # where it should be is to find it (`repair`) or to report it (slice 60), never to make
            # another one.
            log.warning("%s: not where the plan says, and an adopted album is never re-fetched — "
                        "run `noaap repair`", track.filename)
            on_track(track, "missing")
            continue

        if track.channel is None:
            _follow_source(whose, track)  # whose upload this really is, and how long it runs
        for attempt in range(1, ATTEMPTS + 1):
            try:
                provider = whose(track)
                tmp = provider.audio(track.effective_id, parts, track.audio_choice)
                if line := transfer_line(provider):
                    say(f"  {track.number:02d} {line}")
                captions_said = _words_from_source(provider, plan, track, album_dir)
                # **the file decides what it is.** `ext` was only ever set from a YouTube audio
                # choice — opus, or m4a for a combined stream — so a provider that hands over an
                # mp3 or a flac would have had it filed under `.opus`, tagged as Opus (which
                # raises) and left as a name that lies about its contents (§9, slice 53).
                if (got := tmp.suffix.lstrip(".").lower()) and got != track.ext:
                    track.ext = got
                    # **an adopted album keeps its name here too** (§9, slice 61): only the suffix
                    # follows the file, because deriving the whole name is the one thing adoption
                    # promised not to do — and it would move the file out of its own folder.
                    track.filename = (wanted_filename(plan, track) if rename
                                      else str(Path(track.filename).with_suffix(f".{got}")))
                    final = album_dir / track.filename
                if captions_said:
                    say(f"  {track.number:02d} {captions_said}")
                # **words that came with the recording are never looked up over** (§9, slice 74)
                text = update_track(lyrics, plan, track, album_dir, tmp) \
                    if lyrics and track.provenance.get("lyrics") != Provenance.SOURCE else None
                track.file_length, track.file_length_by = measure(tmp)
                _measure_candidate(track, tmp)
                try:
                    track.tagged = tag_file(tmp, plan, track, cover, text)
                except MutagenError as e:
                    raise RuntimeError(f"downloaded file cannot be tagged: {e}") from e
                os.replace(tmp, final)
                _put_away(album_dir, plan, track, final)
                # a fresh download is by definition untouched, whatever the plan said before:
                # the trim points stay and the next pass applies them to this file
                track.state, track.error, track.error_kind, track.trimmed = "done", None, None, None
                break
            except NoAudio as e:
                track.state = "failed"
                track.error = f"no separate audio stream ({e.description})"
                track.error_kind = Failure.NO_AUDIO_STREAM
                break  # retrying changes nothing; the user picks what to do
            except (SourceError, RuntimeError, OSError) as e:
                # the provider says which it is; nothing here reads its words (§9, slice 51)
                message = str(e).removeprefix("ERROR: ").strip()
                blocked = isinstance(e, Blocked)
                track.state, track.error = "failed", message
                track.error_kind = Failure.BOT_CHECK if blocked else None
                log.debug("track %s attempt %d failed", track.video_id, attempt, exc_info=True)
                if blocked:
                    break  # retrying only makes it worse
                if attempt < ATTEMPTS:
                    time.sleep(RETRY_DELAY)
        save_plan(plan, album_dir)
        taken = f" from {track.effective_id}" if track.source_override else ""
        on_track(track, f"downloaded{taken}" if track.state == "done" else "failed")
        if track.error_kind == Failure.BOT_CHECK:
            log.warning("the source is blocking requests (bot check) - stopping this album")
            break

    if all(t.state == "done" or not t.in_source for t in plan.tracks) and parts.exists():
        shutil.rmtree(parts)  # only our own scratch dir, and only when nothing is left to resume
    # **a disc folder this pass emptied is this pass's own mess** (§9, slice 102). Bringing a
    # multi-disc album to the scheme takes every file out of `CD 1`, `CD 2` and leaves them standing
    # empty — measured on a two-disc album: both still there afterwards. Deepest first, and `rmdir`
    # refuses a folder that still holds anything, so whatever else was in there stays and so does the
    # folder. A restore puts the files back under their recorded paths, which makes it again.
    for folder in empty_under(album_dir, emptied, everything=False):
        with contextlib.suppress(OSError):
            folder.rmdir()
    return plan


def _follow_source(whose: Callable[[PlanTrack], Source], track: PlanTrack) -> None:
    """Take the uploader and the length from the video the audio actually comes from (§9, slice 34).

    Two things follow the *audio* rather than the identity: the channel, because "trim everything
    from this uploader" must not apply another channel's cut to this file, and the duration the
    page draws the trim bar with. Both are dropped by a source change in either direction, which
    is what asks for them here; a playlist listing that carried no uploader at all is answered on
    the way past. Failing to read it is not an error — the download itself will say so in a
    moment, with a better message.
    """
    try:
        facts = whose(track).probe(track.effective_id)
    except (SourceError, RuntimeError, OSError) as e:
        log.info("could not read %s: %s", track.effective_id, e)
        return
    track.channel = facts.channel or track.channel
    track.duration = facts.duration or track.duration


# -- cover -------------------------------------------------------------------------------


def cover_candidates(url: str, source: Source | None = None) -> list[str]:
    """Best-first addresses for a cover — whatever the provider knows to try (§9, slice 51)."""
    better = getattr(source, "art_candidates", None)
    return better(url) if better else [url]


def _cover(plan: AlbumPlan, album_dir: Path, source: Source, fetch: bool = True) -> bytes | None:
    """The album cover, kept as cover.* in the album folder.

    A cover the user put there is always used. One we saved ourselves is replaced once a
    better source is known (e.g. the Cover Art Archive after MusicBrainz matched).
    """
    beside = sorted(album_dir.glob(f"{COVER_STEM}.*"))
    existing = next((p for p in beside if image_mime(p.read_bytes())), None)
    # **a cover file this program cannot read is said out loud, once** (§9, slice 127). Three albums
    # of the user's keep a GIF or a BMP in a file called `cover.jpg`; those two are pictures now, so
    # what is left here is a file that is no picture at all — an html consent page saved as a cover,
    # a truncated download — and silently writing another beside it is not the answer.
    if not existing:
        for q in beside:
            log.warning("%s is not a picture this program can read, and is left as it is", q)
    if existing:
        data = existing.read_bytes()
        existing = _named_for_what_it_is(existing, data)
        ours = plan.cover_fetched.get("sha1") == _sha1(data)
        if ours and (square := square_if_padded(data)):  # saved before covers were squared
            existing.unlink()
            data = _save_cover(plan, album_dir, plan.cover_fetched.get("url", ""), square)
            existing = next(album_dir.glob(f"{COVER_STEM}.*"))
        if not ours or not plan.cover_url or plan.cover_url in (plan.cover_fetched.get("url"), plan.cover_fetched.get("tried")):
            return data
        new = _download_cover(plan.cover_url, source)
        plan.cover_fetched["tried"] = plan.cover_url
        if not new:
            return data
        existing.unlink()
        return _save_cover(plan, album_dir, *new)

    if not fetch:
        return None
    why: list[str] = []
    addresses = [url for url in _cover_addresses(plan, album_dir) if url]
    for url in addresses:
        if found := _download_cover(url, source, why):
            return _save_cover(plan, album_dir, *found)
    # **an album of somebody's own whose files hold no picture is not a problem to report.** Most of
    # them do not: 269 of 2000 files in the reference collection carry one, so a take-in of 700
    # albums would warn about hundreds of them. A published address that failed still warns.
    published = [url for url in addresses if album_dir is None or url != str(album_dir)]
    if published and (plan.cover_url or _asks_its_source(plan)):
        log.warning("could not fetch any cover for %s%s", plan.cover_url or plan.source_url,
                    f" — {'; '.join(dict.fromkeys(why))}" if why else "")
    elif addresses:
        log.debug("no picture in the files of %s", album_dir)
    return None


def _asks_its_source(plan: AlbumPlan) -> bool:
    """Whether this album's cover has to be asked for rather than read off the plan (§9, slice 73).

    A private album's plan holds no address — that is the rule the plan is written under — so without
    this it could never get a cover at all: the first fetch had one in memory and every later run had
    nothing to try. Only for private sources, because for everybody else the plan's address is the
    address, and handing a collection's own page to `art()` would be a request nobody asked for.
    """
    from . import sources

    return bool(plan.source_url) and sources.private(plan.provider)


def _cover_addresses(plan: AlbumPlan, album_dir: Path | None = None) -> list[str]:
    """What to try, in order: what the plan holds, and — for some albums — its source itself.

    The provider answers a collection address by reading it and taking the image out of that read, so
    the signed address exists for the length of one request and is never written down. It happens only
    while a fetch or an update is running for that album anyway; there is no pass of its own.

    **An adopted album's address is its own folder** (§9, slice 101). Its plan holds no cover url —
    nobody published it — so without this an album somebody already owns could never be given a
    cover beside it, however the library is set. Asking its own source for it reads the files that
    are already there and reaches nobody: measured on the reference collection, 269 of 2000 files
    carry a picture.
    """
    found = [plan.cover_url, plan.cover_fallback_url]
    if not any(found) and _asks_its_source(plan):
        found.append(plan.source_url)
    if not any(found) and plan.adopted and album_dir is not None:
        found.append(str(album_dir))
    return [url for url in found if url]


def _download_cover(url: str, source: Source, why: list[str] | None = None) -> tuple[str, bytes] | None:
    """The cover bytes, or None — and `why` collects the reasons, because *could not* is not a reason.

    The live Patreon fetch warned `could not fetch any cover` and the reason was at debug level,
    where nobody saw it; the address turned out to be good and the request shape wrong (§9, slice 73).
    """
    for candidate in cover_candidates(url, source):
        try:
            data = source.art(candidate)
        except Exception as e:  # a missing cover must never stop the album
            log.debug("cover %s not available: %s", candidate, e)
            if why is not None:
                why.append(str(e).strip().splitlines()[0][:160] if str(e).strip() else type(e).__name__)
            continue
        if image_mime(data):
            return url, data
    return None


def _named_for_what_it_is(path: Path, data: bytes) -> Path:
    """A cover file renamed to the suffix its own bytes say, where the two disagree (§9, slice 127).

    `cover.jpg` holding a GIF is three albums of the user's. The picture is theirs and is kept
    exactly as it is; only the name is corrected, and only where the target is free — a `cover.gif`
    already beside it is somebody's decision and not this function's to overwrite.
    """
    want = image_mime(data)
    if not want:
        return path
    ext = want.split("/")[1].replace("jpeg", "jpg")
    if path.suffix.lower() == f".{ext}":
        return path
    target = path.with_name(f"{path.stem}.{ext}")
    if target.exists():
        return path
    try:
        path.rename(target)
    except OSError as e:
        log.warning("could not rename %s to %s: %s", path.name, target.name, e)
        return path
    log.info("%s is a %s and is now called %s", path.name, want, target.name)
    return target


def _save_cover(plan: AlbumPlan, album_dir: Path, url: str, data: bytes) -> bytes:
    data = square_if_padded(data) or data  # pillarboxed YouTube thumbnails -> the square art
    ext = image_mime(data).split("/")[1].replace("jpeg", "jpg")
    album_dir.mkdir(parents=True, exist_ok=True)
    (album_dir / f"{COVER_STEM}.{ext}").write_bytes(data)
    plan.cover_fetched = {"url": url, "sha1": _sha1(data)}
    return data


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()
