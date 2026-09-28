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
import shutil
from pathlib import Path
from typing import Any

from . import sources
from .config import Config
from .models import Collection, Entry, Music, SourceRef
from .tag import audio_length

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
        files = audio_files(folder)
        if not files:
            raise sources.NotSupported(f"no audio files in {folder.name!r}")

        rows = [(path, read_tags(path)) for path in files]
        if all(t.get("tracknumber") for _, t in rows):
            rows.sort(key=lambda r: (r[1].get("discnumber") or 1, r[1]["tracknumber"]))

        entries = [
            Entry(
                video_id=str(path),
                position=n,
                title=tags.get("title") or path.stem,
                channel=tags.get("albumartist") or tags.get("artist"),
                duration=audio_length(path),
                music=Music(artist=tags.get("artist"), track=tags.get("title"),
                            album=tags.get("album"), year=tags.get("year")),
            )
            for n, (path, tags) in enumerate(rows, 1)
        ]
        owners = {e.channel for e in entries if e.channel}
        cover = cover_in(folder)
        return Collection(
            source_url=str(folder),
            source_id=str(folder),
            is_playlist=True,
            title=next((t["album"] for _, t in rows if t.get("album")), folder.name.strip()),
            channel=owners.pop() if len(owners) == 1 else None,
            thumbnail=str(cover) if cover else None,
            fetched_at=dt.date.today().isoformat(),
            entries=entries,
            modified=_stamp(folder),
        )

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
        return Entry(video_id=ref, position=tags.get("tracknumber") or 1,
                     title=tags.get("title") or path.stem,
                     channel=tags.get("albumartist") or tags.get("artist"),
                     duration=audio_length(path),
                     music=Music(artist=tags.get("artist"), track=tags.get("title"),
                                 album=tags.get("album"), year=tags.get("year")))

    def art(self, address: str) -> bytes:
        """The cover, by the path `collection()` put in `thumbnail`."""
        return Path(address).read_bytes()

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
