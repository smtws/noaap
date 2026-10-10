"""Mending a plan that names files it does not have (DESIGN §9, slice 163; R-566, P119).

The update of 2026-10-10 left four albums in three shapes, and `repair` mends each from what is on
disk — it never fetches, and it never renames here: it only makes the plan say what is there, and
the pass that follows brings the names into line, renames first (`download._renames_first`).

1. **An intact file the plan lost track of** (`In Extremo`, `Saltatio Mortis`): a finished track
   whose file is missing gets its own file back — the one its id names (a folder's ids are paths)
   or the one it was adopted as — when no other entry holds that file.
2. **Two entries for one song, numbered from somewhere else** (the same two): where an entry was
   given back its file and another entry carries the same title, each goes back to the title, disc
   and number its own file carried when it was adopted. MusicBrainz had seated a plain song on the
   release's mix (§9, slice 163's seat rule now refuses that), and the numbers followed.
3. **Two entries, one file** (`Metallica`, `Depeche Mode`): a finished track whose file is missing,
   and one other finished track of the same song whose file is there, are one track — the one the
   source lists, holding the surviving file. Which video the audio is, the file's length says when
   only one of the two videos is that long; otherwise the plan says it does not know.
"""
from __future__ import annotations

from pathlib import Path

from .models import AlbumPlan, Candidate, PlanTrack, Provenance
from .tag import measure
from .text import key

AUDIO = (".opus", ".m4a", ".mp4", ".mp3", ".flac", ".ogg", ".oga", ".wav", ".aac", ".wma", ".webm")
SAME_LENGTH = 1.0     # seconds: the same recording, measured from two containers
LYRICS_FIELDS = ("lyrics", "lyrics_id", "lyrics_length", "lyrics_sha", "lyrics_rejected", "lyrics_for_source",
                 "lyrics_for_length", "lyrics_timed_by", "lyrics_words_by", "lyrics_published",
                 "lyrics_checked", "lyrics_fit", "lyrics_no_entry")


def _audio_here(album_dir: Path) -> set[str]:
    return {str(p.relative_to(album_dir)) for p in album_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in AUDIO
            and not any(part.startswith(".") for part in p.relative_to(album_dir).parts)}


def _first(value: object) -> str:
    while isinstance(value, list | tuple) and value:
        value = value[0]
    return str(value or "")


def _number(text: str) -> int | None:
    head = text.split("/")[0].strip()
    return int(head) if head.isdigit() and int(head) > 0 else None


def adopted_place(track: PlanTrack) -> tuple[str | None, int | None, int | None]:
    """Title, disc and number the track's file carried when it was adopted, as far as it said."""
    tags = track.adopted_tags or {}
    title = next((_first(tags[k]) for k in ("title", "TIT2", "©nam") if k in tags), None)
    number = next((_number(_first(tags[k])) for k in ("tracknumber", "TRCK") if k in tags), None)
    if number is None and "trkn" in tags:
        pair = tags["trkn"][0] if isinstance(tags["trkn"], list | tuple) and tags["trkn"] else tags["trkn"]
        number = _number(str(pair[0])) if isinstance(pair, list | tuple) and pair else None
    disc = next((_number(_first(tags[k])) for k in ("discnumber", "TPOS", "disk") if k in tags), None)
    return title, disc, number


def mend(plan: AlbumPlan, album_dir: Path, given_back: list[PlanTrack] = ()) -> list[str]:
    """Make the plan name what is on disk; one line per thing it changed. Writes nothing itself.

    `given_back` are tracks another step has just given their file back (`Service.find_again`, which
    answers from the collection and runs first): shape 2 is asked for them as for its own.
    """
    said: list[str] = []
    here = _audio_here(album_dir)
    lost = [t for t in plan.tracks if t.state == "done" and t.filename and not (album_dir / t.filename).is_file()]
    if not lost and not given_back:
        return said
    held = {t.filename for t in plan.tracks if t.filename and (album_dir / t.filename).is_file()}
    loose = here - held
    attached: list[PlanTrack] = []
    # 1. a file the plan lost track of
    for t in lost:
        mine = []
        if isinstance(t.video_id, str) and t.video_id.startswith(str(album_dir) + "/"):
            mine.append(str(Path(t.video_id).relative_to(album_dir)))
        if t.adopted_name:
            mine.append(t.adopted_name)
        found = next((m for m in mine if m in loose), None)
        if found:
            said.append(f"{t.disc}-{t.number:02d} {t.title}: its file is here as {found} — the plan names it again")
            t.filename, t.tagged = found, None
            loose.discard(found)
            attached.append(t)
    # 2. entries of one title, one of them just given its file back: each to its own file's place
    for t in attached + [t for t in given_back if t not in attached]:
        group = [o for o in plan.tracks if key(o.title) == key(t.title) and o.state == "done"]
        if len(group) < 2:
            continue
        for o in group:
            title, disc, number = adopted_place(o)
            if title is None or number is None:
                continue
            disc = disc or 1
            if (o.title, o.disc, o.number) == (title, disc, number):
                continue
            if any(x is not o and (x.disc, x.number) == (disc, number) and x not in group for x in plan.tracks):
                said.append(f"{o.disc}-{o.number:02d} {o.title}: its file says {disc}-{number:02d} {title}, "
                            "but another track holds that place — left as it is")
                continue
            said.append(f"{o.disc}-{o.number:02d} {o.title} → {disc}-{number:02d} {title}: what its own file says")
            if o.title != title:
                o.title = o.auto["title"] = title
                o.provenance["title"] = Provenance.FILE_TAGS
                o.mbid, o.mb_length = None, None     # the recording id was the other take's
            o.disc, o.number, o.tagged = disc, number, None
    # 3. two entries, one file
    for t in [t for t in lost if t not in attached]:
        # **only the shape the swap leaves** (R-566): a source's new video and the entry it replaced,
        # so exactly one of the two has left the source. A folder's ids are its files, and two songs
        # of one name on two discs of a live album are two tracks — one of them simply lost.
        siblings = [o for o in plan.tracks if o is not t and o.state == "done"
                    and key(o.title) == key(t.title) and key(o.artist) == key(t.artist)
                    and o.in_source != t.in_source
                    and o.filename and (album_dir / o.filename).is_file()] if plan.provider != "folder" else []
        if len(siblings) != 1:
            said.append(f"{t.disc}-{t.number:02d} {t.title}: its file is missing and nothing here is it")
            continue
        s = siblings[0]
        keep, other = (t, s) if t.in_source or not s.in_source else (s, t)
        file = album_dir / s.filename
        length = measure(file)[0]
        mine = [x for x in (keep, other) if length and x.duration and abs(length - x.duration) <= SAME_LENGTH]
        audio = mine[0] if len(mine) == 1 else None
        keep.filename, keep.tagged, keep.state = s.filename, None, "done"
        keep.file_length = length
        for name in LYRICS_FIELDS:              # the words beside the file are the file's
            setattr(keep, name, getattr(s, name))
        if "lyrics" in s.provenance:
            keep.provenance["lyrics"] = s.provenance["lyrics"]
        if not keep.candidate(other.video_id):
            keep.candidates.append(Candidate(ref=other.video_id, provider=other.provider_in(plan),
                                             length=other.duration, added_by="pass",
                                             why="the other video of this song in this album"))
        keep.source_override = other.video_id if audio is other else None
        keep.sync_candidates()
        plan.tracks.remove(other)
        whose = ("the file is the listed video's" if audio is keep else
                 f"the file is {other.video_id}'s, kept as its audio" if audio is other else
                 "which of the two videos the file holds, its length cannot tell")
        said.append(f"{keep.disc}-{keep.number:02d} {keep.title}: one track again, holding {s.filename} — {whose}")
    return said

