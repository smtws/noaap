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
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from mutagen import MutagenError
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TCMP, TXXX, USLT, ID3NoHeaderError
from mutagen.id3 import Frames as ID3_FRAMES
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus

from .models import AlbumPlan, PlanTrack
from .sources import DEFAULT as DEFAULT_PROVIDER

PICTURE_KEY = "metadata_block_picture"

MP4_SUFFIXES = (".m4a", ".mp4")
CODECS = {"mp4": "aac", "flac": "flac", "mp3": "mp3", "opus": "opus"}


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


def _open(path: Path) -> Any:
    """The mutagen object for reading. Raises exactly what mutagen raises."""
    return {"mp4": MP4, "flac": FLAC, "mp3": MP3, "opus": OggOpus}[kind(path)](path)


def image_mime(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def audio_length(path: Path) -> float | None:
    """Seconds of audio in the file — the trimmed truth, not what a source said.

    **A zero is not a length.** Three albums in the reference collection are 24-bit FLACs whose
    STREAMINFO carries `total_samples = 0`, which some encoders leave behind when they cannot seek
    back to fill it in. mutagen reports that faithfully as `length = 0.0`, and a track "shorter
    than 30s" is dropped as an intro card — so 52 real tracks of a real collection were refused
    with "all 13 videos are unusable". Unknown is what this is, and unknown is what it now says.
    """
    try:
        return float(_open(path).info.length) or None
    except (MutagenError, OSError):  # not readable, not audio: an unknown length means "no match"
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
                              capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    if not (found := TIMESTAMP.findall(done.stderr)):
        return None
    hours, minutes, seconds = found[-1]
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def measured_length(path: Path) -> float | None:
    """What the file says, and what it sounds like when it will not say (§9, slice 53)."""
    return audio_length(path) or decoded_length(path)


def audio_quality(path: Path) -> dict[str, Any]:
    """Codec, bitrate, sample rate and channels — what ranking will compare (§9, slice 50).

    Measured from the file, never from what a source claimed. Empty when the file cannot be read,
    because an unknown quality must not read as a bad one.
    """
    try:
        info = _open(path).info
    except (MutagenError, OSError):
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
    tags = {
        "title": track.title,
        "artist": track.artist,
        "albumartist": plan.albumartist,
        "album": plan.album,
        "tracknumber": str(track.number),
        "tracktotal": str(len(plan.tracks)),
        "totaltracks": str(len(plan.tracks)),
    }
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
    if max(t.disc for t in plan.tracks) > 1:
        tags["discnumber"] = str(track.disc)
    if plan.mbid:
        tags["musicbrainz_albumid"] = plan.mbid
    if track.mbid:
        tags["musicbrainz_trackid"] = track.mbid
    if lyrics:
        # one key, the one every tag-reading player understands; timestamps and all,
        # because that is what the .lrc beside the file holds (lyrics.py)
        tags["lyrics"] = lyrics
    return tags


def signature(plan: AlbumPlan, track: PlanTrack, cover: bytes | None, lyrics: str | None = None) -> str:
    """Changes whenever the tags or cover that tag_file would write change."""
    payload = json.dumps(build_tags(plan, track, lyrics), sort_keys=True).encode()
    payload += hashlib.sha1(cover or b"").digest()
    return hashlib.sha1(payload).hexdigest()[:16]


# Keys a file may already hold that are better than anything we could put there: the user's own
# words, and an id from whoever built this collection. With `keep_unknown` a present value wins
# (§9, slice 53) — for a downloaded file there is nothing to lose, so the rule is opt-in.
KEEP_IF_PRESENT = ("lyrics", "musicbrainz_albumid", "musicbrainz_trackid")

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


def tag_file(path: Path, plan: AlbumPlan, track: PlanTrack, cover: bytes | None = None,
             lyrics: str | None = None, keep_unknown: bool = False) -> str:
    """Write the plan's tags and an embedded cover. Returns the signature.

    By default every existing tag goes: what yt-dlp left there is noise, and the plan is the truth.
    **`keep_unknown=True` is for a file we did not make** (§9, slice 53) — a folder someone has been
    tagging for years holds fields this program does not model (replaygain, ISRC, composer, BPM,
    their own comment) and losing them would be the intake destroying the thing it took in. Then
    only the keys the plan asserts are written, and the keys in `KEEP_IF_PRESENT` are left alone
    where the file already has a value.

    The signature is of what the plan asked for, not of what was written, so a value deliberately
    left alone does not make the file look permanently out of date.
    """
    writer = {"mp4": _tag_mp4, "flac": _tag_vorbis, "mp3": _tag_id3, "opus": _tag_vorbis}[kind(path)]
    return writer(path, plan, track, cover, lyrics, keep_unknown)


def _wanted(plan: AlbumPlan, track: PlanTrack, lyrics: str | None, keep_unknown: bool,
            present: Any) -> dict[str, str]:
    """The tags to write: all of them, or all but the ones this file already answers better."""
    tags = build_tags(plan, track, lyrics)
    if keep_unknown:
        for key in KEEP_IF_PRESENT:
            if present(key):
                tags.pop(key, None)
    return tags


def _tag_vorbis(path: Path, plan: AlbumPlan, track: PlanTrack, cover: bytes | None,
                lyrics: str | None = None, keep_unknown: bool = False) -> str:
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
             lyrics: str | None = None, keep_unknown: bool = False) -> str:
    """MP3. ID3 has a frame per field and two of ours have no frame of their own."""
    try:
        id3 = ID3(path)
    except ID3NoHeaderError:  # a file with no tag block at all is normal, not a failure
        id3 = ID3()

    def present(key: str) -> bool:
        if key == "lyrics":
            return any(isinstance(f, USLT) for f in id3.values())
        return bool(id3.get(ID3_KEYS.get(key, "")))

    tags = _wanted(plan, track, lyrics, keep_unknown, present)
    if not keep_unknown:
        id3.delete()
    for key, frame in ID3_KEYS.items():
        if key not in tags:
            continue
        if frame.startswith("TXXX:"):
            id3.setall(frame, [TXXX(encoding=3, desc=frame[5:], text=[tags[key]])])
        else:
            id3.setall(frame, [ID3_FRAMES[frame](encoding=3, text=[tags[key]])])
    id3.setall("TRCK", [ID3_FRAMES["TRCK"](encoding=3, text=[f"{track.number}/{len(plan.tracks)}"])])
    if plan.is_compilation:
        id3.setall("TCMP", [TCMP(encoding=3, text=["1"])])
    if "lyrics" in tags:
        id3.setall("USLT", [USLT(encoding=3, lang="eng", desc="", text=tags["lyrics"])])
    if cover and (mime := image_mime(cover)):
        id3.setall("APIC", [APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover)])
    id3.save(path)
    return signature(plan, track, cover, lyrics)


def _tag_mp4(path: Path, plan: AlbumPlan, track: PlanTrack, cover: bytes | None,
             lyrics: str | None = None, keep_unknown: bool = False) -> str:
    """Same tags for the .m4a files (audio copied out of a combined stream)."""
    audio = MP4(path)
    old_cover = audio.tags.get("covr") if audio.tags else None
    tags = _wanted(plan, track, lyrics, keep_unknown,
                   lambda key: (audio.tags or {}).get(MP4_KEYS.get(key, "")))
    if not keep_unknown:
        audio.delete()
    for key, atom in MP4_KEYS.items():
        if value := tags.get(key):
            audio[atom] = [value.encode() if atom.startswith("----") else value]
    audio["trkn"] = [(track.number, len(plan.tracks))]
    if max(t.disc for t in plan.tracks) > 1:
        audio["disk"] = [(track.disc, max(t.disc for t in plan.tracks))]
    audio["cpil"] = plan.is_compilation
    if cover and (mime := image_mime(cover)) in ("image/jpeg", "image/png"):
        fmt = MP4Cover.FORMAT_JPEG if mime == "image/jpeg" else MP4Cover.FORMAT_PNG
        audio["covr"] = [MP4Cover(cover, imageformat=fmt)]
    elif old_cover and cover is None:
        audio["covr"] = old_cover
    audio.save()
    return signature(plan, track, cover, lyrics)
