"""An album folder holds no audio its plan does not name (DESIGN §9, slice 55).

Slice 49's sentence is *noaap never removes audio, it only moves it to the bin*. Half of it was
kept: a fetch that landed under a new name left the old file beside the new one — nothing removed,
and nothing put away either, so a player scanning the folder saw the song twice. Found on
2026-09-28 by taking a FLAC over an Opus through the UI.

The cases here are written as **one rule over every shape of switch**, because the shapes are what
kept getting missed: a copy taken from another provider, a different audio stream of the same video,
and a video named by hand.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from test_folder_source import encode

from noaap import sources
from noaap.config import Config
from noaap.download import run, save_plan
from noaap.models import AlbumPlan, Candidate, Entry, Kind, PlanTrack
from noaap.recycle import entries as bin_entries
from noaap.service import apply_user_edits

AUDIO = (".opus", ".m4a", ".mp3", ".flac", ".ogg", ".webm")


def strays(album_dir: Path, plan: AlbumPlan) -> list[str]:
    """Audio lying in the album folder that no track names — what a player scanning it would see
    twice. The dotted working folders are not the album: `.parts` is a download in progress,
    `.originals` holds what a trim cut from, keyed by the ref it belongs to."""
    named = {t.filename for t in plan.tracks}
    return sorted(p.name for p in album_dir.iterdir()
                  if p.is_file() and p.suffix.lower() in AUDIO and p.name not in named)


class Handing:
    """A source that hands over one prepared file, whatever is asked of it."""

    name = "handing"

    def __init__(self, file: Path) -> None:
        self.file = file

    def capabilities(self) -> frozenset[str]:
        return frozenset()

    def audio(self, ref: str, into: Path, choice: str = "best") -> Path:
        into.mkdir(parents=True, exist_ok=True)
        out = into / self.file.name
        shutil.copy(self.file, out)
        return out

    def probe(self, ref: str) -> Entry:
        return Entry(video_id=ref, position=1, title="One", channel=None, duration=31.0)

    def art(self, address: str) -> bytes:
        raise OSError("no cover here")

    def settings(self) -> dict[str, Any]:
        return {}


@pytest.fixture
def album(tmp_path):
    """One done track in a library, with a real Opus file on disk."""
    library = tmp_path / "library"
    track = PlanTrack(video_id="aaaaaaaaaaa", number=1, artist="A Band", title="One",
                      filename="A Band - An Album - 01 - One.opus", provenance={}, state="done")
    plan = AlbumPlan(source_url="https://y/1", source_id="p1", kind=Kind.OFFICIAL_ALBUM,
                     album="An Album", albumartist="A Band", year=None, cover_url=None,
                     folder="A Band/An Album", tracks=[track])
    album_dir = library / plan.folder
    encode(album_dir / track.filename, title="One", artist="A Band")
    save_plan(plan, album_dir)
    return library, album_dir, plan


# -- the rule, over every shape of switch ------------------------------------------------------------


def test_a_copy_taken_in_another_format_puts_the_old_file_away(album, tmp_path):
    """"take this one" on a FLAC copy: the shape that found this."""
    library, album_dir, plan = album
    copy = encode(tmp_path / "elsewhere" / "One.flac", title="One", artist="A Band")
    plan.tracks[0].candidates.append(Candidate(ref=str(copy), provider="folder", added_by="pass",
                                               undecided=True, why="nothing could choose"))
    apply_user_edits(plan, {"tracks": [{"video_id": "aaaaaaaaaaa", "take": str(copy)}]})

    run(plan, album_dir, Handing(copy), track_source=lambda t: Handing(copy))

    assert plan.tracks[0].filename.endswith(".flac") and (album_dir / plan.tracks[0].filename).is_file()
    assert strays(album_dir, plan) == [], "the Opus it replaced is not lying beside it"
    binned = bin_entries(library)
    assert len(binned) == 1 and binned[0].reason.startswith("replaced by ")


def test_a_different_audio_stream_of_the_same_video_does_too(album, tmp_path):
    """The `audio_choice` switch, opus ↔ m4a: the same shape without a second provider."""
    library, album_dir, plan = album
    other = encode(tmp_path / "elsewhere" / "One.m4a", title="One", artist="A Band")
    apply_user_edits(plan, {"tracks": [{"video_id": "aaaaaaaaaaa", "audio_choice": "combined"}]})

    run(plan, album_dir, Handing(other), track_source=lambda t: Handing(other))

    assert plan.tracks[0].filename.endswith(".m4a")
    assert strays(album_dir, plan) == []
    assert len(bin_entries(library)) == 1


def test_the_same_format_writes_over_itself_and_bins_nothing(album, tmp_path):
    """A video named in the panel, in the format the track already has: there is nothing to put
    away, and an entry for a file that was overwritten in place would be a lie."""
    library, album_dir, plan = album
    again = encode(tmp_path / "elsewhere" / "Another.opus", title="One", artist="A Band")
    apply_user_edits(plan, {"tracks": [{"video_id": "aaaaaaaaaaa", "source": "bbbbbbbbbbb"}]})

    run(plan, album_dir, Handing(again), track_source=lambda t: Handing(again))

    assert strays(album_dir, plan) == []
    assert bin_entries(library) == []


def test_what_a_trim_kept_stays_with_the_ref_it_belongs_to(album, tmp_path):
    """The one thing that is *not* a stray. A kept original is filed under the ref it was cut from,
    in `.originals`, and the track can go back to that ref — so switching away from it does not
    make that file loose, and it is not put away with the file the switch displaced."""
    from noaap.trim import ORIGINALS

    library, album_dir, plan = album
    track = plan.tracks[0]
    kept = album_dir / ORIGINALS / f"{track.video_id}.opus"
    kept.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(album_dir / track.filename, kept)
    copy = encode(tmp_path / "elsewhere" / "One.flac", title="One", artist="A Band")
    track.candidates.append(Candidate(ref=str(copy), provider="folder", added_by="pass"))
    apply_user_edits(plan, {"tracks": [{"video_id": "aaaaaaaaaaa", "take": str(copy)}]})

    run(plan, album_dir, Handing(copy), track_source=lambda t: Handing(copy))

    assert strays(album_dir, plan) == []
    assert kept.is_file(), "the playlist's own video is still one of this track's candidates"
    assert bin_entries(library)[0].audio, "and the entry that was written holds the displaced file"


# -- and the root it needs to do any of that ---------------------------------------------------------


def test_the_library_root_is_derived_from_the_album_and_never_passed(album):
    from noaap.download import library_of

    library, album_dir, plan = album

    assert library_of(album_dir, plan) == library
    assert library_of(Path("/somewhere/else"), plan) is None, "not every folder sits in a library"


def test_a_ref_that_is_a_path_can_still_be_a_kept_originals_name(tmp_path):
    """The crash this found. `.originals` is keyed by the ref, and a folder's ref is a path: used
    as a glob it raised `Non-relative patterns are unsupported` **after** `bin_track` had already
    moved the audio, leaving an entry with no `bin.json` that nothing lists and nothing restores.
    """
    from noaap.trim import ORIGINALS, key, originals_of

    assert key("aaaaaaaaaaa") == "aaaaaaaaaaa", "a video id is already a name, and stays its own"
    assert "/" not in key("/home/someone/Music/A Band/01 - One.flac")

    folder = tmp_path / ORIGINALS
    folder.mkdir()
    ref = "/home/someone/Music/A Band/01 - One.flac"
    (folder / f"{key(ref)}.flac").write_bytes(b"audio")

    assert [p.name for p in originals_of(tmp_path, ref)] == [f"{key(ref)}.flac"]


def test_binning_a_track_whose_audio_came_from_a_folder_writes_a_whole_entry(tmp_path):
    """The same crash from the other side: the entry is complete, listed and restorable."""
    from noaap.models import Kind
    from noaap.recycle import bin_track
    from noaap.trim import ORIGINALS

    library = tmp_path / "library"
    track = PlanTrack(video_id="aaaaaaaaaaa", number=1, artist="A Band", title="One",
                      filename="01 - One.flac", provenance={}, state="done",
                      source_override="/home/someone/Music/A Band/01 - One.flac")
    plan = AlbumPlan(source_url="https://y/1", source_id="p1", kind=Kind.OFFICIAL_ALBUM,
                     album="An Album", albumartist="A Band", year=None, cover_url=None,
                     folder="A Band/An Album", tracks=[track])
    album_dir = library / plan.folder
    (album_dir / ORIGINALS).mkdir(parents=True)
    (album_dir / track.filename).write_bytes(b"audio")

    bin_track(library, album_dir, plan, track, reason="deleted", audio=album_dir / track.filename)

    listed = bin_entries(library)
    assert len(listed) == 1 and listed[0].title == "One", "the entry is listed, not a loose folder"
