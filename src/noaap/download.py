"""Stages 6-7: execute an AlbumPlan. The plan file doubles as the progress manifest.

Running a plan is idempotent: finished tracks are only renamed/retagged when the plan
changed, missing ones are downloaded, and the plan is saved after every track.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from mutagen import MutagenError

from .cover import square_if_padded
from .lyrics import LyricsAPI, reconcile, rename_sidecar, update_track
from .models import AlbumPlan, Failure, PlanTrack
from .plan import refresh_derived, wanted_filename, wanted_folder
from .sources import Blocked, NoAudio, Source, SourceError
from .tag import audio_quality, image_mime, measure, signature, tag_file
from .trim import apply as apply_trim

log = logging.getLogger(__name__)

PLAN_FILE = ".ytalbum.json"
PARTS_DIR = ".parts"
COVER_STEM = "cover"
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
    """What is written: a path inside this album becomes `./…`, everything else is untouched."""
    if isinstance(value, dict):
        return {k: portable(v, album_dir) for k, v in value.items()}
    if isinstance(value, list):
        return [portable(v, album_dir) for v in value]
    if not isinstance(value, str) or not value.startswith("/"):
        return value
    where = Path(value)
    if where == album_dir:
        return HERE
    return f"{HERE}{where.relative_to(album_dir)}" if album_dir in where.parents else value


def resolved(value: Any, album_dir: Path) -> Any:
    """What is read: `./…` becomes the file it names in this folder."""
    if isinstance(value, dict):
        return {k: resolved(v, album_dir) for k, v in value.items()}
    if isinstance(value, list):
        return [resolved(v, album_dir) for v in value]
    if not isinstance(value, str) or not value.startswith(HERE):
        return value
    rest = value[len(HERE):]
    return str(album_dir / rest) if rest else str(album_dir)


def load_plan(album_dir: Path) -> AlbumPlan | None:
    path = album_dir / PLAN_FILE
    if not path.exists():
        return None
    return AlbumPlan.from_dict(resolved(json.loads(path.read_text()), album_dir))


def written(plan: AlbumPlan, album_dir: Path) -> dict[str, Any]:
    """The exact object a save puts in the file — **the one place that decides it** (§9, slice 60).

    `plan --verify` has to compare a file against what a save would write, and `repair` has to tell
    whether a save would change it; both ask here rather than rebuilding the rule, because a second
    copy of it is a second thing to get wrong.
    """
    out = portable(plan.to_dict(), album_dir)
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


def iter_plans(library: Path) -> Iterator[tuple[Path, AlbumPlan]]:
    """Every album folder in the library (<library>/<artist>/<album>/.ytalbum.json)."""
    for path in sorted(library.glob(f"*/*/{PLAN_FILE}")):
        try:
            yield path.parent, AlbumPlan.from_dict(resolved(json.loads(path.read_text()), path.parent))
        except (ValueError, KeyError, TypeError) as e:
            log.warning("ignoring unreadable plan %s: %s", path, e)


def find_plan(library: Path, source_id: str) -> tuple[Path, AlbumPlan] | None:
    """The album folder already made from this source, wherever the user renamed it to."""
    return next(((d, p) for d, p in iter_plans(library) if p.source_id == source_id), None)


def relocate(album_dir: Path, plan: AlbumPlan, library: Path) -> Path:
    """Move the album folder to where the (edited) plan says it belongs. Never overwrites.

    An album that keeps its names stays where its owner put it (§9, slice 58).
    """
    if plan.keep_names:
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


def run(
    plan: AlbumPlan,
    album_dir: Path,
    source: Source,
    # A track's audio comes from its chosen candidate, which carries its own provider: one album can
    # hold tracks from two of them (§9, slice 50). `source` stays for what belongs to the collection
    # — the cover — and this answers for a track.
    track_source: Callable[[PlanTrack], Source] | None = None,
    on_track: Callable[[PlanTrack, str], None] = lambda t, what: None,
    check: Callable[[], None] = lambda: None,
    download: bool = True,
    lyrics: LyricsAPI | None = None,
) -> AlbumPlan:
    """Download, tag and place every track that is not done yet; rename/retag finished ones.

    `check()` is called between tracks and may raise to stop (cancel); the plan is always saved.
    With a `lyrics` client, tracks that were never looked up get their `.lrc` sidecar here.
    """
    refresh_derived(plan)
    save_plan(plan, album_dir)
    whose = track_source or (lambda _t: source)
    cover = _cover(plan, album_dir, source, fetch=download)
    parts = album_dir / PARTS_DIR

    for track in plan.tracks:
        check()
        wanted = track.filename if plan.keep_names else wanted_filename(plan, track)
        if track.state == "done" and track.filename != wanted:
            old, new = album_dir / track.filename, album_dir / wanted
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
            try:
                if plan.keep_tags:
                    # nothing is written into this file by any pass (§9, slice 58). The words still
                    # arrive as a sidecar beside it; the tag inside the file is the owner's.
                    if looked_up or measured or failed_trim or reconciled:
                        save_plan(plan, album_dir)
                elif track.tagged != signature(plan, track, cover, text):
                    track.tagged = tag_file(final, plan, track, cover, text)
                    save_plan(plan, album_dir)
                    on_track(track, f"lyrics ({track.lyrics})" if looked_up and text else "retagged")
                elif looked_up or measured or failed_trim or reconciled:
                    save_plan(plan, album_dir)  # the lookup, the length, the owner, or why the trim did not happen
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

        if track.channel is None:
            _follow_source(whose, track)  # whose upload this really is, and how long it runs
        for attempt in range(1, ATTEMPTS + 1):
            try:
                tmp = whose(track).audio(track.effective_id, parts, track.audio_choice)
                # **the file decides what it is.** `ext` was only ever set from a YouTube audio
                # choice — opus, or m4a for a combined stream — so a provider that hands over an
                # mp3 or a flac would have had it filed under `.opus`, tagged as Opus (which
                # raises) and left as a name that lies about its contents (§9, slice 53).
                if (got := tmp.suffix.lstrip(".").lower()) and got != track.ext:
                    track.ext = got
                    # **an adopted album keeps its name here too** (§9, slice 61): only the suffix
                    # follows the file, because deriving the whole name is the one thing adoption
                    # promised not to do — and it would move the file out of its own folder.
                    track.filename = str(Path(track.filename).with_suffix(f".{got}")) \
                        if plan.keep_names else wanted_filename(plan, track)
                    final = album_dir / track.filename
                text = update_track(lyrics, plan, track, album_dir, tmp) if lyrics else None
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
    existing = next((p for p in sorted(album_dir.glob(f"{COVER_STEM}.*")) if image_mime(p.read_bytes())), None)
    if existing:
        data = existing.read_bytes()
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
    for url in filter(None, (plan.cover_url, plan.cover_fallback_url)):
        if found := _download_cover(url, source):
            return _save_cover(plan, album_dir, *found)
    if plan.cover_url:
        log.warning("could not fetch any cover for %s", plan.cover_url)
    return None


def _download_cover(url: str, source: Source) -> tuple[str, bytes] | None:
    for candidate in cover_candidates(url, source):
        try:
            data = source.art(candidate)
        except Exception as e:  # a missing cover must never stop the album
            log.debug("cover %s not available: %s", candidate, e)
            continue
        if image_mime(data):
            return url, data
    return None


def _save_cover(plan: AlbumPlan, album_dir: Path, url: str, data: bytes) -> bytes:
    data = square_if_padded(data) or data  # pillarboxed YouTube thumbnails -> the square art
    ext = image_mime(data).split("/")[1].replace("jpeg", "jpg")
    album_dir.mkdir(parents=True, exist_ok=True)
    (album_dir / f"{COVER_STEM}.{ext}").write_bytes(data)
    plan.cover_fetched = {"url": url, "sha1": _sha1(data)}
    return data


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()
