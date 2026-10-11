"""Cut intros and outros off a track — losslessly, and always from the untouched original.

The trim points live in the plan; the file on disk is produced from `.originals/<id>.<ext>`.
Clearing the trim restores the original byte for byte.

The original is kept in the track's **own** format. It used to be named `.opus` whatever the
track was, so a track switched to the combined stream was re-cut from the previous format's
original: ffmpeg copied Opus into a file named `.m4a` and the tagger then choked on it
(DESIGN.md §9, slice 20). An original whose format does not match the track is never used as a
source — and never invented either: it is only replaced when the file on disk is still the
untouched download.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
from pathlib import Path

import mutagen
from mutagen import MutagenError

from .models import PlanTrack

log = logging.getLogger(__name__)

ORIGINALS = ".originals"
RETAKE = "switch the track's audio source to take it again"


#: how long to wait for ffprobe to answer one question about one file
ASKING = 30.0


def starts_before_zero(path: Path) -> float | None:
    """The file's own start time when it is **negative**, else None (§9, slice 86).

    `ffmpeg -ss … -i … -c copy` keeps the packets before the cut point and marks them with negative
    stamps instead of starting the file at zero: every front-cut file in the user's library reads
    `start_time = -0.900000` for a 4.9 s trim, minus the fraction of the trim point. A player then has
    a clock that runs past the duration it was told, and the track that follows one of these starts
    in its own middle (`docs/qa-catalog.md`, BZ). This is how such a file is recognised again.
    """
    try:
        done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=start_time",
                               "-of", "default=nw=1:nk=1", str(path)],
                              capture_output=True, text=True, errors="replace", timeout=ASKING)
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        start = float((done.stdout or "").strip())
    except ValueError:
        return None
    return start if start < 0 else None


def signature(track: PlanTrack) -> str:
    """What the file should be cut to; '' means untouched."""
    if track.trim_start is None and track.trim_end is None:
        return ""
    return f"{track.trim_start or 0:.2f}-{'' if track.trim_end is None else f'{track.trim_end:.2f}'}"


SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def key(ref: str) -> str:
    """A ref as a file name.

    A YouTube id is already one, which is why this was never needed — but **a ref is opaque** and a
    folder's is a path on this machine. Used as a name it would mean sub-folders; read back as a
    glob pattern it raises `Non-relative patterns are unsupported` **in the middle of moving a
    file**, leaving audio in a bin entry that was never finished and that nothing lists
    (found 2026-09-28). Anything not plainly a name is replaced by a digest of it, so a library
    written before this keeps every original it had (§9, slice 55).
    """
    return ref if ref and set(ref) <= SAFE else "ref-" + hashlib.sha256(ref.encode()).hexdigest()[:16]


def original_path(album_dir: Path, track: PlanTrack) -> Path:
    """Where this track's untouched download is kept, in the track's own format.

    Keyed by the **effective** id: a track pointed at another video (§9, slice 34) holds another
    recording, and the two must never be cut from each other's original.
    """
    return album_dir / ORIGINALS / f"{key(track.effective_id)}.{track.ext}"


def originals_of(album_dir: Path, ref: str) -> list[Path]:
    """Every original kept for one ref, whatever format it was taken in."""
    folder = album_dir / ORIGINALS
    return sorted(folder.glob(f"{key(ref)}.*")) if folder.is_dir() else []


def kept_originals(album_dir: Path, track: PlanTrack) -> list[Path]:
    """Every original kept for this track, whatever format it was taken in."""
    return originals_of(album_dir, track.effective_id)


#: the containers a file of each extension may be (mutagen's class names). **Every extension a
#: library holds, not the two noaap downloads** (§9, slice 166; I-432): this knew only MP4 and
#: Ogg Opus, so the kept original of every adopted mp3 and flac read as "not mp3" — the first cut
#: worked, because no original existed yet, and every later change or reset of that trim refused.
CONTAINERS = {
    "mp3": ("MP3",), "flac": ("FLAC",), "m4a": ("MP4",), "mp4": ("MP4",), "aac": ("AAC", "MP4"),
    "opus": ("OggOpus",), "ogg": ("OggVorbis", "OggOpus", "OggFLAC"), "oga": ("OggVorbis", "OggOpus", "OggFLAC"),
    "wav": ("WAVE",), "aif": ("AIFF",), "aiff": ("AIFF",), "wma": ("ASF",), "ape": ("MonkeysAudio",),
    "wv": ("WavPack",), "mpc": ("Musepack",),
}


def holds(path: Path, ext: str) -> bool:
    """Is this file really the format its name claims? A copy is not proof of a container."""
    try:
        found = mutagen.File(path)
    except (MutagenError, OSError):
        return False
    if found is None:
        return False
    wanted = CONTAINERS.get(ext.lower().lstrip("."))
    # an extension this table does not know is held by any audio mutagen can read
    return wanted is None or type(found).__name__ in wanted


def apply(album_dir: Path, track: PlanTrack, path: Path) -> bool:
    """Bring `path` in line with the track's trim points. True if the file changed."""
    wanted = signature(track)
    if wanted == (track.trimmed or ""):
        if not wanted or starts_before_zero(path) is None:
            return False
        # cut to the right points by an older version, and left with a clock that starts before zero
        original = original_path(album_dir, track)
        if not original.exists() or not holds(original, track.ext):
            log.warning("%s: its clock starts before zero and no untouched original is kept, so it "
                        "cannot be cut again: %s", path.name, RETAKE)
            return False
        track.trimmed = None   # so the cut below runs: the file on disk is not what it should be
    original = original_path(album_dir, track)
    if original.exists() and not holds(original, track.ext):
        # a copy that is not what its name says — a leftover from another format
        log.warning("%s: the kept original is not %s", path.name, track.ext)
        if not track.trimmed:
            original.unlink()  # the file on disk is untouched, so a fresh one can be taken below
    if not original.exists() or not holds(original, track.ext):
        if track.trimmed:
            # The file on disk is cut and nothing holds what it was cut from. Copying it would
            # not give an untouched original but a shorter one, and *clearing* the trim cannot
            # put back what is no longer kept — so neither direction may claim to have worked.
            raise RuntimeError(
                f"no untouched {track.ext} original is kept for this track and the file on disk is "
                f"already cut, so it can be neither cut again nor put back: {RETAKE}"
            )
        if not wanted:  # nothing wanted, and nothing was ever cut
            track.trimmed = None
            return False
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, original)
        for stale in kept_originals(album_dir, track):  # the previous format's copy is dead weight
            if stale != original:
                stale.unlink()

    if not wanted:  # back to the untouched original
        shutil.copy2(original, path)
        track.trimmed = None
        return True

    cut = path.with_suffix(f".trim{path.suffix}")  # the cut keeps the track's own container
    # **`-ss` after `-i`, and a clock that starts at zero** (§9, slice 86). Before `-i` it is a fast
    # input seek: ffmpeg keeps the packets before the cut point and marks them negative, so the file
    # holds audio the user cut away and every player's clock is offset by the fraction of the trim
    # point — measured at `start_time = -0.900000` for a 4.9 s trim, on every front-cut file in the
    # user's library. After `-i` the packets before the point are dropped, and `make_zero` puts the
    # first remaining stamp at 0.000. The cost is the one packet the point falls inside: 20 ms of
    # Opus, kept rather than lost, which is the same accuracy `-c copy` always had.
    command = ["ffmpeg", "-v", "error", "-y", "-i", str(original),
               "-ss", f"{track.trim_start or 0:.3f}"]
    if track.trim_end is not None:
        # still the original's own timeline: `-to` is absolute even when `-ss` is an output option
        command += ["-to", f"{track.trim_end:.3f}"]
    # **FLAC is encoded again, which for FLAC is lossless** (§9, slice 166): a stream copy leaves the
    # original's STREAMINFO, so a cut of 4.9 s still says 10 s to every player that reads the header
    codec = ["-c:a", "flac"] if track.ext == "flac" else ["-c", "copy"]
    command += [*codec, "-avoid_negative_ts", "make_zero", str(cut)]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, errors="replace")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        cut.unlink(missing_ok=True)
        raise RuntimeError(f"could not trim: {getattr(e, 'stderr', e)}".strip()) from e
    cut.replace(path)
    track.trimmed = wanted
    return True
