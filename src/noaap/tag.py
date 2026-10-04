"""Stage 7: write tags and an embedded cover into an audio file.

Four containers, because four is what a library holds: Opus and MP4 come from YouTube, FLAC and MP3
come from a folder someone has been filling for years (§9, slice 53). The tag *names* are Vorbis
comments throughout — `build_tags` speaks one vocabulary and each writer translates.

**Reading a file this module cannot parse must not look like an empty file.** Until 1.0.0 every
non-MP4 path opened as Opus, so a `.flac` raised `MutagenError`, was caught, and `audio_length`
answered `None` — a length that reads as "no match" and a quality that reads as "unknown". Nothing
failed; the numbers were simply absent. That is why the suffix dispatch below is one function used
by all four entry points rather than a condition repeated in each.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

import mutagen
from mutagen import MutagenError
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, COMM, ID3, PRIV, TCMP, TXXX, USLT, ID3NoHeaderError
from mutagen.id3 import Frames as ID3_FRAMES
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus

from .models import AlbumPlan, PlanTrack, Provenance
from .sources import DEFAULT as DEFAULT_PROVIDER

PICTURE_KEY = "metadata_block_picture"

MP4_SUFFIXES = (".m4a", ".mp4")
CODECS = {"mp4": "aac", "flac": "flac", "mp3": "mp3", "opus": "opus"}

# how long any one file may take to answer. A damaged file can keep ffmpeg busy for ever, and a
# pass over thousands of them must not stop for one of them (§9, slice 54).
PATIENCE = 120


def kind(path: Path) -> str:
    """Which container this is, by name. The only place a suffix decides anything."""
    suffix = path.suffix.lower()
    if suffix in MP4_SUFFIXES:
        return "mp4"
    if suffix == ".flac":
        return "flac"
    if suffix == ".mp3":
        return "mp3"
    return "opus"


def _open(path: Path, through: Any = None) -> Any:
    """The mutagen object for reading. Raises exactly what mutagen raises.

    `through` is an open file handle to read it from instead of opening the path again — see
    `reading`. mutagen answers identically either way; checked for all four containers.
    """
    kinds = {"mp4": MP4, "flac": FLAC, "mp3": MP3, "opus": OggOpus}
    if through is None:
        return kinds[kind(path)](path)
    through.seek(0)
    return kinds[kind(path)](through)


class Reading:
    """One open of a file, answering every question a pass asks about it (§9, slice 105).

    **Measured on a share, where opening a file again is not free**: reading an album to adopt it
    opened every file **four** times — `read_tags`, `audio_length`, and twice inside `audio_quality`
    — and a dry run of `take-in` over the user's own collection therefore read **2.7 times the whole
    collection**, 80.75 GB for 41 GB of music, where the dry run's own work is 2.3 MB of an album.
    With `actimeo=1` a share re-reads on each open; locally the page cache hid it entirely.

    One handle, seeked back between reads, so the kernel serves the repeats and nothing is read that
    was not asked for. The handle is closed when the `with` block ends; the mutagen objects are not
    used for saving, only for reading.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: Any = None
        self._typed: Any = None
        self._easy: Any = None
        self._easy_read = False

    def __enter__(self) -> Reading:
        try:
            self._fh = self.path.open("rb")
        except OSError:
            self._fh = None
        return self

    def __exit__(self, *_: Any) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    @property
    def typed(self) -> Any:
        """The container's own mutagen class, for `info` and for the tags as they are written."""
        if self._typed is None:
            self._typed = _open(self.path, self._fh)
        return self._typed

    @property
    def easy(self) -> Any:
        """mutagen's `easy` view, which is one vocabulary for all four containers."""
        if not self._easy_read:
            self._easy_read = True
            if self._fh is None:
                self._easy = mutagen.File(self.path, easy=True)
            else:
                self._fh.seek(0)
                self._easy = mutagen.File(self._fh, easy=True)
        return self._easy


def reading(path: Path) -> Reading:
    """`with reading(path) as one:` — then `one.typed` and `one.easy`, from a single open."""
    return Reading(path)


def image_mime(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def audio_length(path: Path, one: Reading | None = None) -> float | None:
    """Seconds of audio in the file — the trimmed truth, not what a source said.

    **A zero is not a length.** Three albums in the reference collection are 24-bit FLACs whose
    STREAMINFO carries `total_samples = 0`, which some encoders leave behind when they cannot seek
    back to fill it in. mutagen reports that faithfully as `length = 0.0`, and a track "shorter
    than 30s" is dropped as an intro card — so 52 real tracks of a real collection were refused
    with "all 13 videos are unusable". Unknown is what this is, and unknown is what it now says.
    """
    try:
        return float((one.typed if one else _open(path)).info.length) or None
    except (MutagenError, OSError, KeyError):  # not readable, not audio: unknown means "no match"
        return None  # and nothing else is swallowed: a bug here must not read as a missing file


TIMESTAMP = re.compile(r"time=(\d+):(\d\d):(\d\d(?:\.\d+)?)")


def decoded_length(path: Path) -> float | None:
    """Seconds, counted by reading the audio — for a file whose header will not say.

    Three albums in the reference collection are FLACs with `total_samples = 0`, and ffprobe cannot
    answer for them either without doing this. About 0.12 s a file, so it is worth asking whenever
    the cheap answer is missing rather than deciding in advance who might need it.
    """
    try:
        done = subprocess.run(["ffmpeg", "-v", "error", "-stats", "-i", str(path), "-f", "null", "-"],
                              capture_output=True, text=True, errors="replace", timeout=PATIENCE)
    except (OSError, subprocess.SubprocessError):  # including a timeout: no answer is an answer
        return None
    if not (found := TIMESTAMP.findall(done.stderr)):
        return None
    hours, minutes, seconds = found[-1]
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def embedded_cover(path: Path) -> bytes | None:
    """The picture inside a file, for a folder that has no cover of its own."""
    import mutagen
    from mutagen.flac import FLAC
    from mutagen.id3 import ID3NoHeaderError

    try:
        if path.suffix.lower() == ".flac":
            pictures = FLAC(path).pictures
            return pictures[0].data if pictures else None
        if path.suffix.lower() == ".mp3":
            from mutagen.id3 import ID3
            try:
                art = ID3(path).getall("APIC")
            except ID3NoHeaderError:
                return None
            return art[0].data if art else None
        import base64

        from mutagen.flac import Picture
        if path.suffix.lower() in (".m4a", ".mp4", ".m4b"):
            from mutagen.mp4 import MP4
            art = (MP4(path).tags or {}).get("covr")
            return bytes(art[0]) if art else None
        tags = (mutagen.File(path).tags or {}) if mutagen.File(path) else {}
        if raw := tags.get("metadata_block_picture"):
            return Picture(base64.b64decode(raw[0])).data
    except Exception:  # an unreadable picture is no picture; it must not stop the album
        return None
    return None


def set_picture(path: Path, data: bytes | None) -> bool:
    """Put this picture inside the file, or — with `None` — take out whatever is in there.

    **The other half of `embedded_cover`, and a restore needs it** (§9, slice 104). A snapshot records
    the text tags a writer could touch and `restore_tags` puts those back; the picture is written by
    the same writers and was in none of their key lists, so a restore left a cover the pass had
    embedded sitting in somebody's file — 23 of 36 files in the reviewer's own run. Says whether it
    changed anything.
    """
    from mutagen import File as Any_
    from mutagen.flac import FLAC
    from mutagen.id3 import APIC, ID3
    from mutagen.mp4 import MP4, MP4Cover

    mime = image_mime(data) if data else ""
    if data and not mime:
        return False
    try:
        kind_ = kind(path)
        if kind_ == "flac":
            audio = FLAC(path)
            if not audio.pictures and data is None:
                return False
            audio.clear_pictures()
            if data:
                audio.add_picture(_picture(data, mime))
            audio.save()
            return True
        if kind_ == "mp3":
            try:
                id3 = ID3(path)
            except Exception:
                id3 = ID3()
            had = bool(id3.getall("APIC"))
            if not had and data is None:
                return False
            id3.delall("APIC")
            if data:
                id3.setall("APIC", [APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data)])
            id3.save(path)
            return True
        if kind_ == "mp4":
            audio = MP4(path)
            had = bool((audio.tags or {}).get("covr"))
            if not had and data is None:
                return False
            if data:
                fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
                audio["covr"] = [MP4Cover(data, imageformat=fmt)]
            else:
                audio.pop("covr", None)
            audio.save()
            return True
        audio = Any_(path)                      # opus, ogg: a base64 block in the comments
        if audio is None or audio.tags is None:
            return False
        had = PICTURE_KEY in audio.tags
        if not had and data is None:
            return False
        if data:
            audio[PICTURE_KEY] = [base64.b64encode(_picture(data, mime).write()).decode("ascii")]
        else:
            audio.pop(PICTURE_KEY, None)
        audio.save()
        return True
    except (MutagenError, OSError):
        return False


def decoder() -> str:
    """Which decoder produced an identity, so that two are only ever compared on equal terms.

    A lossy format has no one right answer: another build of ffmpeg may decode an mp3 or an Opus file
    to other samples. So an identity carries its maker, and where two makers differ the older identity
    is **measured again, never trusted** (R-227, ruling 1).
    """
    try:
        done = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True,
                              errors="replace", timeout=PATIENCE)
    except (OSError, subprocess.SubprocessError):
        return "decoded/unknown"
    first = (done.stdout or "").splitlines()[0] if done.stdout else ""
    version = first.split()[2] if len(first.split()) > 2 else "unknown"
    return f"decoded/ffmpeg {version}"


def decoded_sha(path: Path) -> str | None:
    """A digest of this file's **decoded** audio, or None where ffmpeg cannot be asked (§9, slice 63).

    **This is what "the same recording" means here** (§9, slice 66), and the measurement that decided
    it is worth keeping: `sources_folder.stream_sha` copies the stream instead of decoding it, which is
    three times cheaper and agrees with this over every untouched file of the reference collection — but
    it called **14 of 22** copies of one real album different from the files they were copied from. The
    cause, measured to the byte: 7699 of 7700 packets identical, and the last one 52 bytes against 180,
    the difference being exactly the **128-byte trailing ID3v1 tag** that ffmpeg's demuxer hands over as
    audio data and that mutagen dropped when noaap tagged the copy. A digest of packets answers "the
    same file, trailing tags and all"; only a decode answers "the same recording".
    """
    out = ffmpeg_audio(path, "s16le")
    return hashlib.sha256(out).hexdigest() if out else None


def ffmpeg_audio(path: Path, shape: str, copy: bool = False) -> bytes | None:
    """ffmpeg's audio of this file in `shape`, **without a trailing ID3v1 tag** (R-433, ruling 2).

    The demuxer hands that 128-byte block to the decoder as if it were audio — measured on the
    user's own `Fan The Fire`, 10,123,878 bytes: change nothing but its last 128 bytes and the
    decoded PCM differs from 23.186 ms before the end, one mp3 frame, while staying the same length.
    That is how a digest which exists to be invariant under a retag came to move under one, and how
    a six-batch pass over the collection ended in a refusal an hour in.

    **An mp3 is therefore always piped in, tail or no tail, and nothing else ever is.** The two
    routes do not agree on mp3: over a pipe ffmpeg applies no gapless trimming, so a one-second tone
    read from its file decodes to 88,200 bytes of PCM and the same bytes piped in decode to 89,950.
    Piping while the tail is there and reading once it is gone would be no digest of the audio at
    all — it would refuse every correct write to a file with a LAME header, which is this same bug
    in other clothes. For opus, flac and m4a the two routes agree to the byte (measured on the
    user's library), and an mp4's index is at its end, so those are read from the file.
    """
    try:
        how = ["-c", "copy"] if copy else []
        if path.suffix.lower() != ".mp3":
            done = subprocess.run(["ffmpeg", "-v", "quiet", "-i", str(path), "-map", "0:a",
                                   *how, "-f", shape, "-"], capture_output=True, timeout=PATIENCE)
            return done.stdout if done.returncode == 0 and done.stdout else None
        return _piped_without_the_tail(path, shape, how)
    except (OSError, subprocess.SubprocessError):
        return None


def _piped_without_the_tail(path: Path, shape: str, how: list[str] | None = None) -> bytes | None:
    """The file written to ffmpeg's stdin in chunks, less a trailing ID3v1 block if it has one."""
    size = path.stat().st_size - (128 if _id3v1_tail(path) else 0)
    proc = subprocess.Popen(["ffmpeg", "-v", "quiet", "-i", "pipe:0", "-map", "0:a",
                             *(how or []), "-f", shape, "-"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    chunks: list[bytes] = []

    def feed() -> None:
        left = size
        try:
            with path.open("rb") as fh:
                while left > 0 and (block := fh.read(min(1 << 20, left))):
                    proc.stdin.write(block)
                    left -= len(block)
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(OSError, ValueError):
                proc.stdin.close()

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    try:
        while block := proc.stdout.read(1 << 20):
            chunks.append(block)
    finally:
        proc.stdout.close()
        proc.wait(timeout=PATIENCE)
        writer.join(timeout=PATIENCE)
    out = b"".join(chunks)
    return out if proc.returncode == 0 and out else None


def measure(path: Path) -> tuple[float | None, str | None]:
    """Seconds, and **how they were arrived at** — `"header"` or `"decoded"` (§9, slice 56).

    Worth recording because the two cost three orders of magnitude apart and because a decoded
    answer means the file's own header would not say, which is a fact about the file: the three
    24-bit FLAC albums in the reference collection are the only ones in 2000 files that need it.
    """
    if (said := audio_length(path)) is not None:
        return said, "header"
    if (heard := decoded_length(path)) is not None:
        return heard, "decoded"
    return None, None


def measured_length(path: Path) -> float | None:
    """What the file says, and what it sounds like when it will not say (§9, slice 53)."""
    return measure(path)[0]


def audio_quality(path: Path, one: Reading | None = None) -> dict[str, Any]:
    """Codec, bitrate, sample rate and channels — what ranking will compare (§9, slice 50).

    Measured from the file, never from what a source claimed. Empty when the file cannot be read,
    because an unknown quality must not read as a bad one.
    """
    try:
        info = (one.typed if one else _open(path)).info
    except (MutagenError, OSError, KeyError):
        return {}
    out = {"codec": CODECS[kind(path)],
           "bitrate": getattr(info, "bitrate", None),
           "sample_rate": getattr(info, "sample_rate", None),
           "channels": getattr(info, "channels", None)}
    # Opus is always 48 kHz and mutagen reports no per-file rate; an absent number is not a zero
    return {k: v for k, v in out.items() if v}


def tagged_lyrics(path: Path) -> str | None:
    """The words currently in the file's tags — what we last wrote there, if anyone."""
    try:
        tags = _open(path).tags or {}
        if kind(path) == "mp4":
            return (tags.get("\xa9lyr") or [None])[0]
        if kind(path) == "mp3":
            found = [f for f in tags.values() if isinstance(f, USLT)]
            return found[0].text if found else None
        return (tags.get("lyrics") or [None])[0]
    except (MutagenError, OSError):  # same rule as `audio_length`: only a file we cannot read
        return None


def build_tags(plan: AlbumPlan, track: PlanTrack, lyrics: str | None = None) -> dict[str, str]:
    # **a track total is the length of the disc the track is on**, and the count of discs is its own
    # field (§9, slice 102). This was `len(plan.tracks)` — the whole album — in all three writers, so
    # every track of a three-disc set said 29 of 29 and none of them said how many discs there were:
    # 627 files in the user's own library, 23 albums, every one of them. For a single-disc album the
    # two numbers are the same, which is why nothing noticed and why the fix moves no single-disc file.
    # The length is `plan.disc_length`, which is never below the highest number present — "track 10
    # of 9" is what the count of tracks present would have written on 8 of their 52 discs (R-346).
    discs = max(t.disc for t in plan.tracks)
    on_this_disc = plan.disc_length(track.disc)
    tags = {
        "title": track.title,
        "artist": track.artist,
        "albumartist": plan.albumartist,
        "album": plan.album,
        "tracknumber": str(track.number),
    }
    # **a disc whose numbers repeat has no length yet** (R-425): `disc_length` answers 0, and a
    # total nobody knows is not written at all rather than guessed from the count.
    if on_this_disc:
        tags["tracktotal"] = tags["totaltracks"] = str(on_this_disc)
    # Where the album came from, for whoever opens the file later — worth writing only when it is
    # somewhere they could go. A folder's address is the user's own directory: it identifies the
    # album to nobody, stops being true the moment anything moves, and puts a home path into every
    # file (§9, slice 53). Judged by the value, so any provider with real addresses keeps it.
    if plan.source_url.startswith(("http://", "https://")):
        tags["source"] = plan.source_url
    # The ref only means something to the provider that minted it (§9, slice 50), so it is written
    # as an identifier only where it *is* one. A folder's ref is an absolute path: putting that in
    # every file would carry someone's home directory around and identify the track to nobody.
    if track.provider_in(plan) == DEFAULT_PROVIDER:
        tags["youtube_id"] = track.video_id
    if plan.year:
        tags["date"] = str(plan.year)
    if plan.is_compilation:
        tags["compilation"] = "1"
    if discs > 1:
        tags["discnumber"] = str(track.disc)
        tags["disctotal"] = tags["totaldiscs"] = str(discs)
    if plan.mbid:
        tags["musicbrainz_albumid"] = plan.mbid
    if track.mbid:
        tags["musicbrainz_trackid"] = track.mbid
    # **words that came with the recording never go into the file** (§9, slice 74). A post's captions
    # are the creator's text, kept beside the track as a sidecar and nowhere else; this is the one
    # place that decides what a tag holds, so no pass — a fetch, a retag, `repair` — can carry them
    # in later by a different route. It is also why the signature is unaffected: `signature()` reads
    # this same function, so such a track does not read as permanently out of date.
    if track.provenance.get("lyrics") == Provenance.SOURCE:
        lyrics = None
    if lyrics:
        # one key, the one every tag-reading player understands; timestamps and all,
        # because that is what the .lrc beside the file holds (lyrics.py)
        tags["lyrics"] = lyrics
    return tags


def tags_in(path: Path) -> dict[str, str]:
    """What the file already holds, keyed the way `build_tags` keys it (§9, slice 85).

    The inverse of the three writers, so that a dry run can say *which* values a retag would change
    rather than only that one would happen. Values are strings, whatever the container stores; a key
    the file does not have is simply absent. Never raises for an unknown frame — a file may hold
    anything, and this is only asked to make a report.
    """
    container = kind(path)
    tags = _open(path).tags or {}
    out: dict[str, str] = {}

    def one(value: Any) -> str:
        if isinstance(value, list | tuple):
            value = value[0] if value else ""
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        return str(value)

    if container in ("opus", "flac"):
        for key, value in tags.items():
            if key.lower() != PICTURE_KEY:
                out[key.lower()] = one(value)
    elif container == "mp3":
        for key, frame in ID3_KEYS.items():
            if frame in tags:
                out[key] = one(tags[frame].text if hasattr(tags[frame], "text") else tags[frame])
        for frame in tags.values():
            if isinstance(frame, USLT):
                out["lyrics"] = one(frame.text)
        # **both of ID3's paired frames are read as the pair they are.** `TPOS` went through the key
        # loop above, which would hand `discnumber` the whole of "1/2" — and a dry run comparing that
        # with "1" would say the file needs retagging after every pass that had just written it.
        for frame, number, totals in (("TRCK", "tracknumber", ("tracktotal", "totaltracks")),
                                      ("TPOS", "discnumber", ("disctotal", "totaldiscs"))):
            if frame in tags:
                numbers = one(tags[frame].text).split("/")
                out[number] = numbers[0]
                for name in totals:
                    if len(numbers) > 1 and numbers[1]:
                        out[name] = numbers[1]
                    else:
                        out.pop(name, None)
        if "TCMP" in tags:
            out["compilation"] = one(tags["TCMP"].text)
    else:
        for key, atom in MP4_KEYS.items():
            if atom in tags:
                out[key] = one(tags[atom])
        if tags.get("trkn"):
            number, total = [*list(tags["trkn"][0]), 0, 0][:2]
            out["tracknumber"] = str(number)
            if total:
                out["tracktotal"] = out["totaltracks"] = str(total)
        if tags.get("disk"):
            disc, of = [*list(tags["disk"][0]), 0, 0][:2]
            out["discnumber"] = str(disc)
            if of:
                out["disctotal"] = out["totaldiscs"] = str(of)
        if tags.get("cpil"):
            out["compilation"] = "1"
    return out


def signature(plan: AlbumPlan, track: PlanTrack, cover: bytes | None, lyrics: str | None = None) -> str:
    """Changes whenever the tags or cover that tag_file would write change."""
    payload = json.dumps(build_tags(plan, track, lyrics), sort_keys=True).encode()
    payload += hashlib.sha1(cover or b"").digest()
    return hashlib.sha1(payload).hexdigest()[:16]


# Keys a file may already hold that are better than anything we could put there: the user's own
# words, and an id from whoever built this collection. With `keep_unknown` a present value wins
# (§9, slice 53) — for a downloaded file there is nothing to lose, so the rule is opt-in.
KEEP_IF_PRESENT = ("lyrics", "musicbrainz_albumid", "musicbrainz_trackid")


# the fields whose value is a number, where "01" and "1" say the same thing
NUMBER_KEYS = ("tracknumber", "tracktotal", "totaltracks", "discnumber", "disctotal", "totaldiscs")


def differences(have: dict[str, Any], wanted: dict[str, Any]) -> list[str]:
    """The keys where the file does not already say what the plan wants."""
    return [key for key, value in wanted.items() if str(have.get(key) or "") != str(value or "")]


def only_padding(have: dict[str, Any], wanted: dict[str, Any], keys: list[str]) -> bool:
    """Whether every difference is a number the file writes with a leading zero (R-410, ruling 7).

    noaap writes `1` where the file says `01`, and that is the one state the library is in — but it
    is not a reason to rewrite a file. On the user's collection the unpadding alone would have
    rewritten thousands of files that are otherwise exactly as noaap wants them.
    """
    def same(key: str) -> bool:
        here = str(have.get(key) or "").split("/")[0].strip()
        there = str(wanted.get(key) or "").strip()
        return key in NUMBER_KEYS and here.isdigit() and there.isdigit() and int(here) == int(there)
    return bool(keys) and all(same(key) for key in keys)


def would_write(plan: AlbumPlan, track: PlanTrack, lyrics: str | None, keep_unknown: bool,
                have: dict[str, Any]) -> dict[str, str]:
    """The tags the writer would put in this file, given what it already holds."""
    return _wanted(plan, track, lyrics, keep_unknown, lambda key: have.get(key))


def kept_from_the_file(plan: AlbumPlan) -> tuple[str, ...]:
    """The keys a file answers better than the plan does.

    For an adopted album that includes **`date`** (R-410, ruling 5): a plan carries a year, and the
    file may carry `2003-01-01` or `2022-06-24`. Writing the year back would throw the month and the
    day away — 336 files of the user's collection — for a value that says the same thing less well.
    """
    if getattr(plan, "adopted", None) and not getattr(plan, "tidy_tags", False):
        return (*KEEP_IF_PRESENT, "date")
    return KEEP_IF_PRESENT

ID3_KEYS = {  # Vorbis comment -> ID3 frame. `source` is a TXXX rather than COMM on purpose:
    "title": "TIT2",            # COMM is where people keep their own notes, and this must not
    "artist": "TPE1",           # land on top of one.
    "albumartist": "TPE2",
    "album": "TALB",
    "date": "TDRC",
    "discnumber": "TPOS",
    "source": "TXXX:source",
    "youtube_id": "TXXX:YOUTUBE_ID",
    "musicbrainz_albumid": "TXXX:MusicBrainz Album Id",
    "musicbrainz_trackid": "TXXX:MusicBrainz Track Id",
}

MP4_KEYS = {  # Vorbis comment -> MP4 atom
    "title": "\xa9nam", "artist": "\xa9ART", "albumartist": "aART", "album": "\xa9alb",
    "date": "\xa9day", "source": "\xa9cmt", "lyrics": "\xa9lyr", "youtube_id": "----:com.apple.iTunes:YOUTUBE_ID",
    "musicbrainz_albumid": "----:com.apple.iTunes:MusicBrainz Album Id",
    "musicbrainz_trackid": "----:com.apple.iTunes:MusicBrainz Track Id",
}


# Where each container keeps a person's own note. `©cmt` is also where noaap writes an m4a's
# `source`, so a drop happens before the plan's keys go in and noaap's own value survives it.
VORBIS_COMMENTS = ("comment", "description")


WM_OWNER = "WM/"          # Windows Media's own `PRIV` owners: WMCollectionID, WMContentID, …


def wm_frames_in(path: Path) -> int:
    """How many Windows-Media `PRIV` frames this file holds — what `drop_wm_frames` would remove.

    Only mp3 has them. Their *descriptions* are raw binary, which is how one of them stopped the
    collection's pass: a `\x85` in a `PRIV:WM/WMCollectionID:…` key cut a snapshot record in two
    (§9, slice 114). Never raises: this is asked to decide or to report.
    """
    if kind(path) != "mp3":
        return 0
    try:
        return sum(1 for f in ID3(path, load_v1=False).getall("PRIV")
                   if str(getattr(f, "owner", "")).startswith(WM_OWNER))
    except (MutagenError, OSError, ID3NoHeaderError, KeyError):
        return 0


def comments_in(path: Path) -> int:
    """How many comment fields this file really holds — what `drop_comments` would take away.

    **mp3 is read without ID3v1** (R-438, ruling 2): mutagen turns a trailing block into a
    `COMM:ID3v1 Comment` frame on load, and counting that would report a comment the file's tag does
    not have. Never raises: a file may hold anything and this is only asked to decide or to report.
    """
    try:
        if kind(path) == "mp3":
            return len(ID3(path, load_v1=False).getall("COMM"))
        if kind(path) == "mp4":
            return len((MP4(path).tags or {}).get("\xa9cmt") or [])
        tags = _open(path).tags or {}
        return sum(1 for key in tags.keys() if key.lower() in VORBIS_COMMENTS)
    except (MutagenError, OSError, ID3NoHeaderError, KeyError):
        return 0


def tag_file(path: Path, plan: AlbumPlan, track: PlanTrack, cover: bytes | None = None,
             lyrics: str | None = None, keep_unknown: bool = False,
             drop_comments: bool = False, drop_wm_frames: bool = False) -> str:
    """Write the plan's tags and an embedded cover. Returns the signature.

    By default every existing tag goes: what yt-dlp left there is noise, and the plan is the truth.
    **`keep_unknown=True` is for a file we did not make** (§9, slice 53) — a folder someone has been
    tagging for years holds fields this program does not model (replaygain, ISRC, composer, BPM,
    their own comment) and losing them would be the intake destroying the thing it took in. Then
    only the keys the plan asserts are written, and the keys in `KEEP_IF_PRESENT` are left alone
    where the file already has a value.

    **`drop_comments=True` is the one thing a write takes away rather than leaves** (§9, slice 113,
    R-438): the library's setting, for a collection whose files carry somebody else's `ripped by`
    and `www.…net`. Nothing else about a field this program does not model ever changes.

    The signature is of what the plan asked for, not of what was written, so a value deliberately
    left alone does not make the file look permanently out of date.
    """
    writer = {"mp4": _tag_mp4, "flac": _tag_vorbis, "mp3": _tag_id3, "opus": _tag_vorbis}[kind(path)]
    return writer(path, plan, track, cover, lyrics, keep_unknown, drop_comments, drop_wm_frames)


def _wanted(plan: AlbumPlan, track: PlanTrack, lyrics: str | None, keep_unknown: bool,
            present: Any) -> dict[str, str]:
    """The tags to write: all of them, or all but the ones this file already answers better."""
    tags = build_tags(plan, track, lyrics)
    if keep_unknown:
        for key in kept_from_the_file(plan):
            if present(key):
                tags.pop(key, None)
    return tags


def _tag_vorbis(path: Path, plan: AlbumPlan, track: PlanTrack, cover: bytes | None,
                lyrics: str | None = None, keep_unknown: bool = False,
                drop_comments: bool = False, drop_wm_frames: bool = False) -> str:
    """Opus and FLAC: the same comment names, two different ways to carry a picture."""
    audio = FLAC(path) if kind(path) == "flac" else OggOpus(path)
    old_tags = dict(audio.tags or {})
    tags = _wanted(plan, track, lyrics, keep_unknown, lambda key: old_tags.get(key))
    old_picture = old_tags.get(PICTURE_KEY)
    if keep_unknown:
        for key in tags:  # only what we are about to write: a key held back must not be dropped
            audio.pop(key, None)
    else:
        audio.delete()  # drop whatever yt-dlp/ffmpeg or an earlier run put there
    if drop_comments:
        for key in [k for k in audio.keys() if k.lower() in VORBIS_COMMENTS]:
            del audio[key]
    for key, value in tags.items():
        audio[key] = [value]

    if kind(path) == "flac":
        if cover and (mime := image_mime(cover)):
            audio.clear_pictures()
            audio.add_picture(_picture(cover, mime))
        audio.save()
        return signature(plan, track, cover, lyrics)

    if cover and (mime := image_mime(cover)):
        audio[PICTURE_KEY] = [base64.b64encode(_picture(cover, mime).write()).decode("ascii")]
    elif old_picture and cover is None:
        audio[PICTURE_KEY] = old_picture
    audio.save()
    return signature(plan, track, cover, lyrics)


def _picture(data: bytes, mime: str) -> Picture:
    pic = Picture()
    pic.type = 3  # front cover
    pic.mime = mime
    pic.desc = "Cover"
    pic.data = data
    return pic


def _tag_id3(path: Path, plan: AlbumPlan, track: PlanTrack, cover: bytes | None,
             lyrics: str | None = None, keep_unknown: bool = False,
             drop_comments: bool = False, drop_wm_frames: bool = False) -> str:
    """MP3. ID3 has a frame per field and two of ours have no frame of their own."""
    try:
        # **read without ID3v1** (R-438, ruling 2). mutagen turns a trailing block into v2 frames on
        # load — a `COMM:ID3v1 Comment` and, where the tag had none, a `TDRC` — and `save` then
        # writes them into the ID3v2 tag. That is how 175 files on the share came to hold a comment
        # frame their owner never put there. The tail goes (R-434); nothing of it is moved inwards.
        id3 = ID3(path, load_v1=False)
    except ID3NoHeaderError:  # a file with no tag block at all is normal, not a failure
        id3 = ID3()

    def present(key: str) -> bool:
        if key == "lyrics":
            return any(isinstance(f, USLT) for f in id3.values())
        return bool(id3.get(ID3_KEYS.get(key, "")))

    tags = _wanted(plan, track, lyrics, keep_unknown, present)
    if not keep_unknown:
        id3.delete()
    if drop_comments:
        id3.delall("COMM")              # every description, theirs and any an older version wrote
    if drop_wm_frames:
        for frame in [f for f in id3.getall("PRIV")
                      if str(getattr(f, "owner", "")).startswith(WM_OWNER)]:
            del id3[frame.HashKey]      # a player's library ids, by their own owner
    for key, frame in ID3_KEYS.items():
        if key not in tags:
            continue
        if frame.startswith("TXXX:"):
            id3.setall(frame, [TXXX(encoding=3, desc=frame[5:], text=[tags[key]])])
        else:
            id3.setall(frame, [ID3_FRAMES[frame](encoding=3, text=[tags[key]])])
    # ID3 carries each number and its total in one frame, so these two are set here rather than by
    # the loop above: `TRCK` as track/of, `TPOS` as disc/of (§9, slice 102).
    on_this_disc = plan.disc_length(track.disc)
    # a length nobody knows yet is left out of the frame rather than written as 0 (R-425)
    said = f"{track.number}/{on_this_disc}" if on_this_disc else str(track.number)
    id3.setall("TRCK", [ID3_FRAMES["TRCK"](encoding=3, text=[said])])
    if (discs := max(t.disc for t in plan.tracks)) > 1:
        id3.setall("TPOS", [ID3_FRAMES["TPOS"](encoding=3, text=[f"{track.disc}/{discs}"])])
    if plan.is_compilation:
        id3.setall("TCMP", [TCMP(encoding=3, text=["1"])])
    if "lyrics" in tags:
        id3.setall("USLT", [USLT(encoding=3, lang="eng", desc="", text=tags["lyrics"])])
    if cover and (mime := image_mime(cover)):
        id3.setall("APIC", [APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover)])
    # **an mp3 this program writes ends without an ID3v1 tail** (R-434, R-435). mutagen's `save`
    # defaults to rewriting an existing one from the v2 frames, and ffmpeg's demuxer hands that
    # 128-byte block to the **decoder** — so the rewrite moved a digest that exists to be invariant
    # under a retag, and the whole-collection pass stopped in a refusal on its first mp3 whose tail
    # was not already noaap's own (R-433, ruling 2).
    # Nothing of it is moved inwards either (R-438, ruling 2). mutagen reads a tail into v2 frames
    # on load and `save` writes them, so the first version of this left the block's 30 characters
    # behind as a `COMM:ID3v1 Comment` — which is how 175 files on the share came to hold a comment
    # frame their owner never put there. The user, shown the measurement (4,873 `ripped by
    # Sir_Mc_Tod` in one batch): *"source descriptions I don't want to carry on"*. So the read is
    # `load_v1=False` above, and every field the block holds is a 30-byte latin-1 copy of what the
    # ID3v2 tag already carries in any length and any encoding.
    id3.save(path, v1=0)
    return signature(plan, track, cover, lyrics)


def ends_with_an_id3v1_tail(path: Path) -> bool:
    """Whether this file still carries a tail a pass of noaap's would now drop (R-434).

    1.31.2 wrote the Crematory batch — 175 of its 187 mp3s — with their ID3v1 block *rewritten*
    from the v2 frames rather than dropped, so those files sit in a state no version writes now.
    Nothing else notices: the plan's signature is of what the plan asked for, and the tags inside
    those files are exactly what it asked for. This is the one question that tells them apart, and
    `repair` asks it of every mp3 so that the answer reaches the files. 128 bytes per file, and only
    for the one format that can have one.
    """
    return path.suffix.lower() == ".mp3" and _id3v1_tail(path) is not None


def _id3v1_tail(path: Path) -> bytes | None:
    """The file's 128-byte `TAG` block, or None where there is none."""
    try:
        with path.open("rb") as fh:
            if fh.seek(0, 2) < 128:
                return None
            fh.seek(-128, 2)
            tail = fh.read(128)
    except OSError:
        return None
    return tail if tail[:3] == b"TAG" else None


def _tag_mp4(path: Path, plan: AlbumPlan, track: PlanTrack, cover: bytes | None,
             lyrics: str | None = None, keep_unknown: bool = False,
             drop_comments: bool = False, drop_wm_frames: bool = False) -> str:
    """Same tags for the .m4a files (audio copied out of a combined stream)."""
    audio = MP4(path)
    old_cover = audio.tags.get("covr") if audio.tags else None
    tags = _wanted(plan, track, lyrics, keep_unknown,
                   lambda key: (audio.tags or {}).get(MP4_KEYS.get(key, "")))
    if not keep_unknown:
        audio.delete()
    if drop_comments:
        audio.pop("\xa9cmt", None)      # before the loop: noaap's own `source` lives in this atom
    for key, atom in MP4_KEYS.items():
        if value := tags.get(key):
            audio[atom] = [value.encode() if atom.startswith("----") else value]
    audio["trkn"] = [(track.number, plan.disc_length(track.disc))]   # a 0 total is "not known"
    if (discs := max(t.disc for t in plan.tracks)) > 1:
        audio["disk"] = [(track.disc, discs)]   # the only writer that had the disc total right
    audio["cpil"] = plan.is_compilation
    if cover and (mime := image_mime(cover)) in ("image/jpeg", "image/png"):
        fmt = MP4Cover.FORMAT_JPEG if mime == "image/jpeg" else MP4Cover.FORMAT_PNG
        audio["covr"] = [MP4Cover(cover, imageformat=fmt)]
    elif old_cover and cover is None:
        audio["covr"] = old_cover
    audio.save()
    return signature(plan, track, cover, lyrics)


# -- reading and putting back exactly what a file said (§9, slice 58) ------------------------------
#
# **Every key each writer above can set, in that container's own spelling, and derived from the
# writers rather than listed by hand.** An undo built on a hand-written list is wrong the first
# time a writer gains a key, and it was: the retag added `tracktotal` and `totaltracks`, the record
# had never heard of them, and 17 files came back carrying tags their owner never had. A guard in
# the suite reads the writers' own source and fails when they set something this does not know.
#
# Pictures are deliberately not here. They are not a tag one puts back from a text record, and a
# cover is never written into a file that had none (R-189, ruling 2).


def _logical_keys() -> tuple[str, ...]:
    """What `build_tags` can produce, with every branch of it turned on."""
    from .models import AlbumPlan, Kind, PlanTrack

    one = PlanTrack(video_id="v", number=1, artist="a", title="t", filename="f", provenance={},
                    disc=1, mbid="x")
    two = PlanTrack(video_id="w", number=2, artist="a", title="u", filename="g", provenance={},
                    disc=2)
    plan = AlbumPlan(source_url="https://example.invalid/1", source_id="s", kind=Kind.COMPILATION,
                     album="al", albumartist="aa", year=2000, cover_url=None, folder="f",
                     tracks=[one, two], mbid="y")
    return tuple(build_tags(plan, one, "words"))


VORBIS_KEYS = (*_logical_keys(), *VORBIS_COMMENTS)
# the three ID3 frames and four MP4 atoms the writers set outside their key maps
# **`COMM` and the Vorbis comment are keys this program writes** — since `drop_comments`, which
# takes them away (§9, slice 113). Before that they were nobody's but the owner's, so the record
# fingerprinted them under `tags_outside_ours` and had no value to put back; a restore after a drop
# would have reported every file's comment as lost and been right. Here, the record keeps them.
ID3_EXTRA = ("TRCK", "TCMP", "USLT", "COMM", "PRIV")
MP4_EXTRA = ("trkn", "disk", "cpil")
WRITES = {
    "opus": VORBIS_KEYS,
    "flac": VORBIS_KEYS,
    "mp3": tuple(ID3_KEYS.values()) + ID3_EXTRA,
    "mp4": tuple(MP4_KEYS.values()) + MP4_EXTRA,
}


def raw_tags(path: Path) -> dict[str, Any]:
    """Everything a writer could have touched in this file, in the container's own spelling.

    JSON-safe, because it is kept in the plan: MP4's freeform bytes and its pairs become strings,
    and an ID3 frame becomes its text. Absent keys are absent from the mapping, not `None` — the
    difference is what tells an undo to *remove* a key rather than write an empty one.
    """
    try:
        audio = _open(path)
    except (MutagenError, OSError):
        return {}
    keys = WRITES.get(kind(path), ())
    out: dict[str, Any] = {}
    if kind(path) == "mp3":
        for key in keys:
            found = audio.tags.getall(key) if audio.tags else []
            if not found:
                continue
            if key == "PRIV":
                # **owner and data, per frame** (§9, slice 121): `drop_wm_frames` removes some of
                # them, so the record must be able to put them back. The data is bytes, so it is
                # hexed; the *description* of such a frame is raw binary and is what cut a snapshot
                # record in two (slice 114), which is why none of it reaches a key.
                out[key] = [[f.owner, f.data.hex()] for f in _priv_frames(path)]
                continue
            if key == "COMM":   # an empty list here is "the tag really holds none", not silence
                # **every description, not the first.** A file may hold `COMM::XXX` beside
                # `COMM:iTunNORM:eng`, and a record of one of them is a restore that silently drops
                # the other. The language goes in too, because the frame is not the same frame
                # without it. Those mutagen makes out of an ID3v1 block are not in the tag at all
                # (R-438, ruling 2) and are not recorded as if they were.
                out[key] = [[f.desc, f.lang, [str(v) for v in f.text]]
                            for f in _comm_frames(path)]
                continue
            frame = found[0]
            out[key] = str(frame.text) if key == "USLT" else [str(v) for v in frame.text]
        return out
    for key in keys:
        value = (audio.tags or {}).get(key) if audio.tags is not None else None
        if value is None or value == []:
            continue
        out[key] = _plain(value)
    return out


def _plain(value: Any) -> Any:
    """A tag value as something JSON can hold, and `_shaped` can turn back."""
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, bool | int | str):
        return value
    return str(value)


def _shaped(value: Any) -> Any:
    if isinstance(value, dict) and "bytes" in value:
        return bytes.fromhex(value["bytes"])
    if isinstance(value, list):
        return [_shaped(v) for v in value]
    return value


def tags_outside_ours(path: Path) -> dict[str, str]:
    """Every key this program never writes, by a digest of what it holds (§9, slice 99).

    For a snapshot of somebody's own collection: their `comment`, their replaygain, their cover. A
    restore has nothing to undo about these — nothing here writes them — but a fingerprint of each
    means a pass that *lost* one is found out and can be named. The values themselves are not kept:
    a cover is a hundred kilobytes, and eleven thousand of them are not a snapshot.
    """
    ours = {str(k).lower() for k in WRITES.get(kind(path), ())}
    out: dict[str, str] = {}
    try:
        audio = _open(path)
        items = list((audio.tags or {}).items()) if audio.tags is not None else []
    except (MutagenError, OSError, AttributeError):
        return out
    for key, value in items:
        name = str(key)
        if name.lower() in ours or name.split(":")[0].lower() in ours:
            continue
        out[name] = hashlib.sha256(repr(value).encode("utf-8", "replace")).hexdigest()[:16]
    return out


def restore_tags(path: Path, values: dict[str, Any]) -> bool:
    """Make this file say exactly what it said, in every key a writer could have touched.

    **Both halves.** What was there goes back; what a writer added and the record does not have is
    *removed*. Putting the old values back while leaving our additions behind is not giving the
    file back, and that is the half that shipped broken twice.
    """
    try:
        audio = _open(path)
    except (MutagenError, OSError):
        return False
    if audio.tags is None:
        try:
            audio.add_tags()
        except (MutagenError, OSError):
            return False
    changed = False
    for key in WRITES.get(kind(path), ()):
        want = values.get(key)
        if _restore_one(audio, path, key, want):
            changed = True
    if changed:
        audio.save()
    return changed


def _restore_one(audio: Any, path: Path, key: str, want: Any) -> bool:
    """One key back to what it was, or gone. True when the file had to change."""
    # **a record that says nothing about the comment is not evidence the file had none.** The undo's
    # rule is "what the record does not have is removed", and it was written when the comment was
    # nobody's but the owner's and never recorded at all (§9, slice 113). Every record taken before
    # this is silent about it, so an absent key leaves the file's comment where it is; a record that
    # really saw none says so with an empty list, and that does remove one.
    if want is None and (key in ("COMM", "PRIV") or str(key).lower() in VORBIS_COMMENTS):
        return False
    if kind(path) == "mp3":
        if key == "COMM":
            return _restore_comments(audio, want)
        if key == "PRIV":
            return _restore_priv(audio, want)
        had = audio.tags.getall(key)
        if want is None:
            if not had:
                return False
            audio.tags.delall(key)
            return True
        if had and _plain(_text_of(had[0], key)) == want:
            return False
        audio.tags.setall(key, [_frame(key, want)])
        return True

    now = (audio.tags or {}).get(key)
    if want is None:
        if now is None or now == []:
            return False
        del audio.tags[key]
        # **`del`, not `pop`.** A Vorbis comment block has no `pop` at all — not even a
        # one-argument one — and `pop(key, None)` raises `TypeError: pop expected at most 1
        # argument, got 2` (found 2026-09-28 by a real album whose files had no tags).
        return True
    want = _shaped(want)
    if now is not None and _plain(now) == _plain(want):
        return False
    audio.tags[key] = want if isinstance(want, list) else [want]
    return True


def _priv_frames(path: Path) -> list[Any]:
    """This file's `PRIV` frames as its ID3v2 tag holds them, without ID3v1."""
    try:
        return list(ID3(path, load_v1=False).getall("PRIV"))
    except (MutagenError, OSError, ID3NoHeaderError):
        return []


def _restore_priv(audio: Any, want: Any) -> bool:
    """Every recorded `PRIV` frame back, with its owner and its bytes, or none at all."""
    # **order is mutagen's, not the file's**, so the comparison and the record are both sorted:
    # `getall` hands these back in hash order and a restore must not rewrite a file over that.
    rows = sorted([str(owner), bytes.fromhex(str(data))] for owner, data in (want or []))
    had = sorted([f.owner, f.data] for f in audio.tags.getall("PRIV"))
    if had == rows:
        return False
    audio.tags.delall("PRIV")
    for owner, data in rows:
        audio.tags.add(PRIV(owner=owner, data=data))
    return True


def _comm_frames(path: Path) -> list[Any]:
    """This file's `COMM` frames as its ID3v2 tag really holds them, without ID3v1 (R-438)."""
    try:
        return list(ID3(path, load_v1=False).getall("COMM"))
    except (MutagenError, OSError, ID3NoHeaderError):
        return []


def _restore_comments(audio: Any, want: Any) -> bool:
    """Every recorded `COMM` frame back, with its description and language, or none at all."""
    rows = sorted([str(d), str(lang), [str(v) for v in text]] for d, lang, text in (want or []))
    had = sorted([f.desc, f.lang, [str(v) for v in f.text]] for f in audio.tags.getall("COMM"))
    if had == rows:
        return False
    audio.tags.delall("COMM")
    for desc, lang, text in rows:
        audio.tags.add(COMM(encoding=3, lang=lang or "eng", desc=desc, text=text))
    return True


def _text_of(frame: Any, key: str) -> Any:
    return str(frame.text) if key == "USLT" else [str(v) for v in frame.text]


def _frame(key: str, text: Any) -> Any:
    """An ID3 frame rebuilt from what it said."""
    if key == "USLT":
        return USLT(encoding=3, lang="eng", desc="", text=text if isinstance(text, str) else text[0])
    if key.startswith("TXXX:"):
        return TXXX(encoding=3, desc=key[5:], text=text if isinstance(text, list) else [text])
    return ID3_FRAMES[key](encoding=3, text=text if isinstance(text, list) else [text])
