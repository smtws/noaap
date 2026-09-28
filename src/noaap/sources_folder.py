"""A folder of audio files as a source (DESIGN §9, slice 53).

The second real provider, and the one the boundary was built for: no network, no ids, no titles to
parse — an address is a path, a collection is a folder, and `audio()` **copies**. The files are the
user's; nothing here writes, moves or re-encodes anything inside the source.

Two things are deliberately unlike YouTube:

**A ref is an absolute path.** The interface hands `audio(ref, into)` one argument to find a file
with, so a ref relative to its collection could not be resolved on its own. The cost is that moving
the folder invalidates every ref — which `changed()` reports rather than hides, and which a
re-fetch at the new address repairs.

**The file's own tags are the truth.** Whoever built this collection knew more about it than any
convention could, so nothing is cleaned or guessed while a tag is present (the CLEAN capability is
off). What to do when tags are absent belongs to the next commit, not to this one.
"""
from __future__ import annotations

import datetime as dt
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import sources
from .config import Config
from .models import Candidate, Collection, Entry, Music, Provenance, SourceRef
from .tag import audio_length, audio_quality
from .text import key as text_key

NAME = "folder"

# What this provider will pick up. Everything mutagen can open that a music folder holds; the
# suffix is only a filter — `tag.kind` decides how a file is actually read.
AUDIO = (".opus", ".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".oga", ".wav", ".aac", ".wma")

# A cover is a file called one of these. `logo.*` is a label's logo and never an album cover, and
# `.thumb/` is a file manager's thumbnail cache — measured on the reference collection: 74 files
# there, one `cover.jpg.jpg` per real cover, which a looser rule picks up as the cover itself.
COVER_STEMS = ("cover", "folder", "front", "album")
COVER_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


def hidden(path: Path) -> bool:
    """A dot-directory anywhere above the file: `.thumb`, `.git`, a sync tool's scratch."""
    return any(part.startswith(".") for part in path.parts)


def audio_files(folder: Path) -> list[Path]:
    """The audio directly in this folder, in name order. Not recursive: a sub-folder is its own
    collection until the grouping rules say otherwise."""
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in AUDIO and not p.name.startswith("."))


def _subfolders(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith("."))


def cover_in(folder: Path) -> Path | None:
    for stem in COVER_STEMS:
        for suffix in COVER_SUFFIXES:
            if (found := folder / f"{stem}{suffix}").is_file():
                return found
    return None


def read_tags(path: Path) -> dict[str, Any]:
    """Artist, title, album, album artist, year, track and disc as the file itself states them.

    One vocabulary out, whatever the container: mutagen's `easy` mapping does the translating, and
    an unreadable file answers `{}` rather than raising — a folder with one broken file in it is
    still a folder worth taking.
    """
    import mutagen

    try:
        tags = mutagen.File(path, easy=True)
    except Exception:  # mutagen raises a family of its own; an unreadable file is not an error here
        return {}
    if tags is None or not tags.tags:
        return {}

    def one(key: str) -> str | None:
        value = tags.tags.get(key)
        return str(value[0]).strip() if value else None

    def number(key: str) -> int | None:
        raw = one(key)
        if not raw:
            return None
        head = raw.split("/")[0].strip()  # "3/12" is one frame in ID3 and a habit in Vorbis
        return int(head) if head.isdigit() else None

    year = None
    if date := one("date"):
        head = date[:4]
        year = int(head) if head.isdigit() else None
    return {k: v for k, v in {
        "title": one("title"), "artist": one("artist"), "album": one("album"),
        "albumartist": one("albumartist"), "year": year,
        "tracknumber": number("tracknumber"), "discnumber": number("discnumber"),
    }.items() if v is not None}


# `cd1`, `CD 1`, `1` — all three occur in the reference collection, in three different albums.
DISC_FOLDER = re.compile(r"(?:cd|disc|disk)?\s*0*(\d{1,2})\Z", re.I)
# and one album spells its discs as sibling folders instead: "… [Deluxe Edition] Disc 1|2"
DISC_SUFFIX = re.compile(r"^(?P<stem>.+?)[\s._-]*(?:cd|disc|disk)[\s._-]*0*(?P<n>\d{1,2})\s*\Z", re.I)

# What a file name can say when the tags say nothing. The first is this program's own output
# scheme, which is what the 17 untagged files in the reference collection are named by.
NAME_SHAPES = (
    re.compile(r"^(?P<artist>.+?) - (?P<album>.+?) - (?P<n>\d{1,3}) - (?P<title>.+)\Z"),
    re.compile(r"^(?P<n>\d{1,3})\s*[-.]\s*(?P<artist>.+?) - (?P<title>.+)\Z"),
    re.compile(r"^(?P<n>\d{1,3})\s*[-.]\s*(?P<title>.+)\Z"),
)


def from_name(path: Path) -> dict[str, Any]:
    """What the file's own name states. Only ever used where a tag is absent.

    This is not title cleaning (DESIGN §5): a folder has no conventions to strip. It is reading a
    name that was written by a program — very often this one — as the record it is.
    """
    for shape in NAME_SHAPES:
        if found := shape.match(path.stem.strip()):
            got = found.groupdict()
            out: dict[str, Any] = {"title": got["title"].strip()}
            if artist := got.get("artist"):
                out["artist"] = artist.strip()
            if album := got.get("album"):
                out["album"] = album.strip()
            if (n := got.get("n")) and n.isdigit():
                out["tracknumber"] = int(n)
            return out
    return {}


def disc_folders(folder: Path) -> list[tuple[int, Path]]:
    """`[(1, cd1), (2, cd2)]` when every sub-folder is a numbered disc, else `[]`.

    All of them or none: one numbered sub-folder beside three named ones is not a disc split, it
    is a folder with a bonus disc in it, and guessing which would be worse than reading it flat.
    """
    subs = _subfolders(folder)
    if not subs or audio_files(folder):
        return []
    found = []
    for sub in subs:
        number = DISC_FOLDER.fullmatch(sub.name.strip())
        if not number or not audio_files(sub):
            return []
        found.append((int(number[1]), sub))
    return sorted(found)


def disc_siblings(folder: Path) -> list[tuple[int, Path]]:
    """`[(1, "… Disc 1"), (2, "… Disc 2")]` when the album is spelled as folders beside each other.

    The one grouping here that the filesystem did not state, so it is the one that has to agree
    with the tags as well: the siblings must name the same album. Anchored on the lowest number,
    so that asking for either disc describes the same album at the same address.
    """
    named = DISC_SUFFIX.match(folder.name.strip())
    if not named or not folder.parent.is_dir():
        return []
    stem, mine = named["stem"].strip().casefold(), int(named["n"])
    found = []
    for sibling in _subfolders(folder.parent):
        other = DISC_SUFFIX.match(sibling.name.strip())
        if other and other["stem"].strip().casefold() == stem and audio_files(sibling):
            found.append((int(other["n"]), sibling))
    if len(found) < 2 or mine not in [n for n, _ in found]:
        return []
    albums = {read_tags(audio_files(d)[0]).get("album") for _, d in found}
    return sorted(found) if len(albums) == 1 and None not in albums else []


def ignored_in(folder: Path) -> dict[str, int]:
    """What is in there that is not a track, by kind — counted for the report, never touched."""
    out: dict[str, int] = {}
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() in AUDIO:
            continue
        if hidden(path.relative_to(folder)):
            out["hidden"] = out.get("hidden", 0) + 1
        elif path.suffix.lower() in COVER_SUFFIXES:
            out["image"] = out.get("image", 0) + 1
        else:
            out[path.suffix.lower().lstrip(".") or "no suffix"] = out.get(path.suffix.lower().lstrip(".") or "no suffix", 0) + 1
    return out


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


# How good a copy is, before anything ranks them. flac first because it is the only lossless
# format here; bitrate decides the rest. P52 replaces this with a measured rule — until then every
# candidate it chose says so, so that they can all be found again.
LOSSLESS = ("flac",)
UNRANKED = "default, not ranked"


def stream_sha(path: Path) -> str | None:
    """A digest of the audio stream alone, or None where ffmpeg cannot be asked.

    Measured over 2000 files: 80 ms each, 160 s for the collection — against 41 s to hash every
    byte, which finds **nothing**, because every copy differs in its tags. This finds the 68
    recordings that collection holds twice. Being the same stream never means discarding one: they
    are a track on the album and the same track on the best-of, and both are wanted.
    """
    try:
        done = subprocess.run(["ffmpeg", "-v", "quiet", "-i", str(path), "-map", "0:a",
                               "-c", "copy", "-f", "md5", "-"], capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    out = done.stdout.strip()
    return out.removeprefix("MD5=") if out.startswith("MD5=") else None


def measure(path: Path) -> Candidate:
    """Everything about this file that ranking will ever want, read from the file itself."""
    return Candidate(ref=str(path), provider=NAME, length=audio_length(path),
                     bytes=path.stat().st_size, stream_sha=stream_sha(path),
                     added_by="source", why=UNRANKED,
                     when=dt.date.today().isoformat(), **audio_quality(path))


def better(a: Candidate, b: Candidate) -> Candidate:
    """The default pick between two copies of one recording: lossless, else the higher bitrate."""
    for one, two in ((a, b), (b, a)):
        if one.codec in LOSSLESS and two.codec not in LOSSLESS:
            return one
    return a if (a.bitrate or 0) >= (b.bitrate or 0) else b


def _stamp(path: Path) -> str:
    return dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y%m%d")


class FolderSource:
    """The five required calls, plus the one capability a folder has: it can say it changed."""

    name = NAME

    def __init__(self, cfg: Config | None = None, cancel: Any = None) -> None:
        self.cfg = cfg
        self.cancel = cancel

    def handles(self, address: str) -> bool:
        if address.startswith(("http://", "https://")):
            return False
        try:
            return Path(address).expanduser().is_dir()
        except OSError:  # a name too long, a dead mount: not ours, and not a crash either
            return False

    def capabilities(self) -> frozenset[str]:
        # no search (nothing to search), no details (reading it *is* the cheap path), and no CLEAN:
        # a folder has no title conventions to strip
        return frozenset({sources.CHANGES})

    # -- required ----------------------------------------------------------------------

    def collection(self, address: str) -> Collection:
        folder = self._folder(address)
        parts = disc_folders(folder)
        siblings = [] if parts else disc_siblings(folder)
        discs = parts or siblings
        # sub-folders belong to this folder; siblings belong to the lowest-numbered one, so that
        # asking for either disc describes the same album at the same address
        folder = siblings[0][1] if siblings else folder
        # tags and name are read apart and stay apart: `music` carries only what the file itself
        # states, so a value the *name* supplied is recorded as `file_name` and not as `file_tags`
        # (§9, slice 53). `known` is the two together, for the questions that only need an answer.
        rows = [(disc, path, read_tags(path), from_name(path))
                for disc, part in (discs or [(1, folder)]) for path in audio_files(part)]
        if not rows:
            raise sources.NotSupported(f"no audio files in {folder.name!r}")
        if all((t.get("tracknumber") or n.get("tracknumber")) for _, _, t, n in rows):
            rows.sort(key=lambda r: (r[0], r[2].get("discnumber") or r[0],
                                     r[2].get("tracknumber") or r[3]["tracknumber"]))

        # one recording may be in there twice — an album kept as flac *and* as mp3 is two of the
        # 135 folders in the reference collection. Those are two copies of one track, not two
        # tracks, which is what P48's candidates were shaped for (§9, slice 50).
        grouped: dict[Any, list[tuple[int, Path, dict, dict]]] = {}
        for row in rows:
            disc, path, tags, named = row
            known = {**named, **tags}
            number = known.get("tracknumber")
            grouped.setdefault((disc, number) if number else (disc, text_key(known.get("title") or path.stem)),
                               []).append(row)

        entries = []
        for n, group in enumerate(grouped.values(), 1):
            copies = sorted((measure(path) for _, path, _, _ in group),
                            key=lambda c: (c.codec not in LOSSLESS, -(c.bitrate or 0)))
            disc, path, tags, named = next(row for row in group if str(row[1]) == copies[0].ref)
            known = {**named, **tags}
            entries.append(Entry(
                video_id=copies[0].ref,
                position=n,
                title=known.get("title") or path.stem,
                channel=known.get("albumartist") or known.get("artist"),
                duration=copies[0].length,
                disc=disc,
                copies=copies,
                music=Music(artist=tags.get("artist"), track=tags.get("title"),
                            album=tags.get("album"), year=tags.get("year")),
            ))
        owners = {e.channel for e in entries if e.channel}
        cover = self._cover_for(folder, discs)
        return Collection(
            source_url=str(folder),
            source_id=str(folder),
            is_playlist=True,
            title=next((t.get("album") or n.get("album") for _, _, t, n in rows
                         if t.get("album") or n.get("album")), folder.name.strip()),
            channel=owners.pop() if len(owners) == 1 else None,
            thumbnail=str(cover) if cover else None,
            fetched_at=dt.date.today().isoformat(),
            entries=entries,
            modified=_stamp(folder),
        )

    def discs_of(self, address: str) -> list[tuple[int, Path]]:
        """How this album's discs are spelled, for whoever wants to report the grouping."""
        return self._discs(self._folder(address))

    @staticmethod
    def _discs(folder: Path) -> list[tuple[int, Path]]:
        """Sub-folders first, then siblings — the two shapes the reference collection holds."""
        return disc_folders(folder) or disc_siblings(folder)

    def _cover_for(self, folder: Path, discs: list[tuple[int, Path]]) -> Path | None:
        return cover_in(folder) or next((d for _, part in discs if (d := cover_in(part))), None)

    def owner_artist(self, owner: str | None) -> str | None:
        """A folder's owner *is* the artist — there is no channel handle to see through."""
        return owner

    def origins(self) -> dict[str, str]:
        """A folder's three kinds of evidence, under their own names on disk (§9, slice 53)."""
        return {"tags": Provenance.FILE_TAGS, "title": Provenance.FILE_NAME,
                "collection": Provenance.FOLDER_NAME}

    def ignored(self, address: str) -> dict[str, int]:
        """What was left alone in there, by kind. Reported, never removed — it is not ours."""
        return ignored_in(self._folder(address))

    def audio(self, ref: str, into: Path, choice: str = "best") -> Path:
        """Copy, never move. The source file is not ours and is not touched."""
        source = Path(ref)
        if not source.is_file():
            raise sources.NoAudio(f"the file is no longer at {ref}")
        into.mkdir(parents=True, exist_ok=True)
        out = into / source.name
        try:
            shutil.copy2(source, out)  # copy2: the file's own timestamps travel with it
        except OSError as e:
            raise sources.SourceError(f"could not copy {source.name}: {e}") from e
        return out

    def probe(self, ref: str) -> Entry:
        path = Path(ref)
        if not path.is_file():
            raise sources.NoAudio(f"the file is no longer at {ref}")
        tags = read_tags(path)
        known = {**from_name(path), **tags}
        return Entry(video_id=ref, position=known.get("tracknumber") or 1,
                     title=known.get("title") or path.stem,
                     channel=known.get("albumartist") or known.get("artist"),
                     duration=audio_length(path),
                     music=Music(artist=tags.get("artist"), track=tags.get("title"),
                                 album=tags.get("album"), year=tags.get("year")))

    def art(self, address: str) -> bytes:
        """The cover, by the path `collection()` put in `thumbnail` — or, when the folder has no
        cover file, the picture inside the first track that carries one. Measured on the reference
        collection: 65 of 135 folders have a cover file, and 269 of 2000 files carry a picture."""
        path = Path(address)
        if path.suffix.lower() in COVER_SUFFIXES:
            return path.read_bytes()
        if path.is_dir():
            for track in audio_files(path):
                if found := embedded_cover(track):
                    return found
        raise OSError(f"no cover in {address}")

    # -- capabilities ------------------------------------------------------------------

    def changed(self, address: str) -> dict[str, Any] | None:
        """The cheap check: which files are in there now, and when the folder last changed.

        Measured over 2000 files: 0.6 s, against 160 s to hash every audio stream. This is what
        `update` asks, so it has to stay a `stat` per file and nothing more.
        """
        folder = Path(address).expanduser()
        if not folder.is_dir():
            return None  # the folder moved or went away: `update` reads it in full and says so
        return {"ids": [str(p) for p in audio_files(folder)], "modified": _stamp(folder)}

    def listing(self, address: str) -> list[SourceRef]:
        """The albums under an artist folder or a whole collection root."""
        root = self._folder(address)
        out = []
        for child in _subfolders(root):
            # a child holding audio *is* an album; otherwise it is an artist and its children are.
            # Both shapes occur: `listing(<artist>)` and `listing(<the whole collection>)`.
            for folder in [child] if audio_files(child) else _subfolders(child):
                files = audio_files(folder)
                if not files:
                    continue
                tags = read_tags(files[0])
                cover = cover_in(folder)
                out.append(SourceRef(
                    url=str(folder), source_id=str(folder),
                    title=tags.get("album") or folder.name.strip(), tab="folders",
                    artist=tags.get("albumartist") or tags.get("artist"),
                    channel_url=str(folder.parent), count=len(files),
                    thumbnail=str(cover) if cover else None))
        return out

    def url_for(self, ref: str) -> str | None:
        """Nothing to open in a browser. A file manager would need a `file://`, and offering one
        that half the desktops ignore is worse than offering none."""
        return None

    def settings(self) -> dict[str, Any]:
        return {}

    # -- inside ------------------------------------------------------------------------

    @staticmethod
    def _folder(address: str) -> Path:
        folder = Path(address).expanduser()
        if not folder.is_dir():
            raise sources.NotSupported(f"not a folder: {address}")
        return folder.resolve()


sources.register(NAME, FolderSource)
