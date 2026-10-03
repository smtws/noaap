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
from pathlib import Path
from typing import Any

from . import sources
from .config import Config
from .models import Candidate, Collection, Entry, Music, Provenance, SourceRef
from .tag import (
    audio_length,
    audio_quality,
    decoded_length,
    embedded_cover,
    ffmpeg_audio,
    measured_length,
)
from .tag import (
    reading as tag_reading,
)
from .text import key as text_key
from .text import split_feat

NAME = "folder"

# What this provider will pick up. Everything mutagen can open that a music folder holds; the
# suffix is only a filter — `tag.kind` decides how a file is actually read.
AUDIO = (".opus", ".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".oga", ".wav", ".aac", ".wma")

# A cover is a file called one of these. `logo.*` is a label's logo and never an album cover, and
# `.thumb/` is a file manager's thumbnail cache — measured on the reference collection: 74 files
# there, one `cover.jpg.jpg` per real cover, which a looser rule picks up as the cover itself.
COVER_STEMS = ("cover", "folder", "front", "album")
COVER_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


def is_hidden_name(name: str) -> bool:
    """Whether a single path component is meant to be hidden (R-410, ruling 2).

    One leading dot hides: `.thumb`, `.git`, `.Trash-1000`. **Two or more do not** — `...Just
    Dreaming` and `... Ungehörtes und Unerhörtes` are what two albums in the user's collection are
    called, and skipping them as hidden left 26 files out of a take-in with nothing said about it.
    """
    return name.startswith(".") and not name.startswith("..")


def hidden(path: Path) -> bool:
    """A dot-directory anywhere above the file: `.thumb`, `.git`, a sync tool's scratch."""
    return any(is_hidden_name(part) for part in path.parts)


def audio_files(folder: Path) -> list[Path]:
    """The audio directly in this folder, in name order. Not recursive: a sub-folder is its own
    collection until the grouping rules say otherwise."""
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in AUDIO and not is_hidden_name(p.name))


def _subfolders(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_dir() and not is_hidden_name(p.name))


def cover_in(folder: Path) -> Path | None:
    for stem in COVER_STEMS:
        for suffix in COVER_SUFFIXES:
            if (found := folder / f"{stem}{suffix}").is_file():
                return found
    return None


ID3V1_FIELD = 30     # bytes, and therefore characters for anything that fits in latin-1
# …but the field is padded, and a value cut mid-space comes back a character or two short once the
# padding is stripped: `The Cross Of Changes (Special` is 29. Measured on the user's collection.
ID3V1_CUT = range(ID3V1_FIELD - 3, ID3V1_FIELD + 1)


def read_tags(path: Path, one: Any = None) -> dict[str, Any]:
    """Artist, title, album, album artist, year, track and disc as the file itself states them.

    One vocabulary out, whatever the container: mutagen's `easy` mapping does the translating, and
    an unreadable file answers `{}` rather than raising — a folder with one broken file in it is
    still a folder worth taking.
    """
    import mutagen

    try:
        tags = one.easy if one is not None else mutagen.File(path, easy=True)
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
    out = {k: v for k, v in {
        "title": one("title"), "artist": one("artist"), "album": one("album"),
        "albumartist": one("albumartist"), "year": year,
        "tracknumber": number("tracknumber"), "discnumber": number("discnumber"),
    }.items() if v is not None}
    return _past_a_cut_field(path, tags, out)


def _past_a_cut_field(path: Path, tags: Any, out: dict[str, Any]) -> dict[str, Any]:
    """A thirty-character tag whose own file name carries on is a cut value (R-410, ruling 4).

    An ID3v1 field holds thirty bytes. 138 files of the user's collection state a title exactly that
    long — `Zyklus Farbenfinsternis - Kapi`, `Never Let Me Down Again - Aggr` — while the name beside
    them spells the song out in full, and renaming the file to the tag threw away what the owner
    still had. **The version is not the test**: 69 more files carry the same stub in an ID3v2.4
    frame, because whatever wrote them copied the v1 value up. What says it was cut is the length —
    as much as the field holds, give or take the padding that is stripped off it — together with a
    file name that states the same value and goes on. Anything shorter is what its owner wrote.
    """
    from .plan import safe_name  # here, so that a module everything imports stays import-free

    stated = from_name(path)
    for key in ("title", "artist", "album"):
        tag, named = out.get(key), stated.get(key)
        if not tag or not named or len(tag) not in ID3V1_CUT or len(named) <= len(tag):
            continue
        if (whole := _carried_on(tag, named, safe_name)) is not None:
            out[key] = whole
    return out


def _carried_on(tag: str, named: str, safe_name: Any) -> str | None:
    """`tag` with the rest of `named` after it, or None where the name does not continue the tag.

    **The tag's own characters are never replaced** (R-412). A file name cannot hold `/`, `?` or
    `:`, so the name holds what the scheme put there instead — `Die Brut (Columbiahalle/Berlin` in
    the tag is `…-Berlin)` in the name — and taking the name whole would write the scheme's
    substitutes over what the owner typed. What the name is good for is the part beyond where the
    field ran out, so that is all that is taken from it: the head has to match the tag *made safe
    the same way*, and the tail is appended to the tag as it stands.
    """
    if named.startswith(tag):
        return named
    want = safe_name(tag)
    for cut in range(1, len(named) + 1):      # the first cut that matches: a later one eats a space
        if safe_name(named[:cut]) == want:
            return tag + named[cut:]
    return None


# `cd1`, `CD 1`, `1` — all three occur in the reference collection, in three different albums, and
# so does `1-3` / `2-3` / `3-3`, which is "disc one of three" and used to be read as no disc at all:
# the album was then found to hold no audio and was passed over in silence, 34 files of it (R-410,
# ruling 2). What counts is a short name whose first number is the disc; a count after it is noise.
DISC_FOLDER = re.compile(r"(?:cd|disc|disk)?[\s._-]*0*(\d{1,2})"
                         r"(?:\s*(?:of|von|von\s+|/|-|–)\s*0*\d{1,2})?\s*\Z", re.I)
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


def _past_id3v1_together(tags: list[dict[str, Any]]) -> None:
    """One folder's values, read as one: a tag cut to thirty characters is the one that goes on.

    `_past_id3v1` recovers what the *file's own name* still holds, and a file whose name does not
    carry the album keeps the stub — so one folder stated `15 Years After - The dusted Va` in one
    file and `15 Years After - The dusted Variations` in the other eight, and the album was then
    two albums and refused as such (R-410, rulings 4 and 5). The folder is the unit: where a value
    is exactly a field long and another in the same folder goes on from it, they are the same value.
    """
    for key in ("album", "artist", "albumartist"):
        said = {str(row[key]) for row in tags if row.get(key)}
        for short in [one for one in said if len(one) in ID3V1_CUT]:
            longer = sorted((one for one in said if one != short and one.startswith(short)),
                            key=len, reverse=True)
            if longer:
                for row in tags:
                    if row.get(key) == short:
                        row[key] = longer[0]


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
        # **a sub-folder with no audio under it is not a reason to refuse the album** (R-410,
        # ruling 2). The user's Metallica box has an empty `1` beside a full `2` and `3`, and
        # "all of them or none" then read the whole set as no discs at all: 15 files, silently.
        if not any(audio_files(where) for where in (sub, *(w for w in sub.rglob("*") if w.is_dir()))):
            continue
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


# How good a copy is, before anything ranks them. flac first because it is the only lossless
# format here; bitrate decides the rest. P52 replaces this with a measured rule — until then every
# candidate it chose says so, so that they can all be found again.
LOSSLESS = ("flac",)
UNRANKED = "default, not ranked"


def stream_sha(path: Path) -> str | None:
    """A digest of the file as ffmpeg re-muxes it — **not of the recording** (§9, slice 66).

    Measured over 2000 files: 80 ms each, 167 s for the collection — against 41 s to hash every byte,
    which finds **nothing**, because every copy differs in its tags. Over that untouched collection it
    finds exactly what the decoded identity finds: 1932 distinct answers, 68 held by more than one file,
    the same 68 groups. Being the same stream never means discarding one: they are a track on the album
    and the same track on the best-of, and both are wanted.

    **What it must not be used for is identity.** It includes a trailing ID3v1 or Lyrics3v2 block,
    because the demuxer hands those over as audio data, so a file and a re-tagged copy of it read as two
    recordings — 14 of 22 real pairs in one album (catalogue BD8). It stays because it is cheap and true
    about a *file*, and because `equal here` implies `equal audio`, which makes it a pre-check in that
    one direction. `tag.decoded_sha` is the identity.
    """
    # **without a trailing ID3v1 tag** (R-433, ruling 2), for the same reason the decoded digest
    # leaves it out: the demuxer hands that block over as audio data, so the same stream in two
    # files with different tails reads as two streams.
    got = ffmpeg_audio(path, "md5", copy=True)
    out = (got or b"").decode("utf-8", "replace").strip()
    return out.removeprefix("MD5=") if out.startswith("MD5=") else None


def measure(path: Path, digest: bool = True, one: Any = None) -> Candidate:
    """Everything about this file that ranking will ever want, read from the file itself.

    **`digest=False` leaves out the one expensive part** (§9, slice 101). The packet digest is an
    ffmpeg remux, so measuring it reads the whole file: over the user's own collection — 2000 files,
    41 GB — a pass that only wants to *adopt* them read **43.8 GB from the device and took 172 s**
    for what is otherwise a walk of the tags. It is what ranking two copies of one recording needs,
    so `merge` asks for it and an adoption does not; a later pass measures what it needs, once.
    """
    length, how = audio_length(path, one), None
    if length is None and (length := decoded_length(path)) is not None:
        how = "decoded"
    return Candidate(ref=str(path), provider=NAME, length=length, length_by=how,
                     bytes=path.stat().st_size, stream_sha=stream_sha(path) if digest else None,
                     added_by="source", why=UNRANKED,
                     when=dt.date.today().isoformat(), **audio_quality(path, one))


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
        # whether every file's packets are digested while reading a folder (§9, slice 101). On for
        # `merge`, which ranks copies against each other; a take-in turns it off and reads tags only.
        self.digests = True
        # **a folder's tags mean what they say** (R-410, ruling 5). The conventions a video title
        # carries are not a file's; the owner wrote these. The library may ask for the same tidying
        # anyway, and then this says so and `build_plan` does it.
        self.tidy_entries = bool(getattr(cfg, "tidy_adopted_tags", False))

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
        folder = home = self._folder(address)
        parts = disc_folders(folder)
        siblings = [] if parts else disc_siblings(folder)
        discs = parts or siblings
        # sub-folders belong to this folder; siblings belong to the lowest-numbered one, so that
        # asking for either disc describes the same album at the same address
        folder = siblings[0][1] if siblings else folder
        # tags and name are read apart and stay apart: `music` carries only what the file itself
        # states, so a value the *name* supplied is recorded as `file_name` and not as `file_tags`
        # (§9, slice 53). `known` is the two together, for the questions that only need an answer.
        # **one open per file, for every question asked of it** (§9, slice 105). This read the tags,
        # then the length, then the quality — four opens — and a share re-reads on every one of them:
        # 80.75 GB for a dry run over 41 GB of music. The reading is held open across all three.
        rows = []
        for disc, part in (discs or [(1, folder)]):
            for path in audio_files(part):
                with tag_reading(path) as one:
                    rows.append((disc, path, read_tags(path, one), from_name(path),
                                 measure(path, digest=self.digests, one=one)))
        if not rows:
            raise sources.NotSupported(f"no audio files in {folder.name!r}")
        _past_id3v1_together([row[2] for row in rows])
        if all((t.get("tracknumber") or n.get("tracknumber")) for _, _, t, n, _ in rows):
            rows.sort(key=lambda r: (r[0], r[2].get("discnumber") or r[0],
                                     r[2].get("tracknumber") or r[3]["tracknumber"]))

        # One recording may be in there twice — the same track as flac and as mp3 — and those are
        # two copies of one track, not two tracks (§9, slice 50). **A shared track number is not
        # enough to say so**: a folder in the reference collection holds two different songs both
        # numbered 10, and grouping by number alone silently dropped one of them. So a number has
        # to agree with a title, and that is the whole of the rule here. Two files with identical
        # audio and different names are recorded as they are, with their digests: "is this the same
        # recording as that one" is P52's first question and it is answered there, once, for the
        # library as well as for a folder.
        tracks: dict[Any, list[Any]] = {}
        for row in rows:
            disc, path, tags, named, measured = row
            known = {**named, **tags}
            name = (disc, known.get("tracknumber"), text_key(known.get("title") or path.stem))
            tracks.setdefault(name, []).append((row[:4], measured))

        entries = []
        for n, group in enumerate(tracks.values(), 1):
            copies = sorted((copy for _, copy in group),
                            key=lambda c: (c.codec not in LOSSLESS, -(c.bitrate or 0)))
            disc, path, tags, named = next(row for row, _ in group if str(row[1]) == copies[0].ref)
            known = {**named, **tags}
            entries.append(Entry(
                video_id=copies[0].ref,
                position=n,
                title=known.get("title") or path.stem,
                channel=known.get("albumartist") or known.get("artist"),
                duration=copies[0].length,
                disc=disc,
                # **the number the file itself states**, tags first and then the name — the same
                # value this already sorted on. It was read and dropped, and the plan then counted
                # by position, which renumbered every gapped disc (R-372, §9 slice 108).
                number=known.get("tracknumber"),
                copies=copies,
                # the file is already here, so the container is known now rather than after a
                # download. Said by the provider, because the ref it is read off is opaque to
                # everyone else (§9, slice 58).
                ext=Path(copies[0].ref).suffix.lstrip(".").lower() or None,
                # and where the file is, for the same reason and with the same boundary: relative to
                # the folder that was asked for, or `None` when it is not inside it — which is what a
                # disc spelled as a *sibling* folder is (§9, slice 61).
                where=str(path.relative_to(home)) if home in path.parents else None,
                music=Music(artist=tags.get("artist"), track=tags.get("title"),
                            album=tags.get("album"), year=tags.get("year")),
            ))
        # "Mono Inc." on 33 tracks and "Mono Inc. feat. Ronan Harris" on the 34th is one artist
        # with a guest, not two — and reading it as two left that album with no owner, so it was
        # not a release and became an artist playlist (§9, slice 53).
        owners = {split_feat(e.channel)[0] for e in entries if e.channel}
        # A folder with no cover file still has a cover to offer — the picture inside a track. The
        # address for it is the folder itself, because `art()` is only ever asked about an address
        # the collection named: with `thumbnail` left None nothing asks, and 269 embedded pictures
        # in the reference collection went unused (§9, slice 53).
        cover = self._cover_for(folder, discs) or folder
        return Collection(
            source_url=str(folder),
            source_id=str(folder),
            is_playlist=True,
            title=next((t.get("album") or n.get("album") for _, _, t, n, _ in rows
                         if t.get("album") or n.get("album")), folder.name.strip()),
            channel=owners.pop() if len(owners) == 1 else None,
            thumbnail=str(cover),
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

    def is_release(self, collection: Collection) -> bool:
        """An album by one artist, which its own files say by agreeing on an album artist.

        Without this a normal album reads as a compilation the moment one track credits a guest —
        "van Canto, Kai Hansen" is one `artist` tag among twenty, and counting distinct artists
        then says two. A folder does not have to guess: `albumartist` is what it is for, and
        "Various Artists" is the one value that means the opposite (§9, slice 53).
        """
        owner = collection.owner
        return bool(owner) and text_key(owner) != text_key("Various Artists")

    def owner_artist(self, owner: str | None) -> str | None:
        """A folder's owner *is* the artist — there is no channel handle to see through."""
        return owner

    def shortest_track(self) -> float:
        """Nothing in a folder is too short to be part of the album it sits in."""
        return 0.0

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
                     duration=measured_length(path),
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
                files = audio_files(folder) or [f for _, disc in disc_folders(folder)
                                                for f in audio_files(disc)]
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

    def collection_url(self, text: str) -> str | None:
        """A folder that holds albums rather than audio — an artist, or a whole collection.

        This is what makes `noaap fetch <a root>` list what is under it instead of refusing: the
        same question YouTube answers with a channel's base URL (§9, slice 53).
        """
        folder = Path(text).expanduser()
        if not folder.is_dir() or audio_files(folder) or disc_folders(folder):
            return None
        return str(folder.resolve()) if self.listing(text) else None

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
