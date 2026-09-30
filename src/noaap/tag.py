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
        tags = (mutagen.File(path).tags or {}) if mutagen.File(path) else {}
        if raw := tags.get("metadata_block_picture"):
            return Picture(base64.b64decode(raw[0])).data
    except Exception:  # an unreadable picture is no picture; it must not stop the album
        return None
    return None


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
    try:
        done = subprocess.run(["ffmpeg", "-v", "quiet", "-i", str(path), "-map", "0:a",
                               "-f", "s16le", "-"],
                              capture_output=True, timeout=PATIENCE)
    except (OSError, subprocess.SubprocessError):
        return None
    return hashlib.sha256(done.stdout).hexdigest() if done.returncode == 0 and done.stdout else None


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
        if "TRCK" in tags:
            numbers = one(tags["TRCK"].text).split("/")
            out["tracknumber"] = numbers[0]
            if len(numbers) > 1:
                out["tracktotal"] = out["totaltracks"] = numbers[1]
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
            out["discnumber"] = str(tags["disk"][0][0])
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


VORBIS_KEYS = _logical_keys()
# the three ID3 frames and four MP4 atoms the writers set outside their key maps
ID3_EXTRA = ("TRCK", "TCMP", "USLT")
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
    if kind(path) == "mp3":
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


def _text_of(frame: Any, key: str) -> Any:
    return str(frame.text) if key == "USLT" else [str(v) for v in frame.text]


def _frame(key: str, text: Any) -> Any:
    """An ID3 frame rebuilt from what it said."""
    if key == "USLT":
        return USLT(encoding=3, lang="eng", desc="", text=text if isinstance(text, str) else text[0])
    if key.startswith("TXXX:"):
        return TXXX(encoding=3, desc=key[5:], text=text if isinstance(text, list) else [text])
    return ID3_FRAMES[key](encoding=3, text=text if isinstance(text, list) else [text])
