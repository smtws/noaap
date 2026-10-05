"""Pipeline stages 3-5: classify, derive metadata, build the AlbumPlan. Pure, no I/O."""

from __future__ import annotations

import copy
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from .models import AlbumPlan, Collection, Entry, Kind, PlanTrack, Provenance
from .text import key, move_feat, split_feat, strip_leading_artist, strip_self_feat
from .titles import (
    NOISE_WORDS,
    clean_title,
    drop_label,
    strip_album_name,
    title_by_artist,
)

MIN_TRACK_SECONDS = 30  # shorter entries are intro cards, not songs (DESIGN.md §3.4)


# -- which entries become tracks -----------------------------------------------------


def shortest_track(source: Any = None) -> float:
    """The shortest thing this source can offer that is still a song.

    30 seconds is **YouTube's** rule: a playlist opens with an intro card, and a 12-second video
    among thirteen songs is not one of them (DESIGN §3.4). A folder has no intro cards — a short
    file sitting in an album folder is an interlude, a skit or a spoken intro, and it belongs to
    the album. So the number is the provider's, not the core's (§9, slice 53).
    """
    asked = getattr(_source(source), "shortest_track", None)
    return asked() if asked else MIN_TRACK_SECONDS


def skip_reason(entry: Entry, collection: Collection, source: Any = None) -> str | None:
    if entry.skipped:
        return entry.skipped
    if entry.duration is not None and entry.duration < (least := shortest_track(source)):
        return f"shorter than {least:.0f}s ({entry.duration:.0f}s)"
    return None


def usable_entries(collection: Collection, source: Any = None) -> list[Entry]:
    """Entries that become tracks. A playlist may list the same video twice — it is one track."""
    seen: set[str] = set()
    out = []
    for e in collection.entries:
        if skip_reason(e, collection, source) is None and e.video_id not in seen:
            seen.add(e.video_id)
            out.append(e)
    return out


# -- what the source's own conventions say ---------------------------------------------


def _source(source: Any) -> Any:
    """The provider to read conventions with.

    `None` means the default one, for the same reason `AlbumPlan.provider` defaults to it: a
    collection that names no provider came from the only one there was. Making the caller pass it
    would be purer and would silently drop the conventions wherever somebody forgot.
    """
    if source is not None:
        return source
    from . import sources
    from .config import Config

    return sources.get(None, Config())


def read_entry(entry: Entry, source: Any = None) -> tuple[str | None, str]:
    """(artist, title) as the source's own conventions read them.

    A provider whose titles carry conventions — "(Official Video)", a reversed "Song - Artist" —
    says so with the CLEAN capability and reads them here (§9, slice 51; DESIGN §5). One whose
    titles mean what they say, a folder of well-tagged files, has no such method, and the entry's
    own title is used unchanged.
    """
    reader = getattr(_source(source), "clean_entry", None)
    return reader(entry) if reader else (None, entry.title)


DEFAULT_ORIGINS = {"tags": Provenance.SOURCE_TAGS, "title": Provenance.SOURCE_TITLE,
                   "collection": Provenance.COLLECTION}


def tidies(source: Any = None) -> bool:
    """Whether this source's stated values may be tidied (R-410, ruling 5).

    A video title carries conventions — a label suffix, "(Official Video)", the artist in front of
    the song — and stripping them is what makes a playlist into an album. **A folder's tags carry
    none of that**: somebody wrote them, and "Wir sind allein (Live in Dresden)" is the name of that
    recording, not noise. The folder source answers False unless the library asks for the tidying
    anyway (`tidy_adopted_tags`); everything else answers True, as it always did.
    """
    return bool(getattr(_source(source), "tidy_entries", True))


def origins(source: Any = None) -> dict[str, str]:
    """Which provenance names this source's evidence carries (§9, slice 53)."""
    naming = getattr(_source(source), "origins", None)
    return {**DEFAULT_ORIGINS, **naming()} if naming else DEFAULT_ORIGINS


def owner_artist_of(owner: str | None, source: Any = None) -> str | None:
    """The artist an owner's name stands for, if the provider says it stands for one."""
    reader = getattr(_source(source), "owner_artist", None)
    return reader(owner) if reader else None


# -- track-level metadata ------------------------------------------------------------


def track_artist(entry: Entry, source: Any = None) -> tuple[str, Provenance]:
    """YouTube Music's field, else the artist named in the title, else the channel."""
    named = origins(source)
    if entry.music.artist:
        return entry.music.artist, named["tags"]
    parsed, _ = read_entry(entry, source)
    return parsed or owner_artist_of(entry.owner, source) or "Unknown Artist", named["title"]


def track_title(entry: Entry, source: Any = None) -> tuple[str, Provenance]:
    if entry.music.track:
        return entry.music.track, origins(source)["tags"]
    return read_entry(entry, source)[1], origins(source)["title"]


def named_artist(entry: Entry, collection: Collection, source: Any = None) -> str | None:
    """The artist a title actually names, or None when only the channel is left to go by.

    On an artist's own channel the video titles carry the album around ("Feuerschwanz
    Methämmer - Song by Song - …", "Das Elfte Gebot - Unboxing"), which otherwise counts as
    a second and third artist and turns the playlist into a compilation of its own channel.
    """
    if entry.music.artist:
        return split_feat(entry.music.artist)[0]
    parsed, title = read_entry(entry, source)
    if not parsed and (credited := title_by_artist(title)):
        return credited[0]  # "… by The Editors": the title names them after all
    return _without_collection_title(split_feat(parsed)[0], collection.title) if parsed else None


def _without_collection_title(artist: str, playlist_title: str) -> str | None:
    """'Feuerschwanz Methämmer' in the playlist "Methämmer" is Feuerschwanz; "Methämmer" is nobody."""
    words = re.findall(r"\w+", playlist_title or "")
    if not words:
        return artist
    tail = r"\W*".join(map(re.escape, words))  # the words with any spacing/punctuation between
    return re.sub(rf"\W*\b{tail}\s*$", "", artist, flags=re.I).strip(" -–—:|") or None


# -- stage 3: classify ---------------------------------------------------------------


def classify(collection: Collection, source: Any = None) -> Kind:
    if not collection.is_playlist:
        return Kind.SINGLE
    # whether a collection is a release is the provider's to say: it turns on an id prefix only
    # that provider knows, and the core must not read one (§9, slice 51)
    if (released := getattr(_source(source), "is_release", None)) and released(collection):
        return Kind.OFFICIAL_ALBUM
    artists = {_key(a) for e in usable_entries(collection, source) if (a := named_artist(e, collection, source))}
    return Kind.ARTIST_PLAYLIST if len(artists) <= 1 else Kind.COMPILATION


# -- stages 4+5: metadata and plan ---------------------------------------------------


def stated_numbers(entries: Sequence[Entry], keep_duplicates: bool = False) -> list[int]:
    """The number each entry gets: the one it already carries, else the lowest still free on its disc.

    Numbering runs within a disc. For a flat source — every entry on disc 1, none of them stating a
    number — this is the straight 1..N it has always been; a source that knows its discs (a folder of
    `cd1`/`cd2`) counts each one from 1, which is what a disc means.

    **A number the source states is kept, and a gap stays a gap** (R-372, R-373). The owner's rip of
    `01/16 … 09/16, 11/16 … 16/16` is a disc whose track 10 is missing: counting it 1…15 wrote a
    wrong position into the tag and the name of six files and erased the only evidence that anything
    was missing. Of two entries stating the same number on one disc the first in collection order
    keeps it and the other counts as having none; position-counting then fills only numbers free on
    that disc, lowest first.

    **`keep_duplicates` is for a folder** (R-423, point 1). A folder whose files state 1-9 twice is
    two discs somebody flattened, or two releases in one place; renumbering the second run 10-18
    writes a position into eighteen of the owner's files that nothing ever said, and the owner asked
    for the opposite - keep what is there, report it, never prompt and never drop. The names keep
    the files apart by title; where two would still want one name, that is a collision and the album
    keeps its own names (R-410, point 1).
    """
    if keep_duplicates:
        out_dup: list[int] = []
        last_dup: dict[int, int] = {}
        for entry in entries:
            stated = entry.number if isinstance(entry.number, int) and entry.number > 0 else None
            if stated is None:
                stated = last_dup.get(entry.disc, 0) + 1
            last_dup[entry.disc] = max(last_dup.get(entry.disc, 0), stated)
            out_dup.append(stated)
        return out_dup

    taken: dict[int, set[int]] = {}
    out: list[int | None] = []
    # every stated number is claimed before anything is counted, so a number stated late in the
    # collection is not handed to an earlier entry that had none
    for entry in entries:
        stated = entry.number if isinstance(entry.number, int) and entry.number > 0 else None
        mine = taken.setdefault(entry.disc, set())
        if stated is not None and stated not in mine:
            mine.add(stated)
            out.append(stated)
        else:
            out.append(None)
    last: dict[int, int] = {}
    for i, entry in enumerate(entries):
        if out[i] is not None:
            continue
        mine = taken.setdefault(entry.disc, set())
        number = last.get(entry.disc, 0) + 1
        while number in mine:
            number += 1
        last[entry.disc] = number
        mine.add(number)
        out[i] = number
    return [n for n in out if n is not None]


def build_plan(collection: Collection, kind: Kind | None = None, source: Any = None) -> AlbumPlan:
    kind = kind or classify(collection, source)
    entries = usable_entries(collection, source)
    album_prov: dict[str, str] = {}
    named_origin = origins(source)
    tidy = tidies(source)

    if kind == Kind.COMPILATION:
        albumartist = collection.channel or "Various Artists"
        album = compilation_album_title(collection.title, albumartist)
        album_prov = {"albumartist": named_origin["collection"], "album": named_origin["collection"]}
        # **what the files state is what the album is called** (R-410, ruling 5). A folder of music
        # by many hands is still an album somebody tagged, and the folder's name is a worse record
        # of it than the tag in every file.
        if not tidy and (shared_album := _shared([e.music.album for e in entries])):
            album, album_prov["album"] = shared_album, named_origin["tags"]
        year = None
    else:
        named = [(a, track_artist(e, source)[1]) for e in entries if (a := named_artist(e, collection, source))]
        # when no title names an artist, the uploader is the best guess there is: an album
        # playlist carries no channel of its own, but its videos do ("Saltatio Mortis")
        albumartist, prov = (
            _most_common(named)
            or _most_common([track_artist(e, source) for e in entries])
            or (collection.channel or "Unknown Artist", named_origin["collection"])
        )
        album_prov["albumartist"] = prov
        shared_album = _shared([e.music.album for e in entries])
        if shared_album:
            album, album_prov["album"] = shared_album, named_origin["tags"]
        else:
            album = _playlist_album_title(collection.title, albumartist)
            album_prov["album"] = named_origin["collection"]
        # release_year also exists on plain videos (upload year) - only trust it with an album
        year = _most_common_value([e.music.year for e in entries if e.music.year and e.music.album])
        if year:
            album_prov["year"] = named_origin["tags"]

    tracks = []
    # **a folder's numbers are kept as stated, duplicates and all** (R-423, point 1)
    numbers = stated_numbers(entries, keep_duplicates=states_numbers_for(source))
    for entry, number in zip(entries, numbers, strict=True):
        artist, artist_prov = track_artist(entry, source)
        title, title_prov = track_title(entry, source)
        named = entry.music.artist or read_entry(entry, source)[0]
        if not named and (credited := title_by_artist(title)):
            # the uploader is not the artist, the title credits them: "… by The Editors"
            artist, title, named = credited[0], credited[1], credited[0]
        if kind != Kind.COMPILATION and tidy:
            # the album's own artist, not the channel handle or the album title read as a name
            # (the guest credit stays on for move_feat, which puts it into the title below)
            stripped = _without_collection_title(artist, collection.title) if named else None
            artist, artist_prov = (stripped, artist_prov) if stripped else (albumartist, named_origin["collection"])
        elif kind != Kind.COMPILATION and not named:
            # **a field that is empty is filled; one that is written is left** (R-410, ruling 5)
            artist, artist_prov = albumartist, named_origin["collection"]
        if tidy:
            title = strip_leading_artist(artist, title)  # "Metallica: Nothing Else Matters"
            artist, title = move_feat(artist, title)  # guests belong in the title
            title = strip_self_feat(artist, title)
        tracks.append(
            PlanTrack(
                video_id=entry.video_id,
                number=number,
                disc=entry.disc,
                artist=artist,
                title=title,
                filename="",  # set by refresh_derived
                # what the audio already is, where the source knows. A download finds out when the
                # file arrives; a folder's file is on the disk now, and taking the default instead
                # is what would rename an mp3 to `.opus` (§9, slice 58).
                ext=entry.ext or PlanTrack.ext,
                provenance={"artist": artist_prov, "title": title_prov},
                auto={"artist": artist, "title": title},
                channel=entry.channel,
                duration=entry.duration,
                # every copy the source can offer, best first. `sync_candidates` only ever adds a
                # missing ref, so the one that is `video_id` is already here and stays chosen.
                candidates=[replace(c) for c in entry.copies],
            )
        )

    if tidy:
        drop_album_name(album, tracks)

    skipped = [
        {"video_id": e.video_id, "title": e.title, "reason": reason} | ({"transient": True} if e.transient else {})
        for e in collection.entries
        if (reason := skip_reason(e, collection, source))
    ]
    plan = AlbumPlan(
        source_url=collection.source_url,
        source_id=collection.source_id,
        kind=kind,
        album=album,
        albumartist=albumartist,
        year=year,
        cover_url=collection.thumbnail or (entries[0].thumbnail if entries else None),
        folder="",  # set by refresh_derived
        tracks=tracks,
        skipped=skipped,
        provenance=album_prov,
        auto={"kind": kind, "album": album, "albumartist": albumartist, "year": year},
        source_state={"ids": [e.video_id for e in collection.entries], "modified": collection.modified},
    )
    return refresh_derived(plan)


PREFIXED_SHARE = 0.8  # a prefix on nearly every track labels the release, not the songs


def drop_album_name(album: str, tracks: list[PlanTrack]) -> int:
    """Remove the album's name from the track titles when almost all of them carry it.

    YouTube Music writes "1 - Der Kuss des Kometen (Teil 01)" for every part of an audio play.
    Judged per album, so a lone title track keeps its name: "Carolus Rex (Swedish version)"
    stands among fifteen unrelated titles, "Teil 01" among thirty siblings.
    """
    shorter = {t.video_id: strip_album_name(album, t.title) for t in tracks}
    hits = [t for t in tracks if shorter[t.video_id] != t.title]
    if len(hits) < 3 or len(hits) < PREFIXED_SHARE * len(tracks):
        return 0
    for t in hits:
        t.title = shorter[t.video_id]
        if "title" in t.auto:
            t.auto["title"] = t.title
    return len(hits)


def name_single_after_its_song(plan: AlbumPlan) -> str | None:
    """A single is one song, so its album name is that song's name. Returns the new name or None.

    `build_plan` reads the album name off the *video* title, and enrichment then improves the
    track only — so the album kept `The Dead Don't Die (feat. @xxFEUERSCHWANZxx)` while its one
    track became MusicBrainz' `The Dead Don't Die feat. Feuerschwanz`, and the folder carried the
    uploader's handle (DESIGN.md §9, slice 25). Applied after enrichment, in the fetch and in `repair`.

    An album name the user chose is never touched. A track title they chose *is* followed, and the
    album's provenance is then left as it was rather than set to `user`: marking it would freeze
    the album, so a later edit of the same track title would stop reaching it.
    """
    if plan.kind != Kind.SINGLE or len(plan.tracks) != 1:
        return None
    if plan.provenance.get("album") == Provenance.USER:
        return None
    title = plan.tracks[0].title.strip()
    return title if title and title != plan.album else None


def set_single_album_name(plan: AlbumPlan) -> str | None:
    """Apply `name_single_after_its_song` to the plan. Returns the name it set, or None."""
    wanted = name_single_after_its_song(plan)
    if wanted is None:
        return None
    plan.album = plan.auto["album"] = wanted
    if (source := plan.tracks[0].provenance.get("title")) and source != Provenance.USER:
        plan.provenance["album"] = source
    refresh_derived(plan)
    return wanted


def wanted_folder(plan: AlbumPlan) -> str:
    """Where the album belongs, relative to the library root, given its (edited) fields."""
    return f"{safe_name(plan.albumartist)}/{safe_name(plan.album)}"


def shows_artist(plan: AlbumPlan, t: PlanTrack) -> bool:
    """Whether this track's own artist belongs in its file name. Asked here and nowhere else, so that
    a pass recognising a name noaap wrote asks the same question the writer did (§9, slice 63)."""
    return plan.kind == Kind.COMPILATION or _key(t.artist) != _key(plan.albumartist)


def wanted_filename(plan: AlbumPlan, t: PlanTrack, disc: int | bool | None = False) -> str:
    """The name noaap's scheme gives this track. `disc` overrides which form: the number is in the
    name when the album has more than one disc, and a plan damaged by 1.5.0 can hold either form."""
    if disc is False:
        disc = t.disc if max(x.disc for x in plan.tracks) > 1 else None
    return track_filename(plan.albumartist, plan.album, t.number,
                          t.artist if shows_artist(plan, t) else None, t.title, disc, t.ext)


def refresh_derived(plan: AlbumPlan) -> AlbumPlan:
    """Update names of things not on disk yet. `folder` and the filenames of finished tracks
    describe what IS on disk; only the executor moves those (download.relocate / download.run).

    **An album that keeps its names is left entirely alone here** (§9, slice 58). Its files are
    the owner's and are called what the owner called them; deriving a name for them would make
    every later pass want to rename, which is the one thing adoption promises not to do.
    """
    if plan.keep_names:
        return plan
    if not any(t.state == "done" for t in plan.tracks):
        plan.folder = wanted_folder(plan)  # nothing on disk yet: follow the (enriched) names
    for t in plan.tracks:
        if t.state != "done":
            t.filename = wanted_filename(plan, t)
    return plan


FOLDER = "folder"   # the one provider that reads a track's number before anything is fetched


# words a title carries when it is another take of the same song. The hint below only ever points
# at them; what a run of files really is stays MusicBrainz' answer or the owner's (R-424).
RETAKE_WORDS = ("mix", "remix", "live", "demo", "acoustic", "instrumental", "bonus", "edit",
                "version")


def duplicate_numbers(plan: AlbumPlan) -> dict[tuple[int, int], list[PlanTrack]]:
    """`{(disc, number): the tracks holding it}`, for every number held more than once."""
    by: dict[tuple[int, int], list[PlanTrack]] = {}
    for track in plan.tracks:
        by.setdefault(((track.disc or 1), track.number), []).append(track)
    return {key: tracks for key, tracks in sorted(by.items()) if len(tracks) > 1}


def _retake_hint(groups: list[list[PlanTrack]]) -> str:
    """`run 2: every title says "mix"` — where exactly one title per group carries one word."""
    for word in RETAKE_WORDS:
        marked = [[t for t in group if word in t.title.lower()] for group in groups]
        if all(len(m) == 1 for m in marked) and len(groups) > 1:
            return f'run 2: every title says "{word}"'
    return ""


def says_duplicates(plan: AlbumPlan) -> str | None:
    """What is odd about this album's numbers, in one line, or nothing (R-423, point 2).

    Never a prompt and never a decision: the album is taken in exactly as it states itself, and this
    says what a person — or MusicBrainz, where it is asked — would want to look at.
    """
    dups = duplicate_numbers(plan)
    if not dups:
        return None
    groups = list(dups.values())
    runs = max(len(group) for group in groups)
    numbers = sorted(number for _, number in dups)
    every = len(dups) * runs == len(plan.tracks) and len({len(g) for g in groups}) == 1
    whole = every and numbers == list(range(min(numbers), max(numbers) + 1))
    if whole:
        what = f"{runs} runs of {min(numbers)}–{max(numbers)} ({len(plan.tracks)} files): discs?"
    else:
        what = ", ".join(f"track {number} "
                         + ("twice" if len(dups[key]) == 2 else f"{len(dups[key])} times")
                         for key, number in ((key, key[1]) for key in dups))
    if hint := _retake_hint(groups):
        what += f" — {hint}"
    # **and the total nobody knows is said out loud** (R-425): a disc whose numbers repeat has no
    # length until something answers, so none is written and the line says that is why.
    if any(plan.disc_length(disc) == 0 for disc, _ in dups):
        what += " · no track total is written until this is answered"
    return what


def states_numbers_for(source: Any = None) -> bool:
    """Whether this source knows what number a track carries, asked of the source itself."""
    return getattr(_source(source), "name", "") == FOLDER


def states_numbers(plan: AlbumPlan) -> bool:
    """Whether this album's source knows what number a track carries (R-375).

    A folder does: the number is in the file, and `Entry.number` carries it. Everything that has to
    fetch before there is a file cannot know one, and for those the position in the source is the
    only answer there is. Where the source does know, **the files are the authority**: a file that
    left leaves a gap, a file that arrived takes its own number, and nothing counts off a disc that
    already has numbers — counting it is R-372's defect one update later.
    """
    return plan.provider == FOLDER


def keep_stated_numbers(plan: AlbumPlan, fresh_by_id: dict[str, PlanTrack]) -> None:
    """Every track's number as its file states it now; a track no longer there keeps the one it had.

    Claimed in the plan's own order — the source's tracks first, then what has left it — so that a
    file which now carries number 4 gets it and a departed track holding 4 is the one that moves. A
    track with no usable number, or one already taken on its disc, takes the lowest number free
    there.
    """
    for t in plan.tracks:
        if (f := fresh_by_id.get(t.video_id)) is not None:
            t.number, t.disc = f.number, f.disc
    taken: dict[int, set[int]] = {}
    owed: list[PlanTrack] = []
    for t in plan.tracks:
        mine = taken.setdefault(t.disc, set())
        if t.number and t.number not in mine:
            mine.add(t.number)
        else:
            owed.append(t)
    last: dict[int, int] = {}
    for t in owed:
        mine = taken.setdefault(t.disc, set())
        number = last.get(t.disc, 0) + 1
        while number in mine:
            number += 1
        last[t.disc], t.number = number, number
        mine.add(number)


ALBUM_FIELDS = ("kind", "album", "albumartist", "year")
TRACK_FIELDS = ("artist", "title")


def as_the_plan_knows_them(existing: AlbumPlan, fresh: AlbumPlan) -> AlbumPlan:
    """A fresh read of a folder, with each track's id put back to the one the plan holds for that
    very file (§9, slice 135; R-488).

    A folder's refs **are** paths, so a fresh read calls a file by where it is while the plan calls it
    by `filename` — and where a pass or a person took another candidate for a track, those two names
    are different: the plan's id is the owner's file, which is gone, and the file in the folder is
    the copy that superseded it. Matching on the id alone then reported *both* — the superseded track
    as `no longer in the source` and its own replacement as new.
    **On 20 of the user's albums.** `The Dead Don't Die` went from 20 tracks to 37 with the numbers
    1,1,2,2,…,28 and 37 audio files in the folder, measured on a copy before any of it reached the
    library; `Hey Living People` 13 → 21, `Schandmaul — Wie Pech & Schwefel` 15 → 22.

    Matched on the path **relative to the folder that was read**, so a track in `cd1/` is only ever
    the plan's `cd1/` track — and **never where two tracks could answer**: an ambiguous name is left
    to the id, which is the behaviour that was there before.
    """
    if fresh.provider != FOLDER:
        return fresh
    root = str(fresh.source_url or fresh.source_id or "")
    if not root.startswith("/"):
        return fresh
    mine: dict[str, list[PlanTrack]] = {}
    for track in existing.tracks:
        if track.filename:
            mine.setdefault(str(Path(track.filename)), []).append(track)
    out = copy.deepcopy(fresh)
    for track in out.tracks:
        said = str(track.video_id or "")
        if not said.startswith("/"):
            continue
        try:
            rel = str(Path(said).relative_to(root))
        except ValueError:
            continue
        held = mine.get(rel, [])
        if len(held) == 1 and held[0].video_id != track.video_id:
            track.video_id = held[0].video_id
            track.sync_candidates()
    return out


def merge_plans(existing: AlbumPlan, fresh: AlbumPlan) -> AlbumPlan:
    """Bring an existing plan up to date with a fresh one from the same source (DESIGN.md slice 3).

    - user edits (value differs from the recorded auto value) always win;
      untouched fields take the fresh auto value
    - track order and numbers follow the source (for a curated playlist the order is the
      content; for a matched release, MusicBrainz' numbering), so a video that shows up
      later lands where it belongs, not at the end
    - tracks that left the source are kept (their files stay), flagged and put last
    """
    # **a file the plan already holds is that plan's track, whatever the fresh id says** (slice 135)
    fresh = as_the_plan_knows_them(existing, fresh)
    merged = copy.deepcopy(existing)
    _merge_fields(merged, fresh, ALBUM_FIELDS)
    merged.cover_url = fresh.cover_url or merged.cover_url
    merged.cover_fallback_url = fresh.cover_fallback_url or merged.cover_fallback_url
    # **a release a person named is theirs to keep** (§9, slice 137): every other album field goes
    # through `_merge_fields`, which honours a user edit; `mbid` was taken from the fresh plan
    # unconditionally, so a pinned release was gone at the next update.
    if merged.provenance.get("mbid") != Provenance.USER:
        merged.mbid = fresh.mbid or merged.mbid
    merged.skipped = fresh.skipped
    merged.source_state = fresh.source_state or merged.source_state

    fresh_by_id = {t.video_id: t for t in fresh.tracks}  # a repeated video is one entry
    listed = set(fresh_by_id) | {s["video_id"] for s in fresh.skipped}  # skipped videos are still in the source
    known = set()
    for t in merged.tracks:
        known.add(t.video_id)
        f = fresh_by_id.get(t.video_id)
        t.in_source = t.video_id in listed
        if f:
            _merge_fields(t, f, TRACK_FIELDS)
            if t.provenance.get("title") != Provenance.USER:
                t.mbid = f.mbid or t.mbid
            t.mb_length = f.mb_length or t.mb_length
            if not t.source_override:
                # both describe the *video*, and a track pointed at another one (§9, slice 34) is not
                # taking its audio from the playlist's: its uploader decides which channel-wide
                # trim applies to it, and its length is what the trim bar is drawn with
                t.channel = f.channel or t.channel
                t.duration = f.duration or t.duration

    for f in fresh.tracks:
        if f.video_id not in known:
            merged.tracks.append(copy.deepcopy(f))

    # numbering: the fresh plan's, then everything no longer in it, in its previous order
    by_id = {t.video_id: t for t in merged.tracks}
    ordered, placed = [], set()
    for f in fresh.tracks:
        if f.video_id not in placed:
            placed.add(f.video_id)
            ordered.append(by_id[f.video_id])
    rest = sorted((t for t in merged.tracks if t.video_id not in fresh_by_id), key=lambda t: (t.disc, t.number))
    # **where the source states its numbers, they are kept and nothing is counted off** (R-375).
    # An update of a folder album read every number out of the files again; position-counting them
    # here would renumber a gapped disc one update after adoption stopped doing it.
    if states_numbers(fresh):
        merged.tracks = ordered + rest
        keep_stated_numbers(merged, fresh_by_id)
        return refresh_derived(merged)
    # The order is the user's when they said so: a YouTube playlist's sequence is often just
    # the order things were added in, while the album may follow a release or another shop.
    if merged.provenance.get("order") == Provenance.USER:
        last = max((t.number for t in merged.tracks), default=0)
        for t in ordered:
            if t.video_id not in known:  # a video that appeared since goes to the end
                last += 1
                t.number = last
        merged.tracks = sorted(ordered + rest, key=lambda t: (t.disc, t.number))
        return refresh_derived(merged)

    # A flat source says nothing about a disc split this album already has - YouTube playlists
    # have no media - so a split (by hand, or from a release MusicBrainz matched earlier)
    # survives an update, and only a fresh plan that has discs of its own may change them.
    split = max((t.disc for t in merged.tracks), default=1) > 1 and max((f.disc for f in fresh.tracks), default=1) == 1
    if split:
        last_disc = max(t.disc for t in merged.tracks)
        next_number = max((t.number for t in merged.tracks if t.disc == last_disc), default=0)
        for t in ordered:
            if t.video_id not in known:  # a video that appeared since joins the last disc
                next_number += 1
                t.disc, t.number = last_disc, next_number
        merged.tracks = sorted(ordered + rest, key=lambda t: (t.disc, t.number))
        return refresh_derived(merged)

    for number, t in enumerate(ordered, 1):
        fresh_track = fresh_by_id[t.video_id]
        t.number, t.disc = (fresh_track.number, fresh_track.disc) if len(ordered) == len(fresh.tracks) else (number, fresh_track.disc)
    last = max((t.number for t in ordered), default=0)
    for i, t in enumerate(rest, 1):
        t.number, t.disc = last + i, max((f.disc for f in fresh.tracks), default=1)
    merged.tracks = ordered + rest
    return refresh_derived(merged)


# how much a value is trusted; a merge never replaces a value by a less trusted one
TRUST = {Provenance.COLLECTION: 1, Provenance.SOURCE_TITLE: 1, Provenance.SOURCE_TAGS: 2,
         Provenance.MB: 3, Provenance.USER: 4,
         # a folder's origins weigh the same as the ones they stand in for: a file's own tags are
         # evidence of the same kind as a source's metadata, a name is a name
         Provenance.FOLDER_NAME: 1, Provenance.FILE_NAME: 1, Provenance.FILE_TAGS: 2}


def _merge_fields(target: AlbumPlan | PlanTrack, fresh: AlbumPlan | PlanTrack, fields: tuple[str, ...]) -> None:
    for name in fields:
        if _edited(target, name):
            target.provenance[name] = Provenance.USER
        elif TRUST.get(fresh.provenance.get(name), 0) < TRUST.get(target.provenance.get(name), 0):
            continue  # e.g. MusicBrainz was skipped or down this time: keep what it said before
        else:
            setattr(target, name, getattr(fresh, name))
            if name in fresh.provenance:
                target.provenance[name] = fresh.provenance[name]
            else:
                target.provenance.pop(name, None)
        target.auto[name] = fresh.auto.get(name)


def _edited(obj: AlbumPlan | PlanTrack, name: str) -> bool:
    return name in obj.auto and getattr(obj, name) != obj.auto[name]


def compilation_album_title(playlist_title: str, curator: str) -> str:
    """'My Dark Lullabies Vol.1 - Heavy Sleeping' -> 'Vol. 1 - Heavy Sleeping' (DESIGN.md §2.1)."""
    title = drop_label(playlist_title)
    words = re.findall(r"\w+", curator)
    if words:  # match the curator's words with any spacing/punctuation between them
        prefix = r"\W*".join(map(re.escape, words))
        title = re.sub(rf"^{prefix}\b[\s\-–—:|]*", "", title, flags=re.I)
    title = re.sub(r"^vol(?:ume)?\.?\s*(\d+)\s*[-–—:|]\s*", r"Vol. \1 - ", title, flags=re.I)
    return title or playlist_title.strip()


ALBUM_NOISE = NOISE_WORDS | {"full", "album", "complete", "playlist", "stream"}


def _playlist_album_title(playlist_title: str, artist: str) -> str:
    """'SABATON - Legends (Full Album)' -> 'Legends'; 'Album - Chronik' -> 'Chronik'.

    A single takes its album name from the video's own title, so it gets the same hygiene the
    track title gets — `clean_title`, which drops the label tail, the noise brackets and the `@`
    of a handle. Without it the album read *The Dead Don't Die (feat. @xxFEUERSCHWANZxx)* while
    its one track read the same name without the `@`, and the folder carried the handle.
    """
    title = clean_title(playlist_title).removeprefix("Album - ")
    for sep in (" - ", " – ", ": "):
        head, found, rest = title.partition(sep)
        if found and _key(head) == _key(artist):
            title = rest.strip()
            break
    cleaned = re.sub(
        r"\s*[(\[]([^()\[\]]*)[)\]]",
        lambda m: "" if set(re.findall(r"\w+", m[1].casefold())) <= ALBUM_NOISE else m[0],
        title,
    ).strip()
    return cleaned or title


# -- filenames -----------------------------------------------------------------------

_UNSAFE = str.maketrans({"/": "-", "\\": "-", "|": "-", ":": " -", "*": "", "?": "", "<": "", ">": "", '"': "'"})
MAX_NAME_BYTES = 240


def safe_name(name: str) -> str:
    name = "".join(ch for ch in name.translate(_UNSAFE) if ch.isprintable())
    name = re.sub(r"\s+", " ", name).strip(" .")
    while len(name.encode()) > MAX_NAME_BYTES:
        name = name[:-1].rstrip(" .")
    return name or "_"


def clashing_names(plan: AlbumPlan) -> dict[str, list[PlanTrack]]:
    """The names two or more of this album's tracks would both be given (R-410, ruling 1).

    A rename that lands on a name another track already took does not happen — `old.rename(new)` is
    guarded — so the file keeps its name while the plan records the one it did not get. Two tracks
    wanting one name is the owner's folder telling us something (two files, one number, or a title
    cut to the same stub), and the answer is to leave the album's names alone and say so.
    """
    by: dict[str, list[PlanTrack]] = {}
    for track in plan.tracks:
        if track.state == "done":
            by.setdefault(wanted_filename(plan, track), []).append(track)
    return {name: tracks for name, tracks in by.items() if len(tracks) > 1}


TITLE_FLOOR = 40     # characters of the title a name keeps before anything else is given up


def _fits(stem: str, ext: str) -> bool:
    return len(f"{safe_name(stem)}.{ext}".encode()) <= MAX_NAME_BYTES


def _cut(text: str, room: int) -> str:
    """`text` shortened to fit `room` bytes, on a character boundary."""
    while len(text.encode()) > max(room, 1):
        text = text[:-1].rstrip(" .")
    return text


def track_filename(
    albumartist: str, album: str, number: int, artist: str | None, title: str, disc: int | None = None, ext: str = "opus"
) -> str:
    """v1's convention: 'AlbumArtist - Album - [D-]NN - [TrackArtist - ]Title.opus'.

    **What gets shorter when the name is too long is decided here, in order** (R-410, ruling 4),
    rather than by `safe_name` cutting whatever happens to be at the end — which is always the
    title, the one part that says which song this is. First the album artist goes, because the
    folder above already says it; then the album, for the same reason; and only then the title
    itself, which keeps at least `TITLE_FLOOR` characters and is marked with an ellipsis so that a
    reader can see it was cut.
    """
    # **the two parts that name a folder are sanitised the way that folder is** (§9, slice 116,
    # R-455 item 2). `safe_name` strips " ." from the ends of what it is given, and it used to be
    # given the whole assembled stem — so an album whose name begins with dots lost them in its
    # *folder* and kept them in every *file*:
    # `Crematory/Just Dreaming/Crematory - ...Just Dreaming - 01 - ….mp3` on the user's share.
    # **The title and the track artist are not touched here**: a song called `... Just Dreaming` is
    # called that, and the first version of this renamed two of the user's files to take an ellipsis
    # out of a song's name. What cannot go in a filename at all is still the whole-stem pass below.
    albumartist, album = safe_name(albumartist), safe_name(album)
    middle = f"{artist} - {title}" if artist else title
    num = f"{disc}-{number:02d}" if disc else f"{number:02d}"
    for stem in (f"{albumartist} - {album} - {num} - {middle}",
                 f"{album} - {num} - {middle}",
                 f"{num} - {middle}"):
        if _fits(stem, ext):
            return f"{safe_name(stem)}.{ext}"
    room = MAX_NAME_BYTES - len(f"{safe_name(num)} - ….{ext}".encode())
    return f"{safe_name(f'{num} - {_cut(middle, max(room, TITLE_FLOOR))}…')}.{ext}"


# -- how long the song should be -------------------------------------------------------
#
# Three opinions can exist per track: the file on disk, MusicBrainz, and lrclib (which
# answers even when its recording was too far off to take the lyrics from). Where they
# disagree badly the track is usually not what it claims to be — a teaser, a commentary
# clip, or an upload with a label ident in front. Measured over 3032 comparable tracks:
# 13% are >5s longer than MusicBrainz, so only a wide gap is worth showing (DESIGN.md §9, slice 18).

# These four are the source of truth. The album flag below uses LENGTH_BIG, LENGTH_STUB and
# ALBUM_SHARE; the per-track mark is drawn in the page, so `LENGTH` in webui/app.js mirrors
# all of them — LENGTH_SLACK exists only for that mirror. Change a number here and there.
LENGTH_SLACK = 5.0  # below this nothing is said: masters, fades and count-ins differ
LENGTH_BIG = 20.0  # a gap worth marking on the track
LENGTH_STUB = 0.6  # a file this much shorter than the song is not that recording at all
ALBUM_SHARE = 0.5  # this many of an album's comparable tracks off the same way flags the album


def reference_length(track: PlanTrack) -> float | None:
    """How long the song is according to somebody other than YouTube."""
    return track.mb_length or track.lyrics_length


def effective_length(track: PlanTrack) -> float | None:
    """How long our audio is: measured when we have measured it, else the video minus the trims."""
    if track.file_length:
        return track.file_length
    if not track.duration:
        return None
    return (track.trim_end if track.trim_end else track.duration) - (track.trim_start or 0)


def length_gap(track: PlanTrack) -> float | None:
    """Ours minus theirs, in seconds; None when nobody else has an opinion."""
    ours, theirs = effective_length(track), reference_length(track)
    return None if ours is None or not theirs else ours - theirs


def trimmed_gap(track: PlanTrack, start: float | None, end: float | None) -> tuple[float | None, float | None]:
    """What the file would be, and how far from the reference, if it were cut to these marks.

    The length to cut from is the **video's** duration, because trim points count from the start of
    the video and a file already cut is played from its kept original (§9, slice 17). Returns
    (kept seconds, kept minus the reference); the second is None when nobody knows the song's
    length. `webui/app.js` mirrors this in `trimTarget` — change one and change the other.
    """
    total = track.duration
    if not total:
        return None, None
    kept = (total if end is None else min(end, total)) - (start or 0)
    ref = reference_length(track)
    return kept, (kept - ref if ref else None)


def is_stub(track: PlanTrack) -> bool:
    """A file far shorter than the song: a snippet, a teaser, or a match that is plain wrong."""
    ours, theirs = effective_length(track), reference_length(track)
    return bool(ours and theirs and ours < theirs * LENGTH_STUB)


def album_length_flag(plan: AlbumPlan) -> dict[str, object] | None:
    """`{way, n, of}` when an album is worth looking at, else None.

    Two faults, two messages. A *stub* is strong on its own — 8 of 3946 tracks are one, and
    each turned out to be a snippet, a radio edit or a wrong recording. A whole album being
    off the same way means the release we matched is not the one we downloaded (a Sabaton
    "album" of 11 track-commentary clips is what this first caught), and that needs more than
    two tracks to say: at two, a compilation where only four tracks can be compared at all
    would be flagged for a pair of long folk songs.
    """
    gaps = [g for t in plan.tracks if t.state == "done" and (g := length_gap(t)) is not None]
    if len(gaps) < 2:
        return None
    if stubs := sum(is_stub(t) for t in plan.tracks if t.state == "done"):
        return {"way": "stub", "n": stubs, "of": len(gaps)}
    for way, n in (("short", sum(g < -LENGTH_BIG for g in gaps)), ("long", sum(g > LENGTH_BIG for g in gaps))):
        if n >= max(3, len(gaps) * ALBUM_SHARE):
            return {"way": way, "n": n, "of": len(gaps)}
    return None


# -- small helpers -------------------------------------------------------------------


_key = key


def _most_common(pairs: list[tuple[str, Provenance]]) -> tuple[str, Provenance] | None:
    if not pairs:
        return None
    best_key, _ = Counter(_key(v) for v, _ in pairs).most_common(1)[0]
    return next(p for p in pairs if _key(p[0]) == best_key)


def _most_common_value[T](values: list[T]) -> T | None:
    return Counter(values).most_common(1)[0][0] if values else None


def _shared(values: list[str | None]) -> str | None:
    """The value if every entry has the same non-empty one."""
    present = {_key(v): v for v in values if v}
    return next(iter(present.values())) if len(present) == 1 and all(values) else None
