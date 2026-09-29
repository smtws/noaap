"""Every finished track with a file gets that file's length (DESIGN §9, slice 56).

The measuring was never missing. What was missing is a pass that reaches the tracks: `repair` skips
an album whose names are already right, *before* the point where anything is measured, and in a tidy
library that is nearly every album. So 574 of the reference library's 3946 tracks had a file, no
length, no length chip and no near-miss check — because nothing ever asked the file.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from test_folder_source import encode

from noaap.config import Config
from noaap.download import load_plan, save_plan
from noaap.models import AlbumPlan, Kind, PlanTrack
from noaap.service import Outcome, Service


def album(tmp_path: Path, *, state: str = "done", file: bool = True) -> tuple[Path, Path, AlbumPlan]:
    library = tmp_path / "library"
    tracks = [PlanTrack(video_id=f"id-{n}", number=n, artist="A Band", title=f"Track {n}",
                        filename=f"A Band - An Album - {n:02d} - Track {n}.opus", provenance={},
                        state=state)
              for n in (1, 2)]
    plan = AlbumPlan(source_url="https://y/1", source_id="p1", kind=Kind.OFFICIAL_ALBUM,
                     album="An Album", albumartist="A Band", year=None, cover_url=None,
                     folder="A Band/An Album", tracks=tracks)
    album_dir = library / plan.folder
    for track in tracks:
        if file:
            encode(album_dir / track.filename, title=track.title, artist="A Band")
    save_plan(plan, album_dir)
    return library, album_dir, plan


def service(library: Path) -> Service:
    return Service(Config(musicbrainz=False, lyrics=False), library, log=lambda s: None)


# -- the measuring itself ------------------------------------------------------------------------


def test_a_track_with_a_file_and_no_length_gets_one(tmp_path):
    library, album_dir, plan = album(tmp_path)

    assert service(library).measure_lengths(plan, album_dir) == 2
    assert all(t.file_length and 30 < t.file_length < 32 for t in plan.tracks)


def test_how_it_was_measured_is_recorded(tmp_path):
    """Three orders of magnitude apart, and a decoded answer is a fact about the file: its own
    header would not say."""
    library, album_dir, plan = album(tmp_path)

    service(library).measure_lengths(plan, album_dir)

    assert [t.file_length_by for t in plan.tracks] == ["header", "header"]


def test_a_length_already_known_is_not_measured_again(tmp_path):
    library, album_dir, plan = album(tmp_path)
    plan.tracks[0].file_length = 99.0

    assert service(library).measure_lengths(plan, album_dir) == 1
    assert plan.tracks[0].file_length == 99.0, "and it is left exactly as it was"


@pytest.mark.parametrize("state,file,expected", [("pending", True, 0), ("done", False, 0)])
def test_only_a_finished_track_with_a_file_is_asked(tmp_path, state, file, expected):
    library, album_dir, plan = album(tmp_path, state=state, file=file)

    assert service(library).measure_lengths(plan, album_dir) == expected


def test_a_dry_run_counts_and_writes_nothing(tmp_path):
    library, album_dir, plan = album(tmp_path)
    before = (album_dir / ".ytalbum.json").read_bytes()

    assert service(library).measure_lengths(plan, album_dir, dry_run=True) == 2
    assert all(t.file_length is None for t in plan.tracks)
    assert (album_dir / ".ytalbum.json").read_bytes() == before


# -- and the passes that have to reach them --------------------------------------------------------


def test_repair_measures_an_album_whose_names_are_already_right(tmp_path):
    """The defect itself: this album needs no tidying, and used to be skipped before anything
    measured it."""
    library, album_dir, plan = album(tmp_path)

    service(library).repair()

    assert [t.file_length_by for t in load_plan(album_dir).tracks] == ["header", "header"]


def test_a_dry_repair_writes_nothing_at_all(tmp_path):
    library, album_dir, plan = album(tmp_path)
    before = {p: p.read_bytes() for p in sorted(album_dir.rglob("*")) if p.is_file()}

    outcomes = service(library).repair(dry_run=True)

    assert outcomes and all(o.status == "ok" for o in outcomes)
    assert {p: p.read_bytes() for p in sorted(album_dir.rglob("*")) if p.is_file()} == before


def test_the_plan_only_gains_fields(tmp_path):
    """The format is additive: a length and how it was read, and nothing else moves.

    Measured on the step alone. A whole `repair` may also retag a file that noaap never tagged,
    which is its own long-standing business and not this."""
    library, album_dir, plan = album(tmp_path)
    before = load_plan(album_dir).to_dict()

    service(library).measure_lengths(plan, album_dir)
    save_plan(plan, album_dir)

    after = load_plan(album_dir).to_dict()
    for was, now in zip(before["tracks"], after["tracks"], strict=True):
        changed = {k for k in was if was[k] != now[k]}
        assert changed == {"file_length", "file_length_by"}, changed
    assert {k: v for k, v in before.items() if k != "tracks"} == {k: v for k, v in after.items() if k != "tracks"}


def test_update_measures_the_albums_it_touches(tmp_path, monkeypatch):
    """`update` is where a library that is used gets them; `repair` is for the rest."""
    library, album_dir, plan = album(tmp_path)
    seen = service(library)
    monkeypatch.setattr(type(seen), "fetch", lambda self, *a, **kw: Outcome("ok", plan, album_dir))
    monkeypatch.setattr(type(seen), "_unchanged", lambda self, p: None)

    seen.update_all()

    assert [t.file_length_by for t in load_plan(album_dir).tracks] == ["header", "header"]


def test_a_dry_update_writes_nothing(tmp_path, monkeypatch):
    library, album_dir, plan = album(tmp_path)
    seen = service(library)
    monkeypatch.setattr(type(seen), "fetch", lambda self, *a, **kw: Outcome("ok", plan, album_dir))
    monkeypatch.setattr(type(seen), "_unchanged", lambda self, p: None)
    before = (album_dir / ".ytalbum.json").read_bytes()

    seen.update_all(report_only=True)

    assert (album_dir / ".ytalbum.json").read_bytes() == before


def test_a_length_whose_origin_nobody_recorded_gets_one(tmp_path):
    """`file_length_by` was added after most plans were written, and `run` measures a track only
    when it has no length at all — so the field stayed empty on every track that already had one:
    **4583 of one real library's tracks against 559 with it** (§9, slice 77). This fills it in,
    from the header alone, and never changes the number it describes."""
    library, album_dir, plan = album(tmp_path)
    service(library).measure_lengths(plan, album_dir)
    known = plan.tracks[0].file_length
    plan.tracks[0].file_length_by = None

    assert service(library).measure_lengths(plan, album_dir) == 1
    assert plan.tracks[0].file_length_by == "header"
    assert plan.tracks[0].file_length == known, "the number it describes is untouched"


def test_but_not_when_the_header_says_something_else(tmp_path):
    """Then the plan's number is not the header's, whatever else it may be, and an empty origin is
    the honest answer. A length may have come from a trim or from somebody's own edit."""
    library, album_dir, plan = album(tmp_path)
    plan.tracks[0].file_length, plan.tracks[0].file_length_by = 99.0, None

    service(library).measure_lengths(plan, album_dir)

    assert plan.tracks[0].file_length == 99.0 and plan.tracks[0].file_length_by is None
