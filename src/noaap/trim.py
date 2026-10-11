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


def ref_key(ref: str, album_dir: Path | None = None) -> str:
    """The name a kept original carries for this ref.

    **A folder's ref is a path, and only the part inside the album says which file it is** (§9, slice 168; R-578).
    Keyed by the whole path, an original belonged to one place on one machine: the walk's copy of
    `Ever Dream` re-pointed `…/02 - Phantom Of The Opera.mp3` at `…/02 - Phantom of the Opera.mp3`,
    the key changed with it, and the cut file could never be put back. A library moved, remounted or
    renamed did the same to every original it had. A ref that is not a path is keyed as it was.
    """
    text = str(ref)
    if text.startswith("./"):
        return key(text[2:])
    if text.startswith("/"):
        # inside the album: the path from the album down (a disc folder is part of the name); a file
        # somewhere else entirely keeps its whole path, which is what it is
        for root in ((album_dir, album_dir.resolve()) if album_dir else ()):
            try:
                return key(str(Path(text).relative_to(root)))
            except ValueError:
                continue
        return key(text)
    return key(text)


def original_path(album_dir: Path, track: PlanTrack) -> Path:
    """Where this track's untouched download is kept, in the track's own format.

    Keyed by the **effective** id: a track pointed at another video (§9, slice 34) holds another
    recording, and the two must never be cut from each other's original.
    """
    return album_dir / ORIGINALS / f"{ref_key(track.effective_id, album_dir)}.{track.ext}"


def originals_of(album_dir: Path, ref: str) -> list[Path]:
    """Every original kept for one ref, whatever format it was taken in — and under the key the ref
    had before slice 168, which was the whole path."""
    folder = album_dir / ORIGINALS
    if not folder.is_dir():
        return []
    names = {ref_key(ref, album_dir), key(str(ref))}
    return sorted(p for name in names for p in folder.glob(f"{name}.*"))


def adopt_legacy(album_dir: Path, track: PlanTrack) -> bool:
    """An original kept under its track's whole path is given the name it has now (§9, slice 168)."""
    now = original_path(album_dir, track)
    was = album_dir / ORIGINALS / f"{key(track.effective_id)}.{track.ext}"
    if was == now or now.exists() or not was.exists():
        return False
    was.rename(now)
    return True


def follow(album_dir: Path, was: str, now: str) -> int:
    """A track's id changed: its kept originals change their name with it (§9, slice 168; R-578).

    Every pass that re-points a track — `point_at`, `find_again`, a source folder found again — calls
    this, so an original is never left under a name no track has. Returns how many moved."""
    moved = 0
    for kept in originals_of(album_dir, was):
        target = kept.with_name(f"{ref_key(now, album_dir)}{kept.suffix}")
        if target != kept and not target.exists():
            kept.rename(target)
            moved += 1
    return moved


def pcm_digest(path: Path) -> str | None:
    """What a file sounds like, as a digest of its decoded samples — the same for two cuts of one
    original at the same points, whatever container wrote them."""
    try:
        done = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "2", "-ar", "44100", "-"],
                              capture_output=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return hashlib.sha256(done.stdout).hexdigest() if done.returncode == 0 and done.stdout else None


def unowned(album_dir: Path, tracks: list[PlanTrack]) -> list[Path]:
    """The kept originals no track of this album has a name for."""
    folder = album_dir / ORIGINALS
    if not folder.is_dir():
        return []
    owned = {kept for t in tracks for ref in {t.effective_id, t.video_id, *(c.ref for c in t.candidates)}
             for kept in originals_of(album_dir, ref)}
    return sorted(p for p in folder.iterdir() if p.is_file() and p not in owned)


def cut_from(original: Path, track: PlanTrack, file: Path) -> bool:
    """Whether `file` is this original cut at the track's recorded points: the same samples."""
    start, _, end = (track.trimmed or "").partition("-")
    if not start:
        return False
    probe = file.with_name(f".noaap-probe{file.suffix}")
    command = ["ffmpeg", "-v", "error", "-y", "-i", str(original), "-ss", start]
    if end:
        command += ["-to", end]
    command += [*(["-c:a", "flac"] if track.ext == "flac" else ["-c", "copy"]),
                "-avoid_negative_ts", "make_zero", str(probe)]
    try:
        if subprocess.run(command, capture_output=True, timeout=600).returncode != 0:
            return False
        ours = pcm_digest(file)
        return ours is not None and ours == pcm_digest(probe)
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        probe.unlink(missing_ok=True)


def re_own(album_dir: Path, tracks: list[PlanTrack], apply: bool) -> list[str]:
    """Kept originals no track owns: named, and given back where exactly one cut track is that original
    cut at its own points (§9, slice 168; R-578). One line per original, for the repair to print."""
    said: list[str] = []
    waiting = [t for t in tracks if t.trimmed and t.filename and not original_path(album_dir, t).exists()
               and (album_dir / t.filename).is_file()]
    for kept in unowned(album_dir, tracks):
        fits = [t for t in waiting if kept.suffix == f".{t.ext}" and cut_from(kept, t, album_dir / t.filename)]
        if len(fits) == 1:
            track = fits[0]
            if apply:
                kept.rename(original_path(album_dir, track))
                waiting.remove(track)
            said.append(f"{track.number:02d} {'took back' if apply else 'would take back'} its kept original "
                        f"{kept.name} — the file is that original cut at {track.trimmed}")
        else:
            said.append(f"kept original without a track: {kept.name}"
                        + (f" — {len(fits)} tracks could be its cut, so it is left" if fits else ""))
    return said


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
    adopt_legacy(album_dir, track)
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
