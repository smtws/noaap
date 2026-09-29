"""An adopted album whose discs are sub-folders (DESIGN §9, slice 61).

The defect this file exists for: a plan's `filename` was a bare name, so for a track in a disc
sub-folder `album_dir / filename` was **not** the file. Every pass then found the track's own file
missing and fetched it again — which for a folder is a copy — into the album root under noaap's own
naming scheme. One `noaap update` on a never-moved adopted library wrote **64 files** for the 67
tracks of three real albums, and three of those copies landed on each other's names, so two
different recordings became one file with two tracks pointing at it.

1245 tests passed over that behaviour. None of them had an album with a sub-folder in it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_folder_source import encode

from noaap import sources
from noaap.config import Config

AUDIO = {".mp3", ".flac", ".opus", ".m4a"}


@pytest.fixture
def discs(tmp_path) -> Path:
    """One album, two discs, each in its own folder — and nothing in the album's root.

    Modelled on the three real ones: `cd1`/`cd2`, `CD 1`/`CD 2` and `1`/`2`, 24, 22 and 21 tracks.
    The two tracks called the same thing on both discs are the collision: with a bare name they
    derive to one filename.
    """
    album = tmp_path / "In Extremo" / "Am goldenen Rhein-Live"
    for disc, titles in ((1, ["Zauberspruch", "Sieben Koeche"]), (2, ["Zauberspruch", "Flaschenpost"])):
        for n, title in enumerate(titles, 1):
            encode(album / f"cd{disc}" / f"{n:02d} - {title}.mp3", title=title, artist="In Extremo",
                   album="Am goldenen Rhein-Live", album_artist="In Extremo", track=str(n),
                   disc=str(disc))
    return album


def folder():
    return sources.get("folder", Config())


def adopt_it(library: Path) -> tuple[Path, object]:
    """Adopt every album under `library`, as `noaap adopt --apply` does."""
    from noaap.adopt import carry_out, survey

    found = survey(library, library, folder())
    carry_out(found)
    return found.taking[0].album_dir, found.taking[0].plan


def audio_in(library: Path) -> list[str]:
    return sorted(str(p.relative_to(library)) for p in library.rglob("*")
                  if p.is_file() and p.suffix.lower() in AUDIO)


# -- what the plan says ----------------------------------------------------------------------------


def test_the_plan_says_where_the_file_is_not_only_what_it_is_called(discs, tmp_path):
    album_dir, plan = adopt_it(tmp_path)

    assert sorted(t.filename for t in plan.tracks) == [
        "cd1/01 - Zauberspruch.mp3", "cd1/02 - Sieben Koeche.mp3",
        "cd2/01 - Zauberspruch.mp3", "cd2/02 - Flaschenpost.mp3",
    ]
    assert all(t.filename == t.adopted_name for t in plan.tracks), "the name it had is the path it had"


def test_two_discs_with_the_same_track_are_two_files(discs, tmp_path):
    """The bare name made these one. Two recordings, one file, two tracks pointing at it."""
    album_dir, plan = adopt_it(tmp_path)

    assert len({t.filename for t in plan.tracks}) == len(plan.tracks)


def test_every_adopted_track_is_where_its_plan_says(discs, tmp_path):
    """The instrument that should have caught this on the day: slice 60's own check. Before the fix
    it reported all 67 tracks of the three real albums as not where their plan says."""
    from noaap.download import load_plan, lost_files

    album_dir, _ = adopt_it(tmp_path)

    assert lost_files(album_dir, load_plan(album_dir)) == []


def test_the_sidecar_goes_into_the_disc_folder_beside_its_audio(discs, tmp_path):
    from noaap.lyrics import sidecar_path

    album_dir, plan = adopt_it(tmp_path)

    assert sidecar_path(album_dir, plan.tracks[0].filename) == album_dir / "cd1" / "01 - Zauberspruch.lrc"


@pytest.mark.parametrize("filename,inside", [
    ("cd1/01 - Zauberspruch.mp3", True),
    ("01 - Zauberspruch.mp3", True),
    ("../../something", False),
    ("cd1/../../../etc/passwd", False),
    ("/etc/passwd", False),
    ("", False),
])
def test_what_counts_as_inside_the_album(tmp_path, filename, inside):
    """Widened to any depth, and no further: a plan is a file on disk and a tampered one can climb."""
    from noaap.service import _inside

    assert (_inside(tmp_path, filename) is not None) is inside


# -- the rule (R-210, do 2) ------------------------------------------------------------------------


def passes_over(library: Path, album_dir: Path):
    """Every offline pass that walks an adopted library, as a name and a callable."""
    from noaap.download import load_plan, run
    from noaap.service import Service

    cfg = Config()
    cfg.library_root = library
    service = Service(cfg, library)
    yield "repair", lambda: service.repair(dry_run=False)
    yield "repair --dry-run", lambda: service.repair(dry_run=True)
    yield "reread", lambda: service.reread(album_dir)
    yield "download.run", lambda: run(load_plan(album_dir), album_dir, folder(),
                                      track_source=lambda t: folder())
    yield "update_all", lambda: service.update_all()
    yield "update_all --dry-run", lambda: service.update_all(report_only=True)
    yield "update_all --deep", lambda: service.update_all(deep=True)


def test_no_pass_over_an_adopted_library_adds_or_removes_a_file(discs, tmp_path):
    """**The rule, for every pass there is** (R-210): afterwards the set of audio files in the
    library is the set it had, name for name. `update` broke it by 64 files on three real albums;
    the executor underneath it is what every other pass shares.
    """
    album_dir, _ = adopt_it(tmp_path)
    was = audio_in(tmp_path)
    assert len(was) == 4

    for name, run_it in passes_over(tmp_path, album_dir):
        run_it()
        assert audio_in(tmp_path) == was, f"{name} changed the files in the library"


def test_and_no_pass_renames_one(discs, tmp_path):
    """Its own case, because a rename leaves the count alone and is just as much a broken promise."""
    from noaap.download import load_plan

    album_dir, _ = adopt_it(tmp_path)
    was = {t.filename for t in load_plan(album_dir).tracks}

    for _name, run_it in passes_over(tmp_path, album_dir):
        run_it()

    assert {t.filename for t in load_plan(album_dir).tracks} == was
    assert {t.adopted_name for t in load_plan(album_dir).tracks} == was


def test_a_track_is_not_read_twice_into_the_plan(discs, tmp_path):
    """The doubling that followed: once a copy sat in the album root, the next read of the folder
    saw two files per recording and appended the copies as new tracks — 24 tracks became 48."""
    from noaap.download import load_plan
    from noaap.service import Service

    album_dir, _ = adopt_it(tmp_path)
    cfg = Config()
    cfg.library_root = tmp_path
    service = Service(cfg, tmp_path)

    for _ in range(3):
        service.update_all()
        service.reread(album_dir)

    assert len(load_plan(album_dir).tracks) == 4
    assert len(audio_in(tmp_path)) == 4


# -- what noaap may leave behind, and what an undo takes back (R-210, do 3) -------------------------


def test_what_a_pass_writes_beside_the_audio_is_noaaps_and_an_undo_takes_it_back(discs, tmp_path):
    from noaap.adopt import give_back
    from noaap.download import PLAN_FILE, load_plan
    from noaap.service import Service

    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())
    album_dir, _ = adopt_it(tmp_path)
    cfg = Config()
    cfg.library_root = tmp_path
    Service(cfg, tmp_path).update_all()

    added = [p for p in sorted(tmp_path.rglob("*"))
             if p.is_file() and str(p.relative_to(tmp_path)) not in before]
    assert all(p.name == PLAN_FILE or p.suffix in (".lrc", ".jpg", ".png") for p in added), \
        f"a pass over an adopted album writes only plans, sidecars and covers: {added}"

    give_back(album_dir, load_plan(album_dir), tmp_path)

    left = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())
    assert left == before, "and an undo leaves the collection exactly as it was"


# -- and the libraries 1.5.0 already adopted (R-210, do 1) ------------------------------------------


def as_1_5_0_adopted(album_dir: Path) -> None:
    """The plan as 1.5.0 wrote it: the bare name of a file that is in a disc sub-folder."""
    from noaap.download import load_plan, save_plan

    plan = load_plan(album_dir)
    for track in plan.tracks:
        track.filename = track.adopted_name = Path(track.filename).name
    save_plan(plan, album_dir)


def test_repair_finds_the_files_a_1_5_0_plan_lost(discs, tmp_path):
    """**Fixing the writer does not fix the plans already written.** Without this, a library adopted
    by 1.5.0 still has its files copied into the album root by the first `update` after the upgrade.
    """
    from noaap.download import load_plan, lost_files
    from noaap.service import Service

    album_dir, _ = adopt_it(tmp_path)
    as_1_5_0_adopted(album_dir)
    assert len(lost_files(album_dir, load_plan(album_dir))) == 4, "the plan has lost all four"

    cfg = Config()
    cfg.library_root = tmp_path
    Service(cfg, tmp_path).repair(dry_run=False)

    plan = load_plan(album_dir)
    assert lost_files(album_dir, plan) == []
    assert sorted(t.filename for t in plan.tracks) == sorted(t.adopted_name for t in plan.tracks)
    assert all("/" in t.filename for t in plan.tracks)


def test_and_then_no_pass_copies_anything(discs, tmp_path):
    from noaap.service import Service

    album_dir, _ = adopt_it(tmp_path)
    as_1_5_0_adopted(album_dir)
    was = audio_in(tmp_path)

    cfg = Config()
    cfg.library_root = tmp_path
    service = Service(cfg, tmp_path)
    service.repair(dry_run=False)
    service.update_all()

    assert audio_in(tmp_path) == was


def test_a_dry_run_finds_them_and_writes_nothing(discs, tmp_path):
    from noaap.download import PLAN_FILE
    from noaap.service import Service

    album_dir, _ = adopt_it(tmp_path)
    as_1_5_0_adopted(album_dir)
    before = (album_dir / PLAN_FILE).read_bytes()

    cfg = Config()
    cfg.library_root = tmp_path
    assert Service(cfg, tmp_path).find_again.__doc__  # it is the documented pass, not a side effect
    Service(cfg, tmp_path).repair(dry_run=True)

    assert (album_dir / PLAN_FILE).read_bytes() == before


def test_a_track_whose_file_really_is_gone_is_left_alone_and_still_reported(discs, tmp_path):
    """Slice 60's two absences: this pass finds files, it does not paper over a broken library."""
    from noaap.download import load_plan, lost_files
    from noaap.service import Service

    album_dir, plan = adopt_it(tmp_path)
    (album_dir / plan.tracks[0].filename).unlink()

    cfg = Config()
    cfg.library_root = tmp_path
    Service(cfg, tmp_path).repair(dry_run=False)

    assert len(lost_files(album_dir, load_plan(album_dir))) == 1


# -- what a provider may not be asked (R-210, do 1) ------------------------------------------------


def test_an_album_whose_files_are_not_inside_it_is_refused_not_adopted(tmp_path):
    """A disc spelled as a *sibling* folder: the collection reaches outside the album folder, so no
    name relative to it can describe the file. The collection has none of these; the code supports
    them, and adopting one would be the copy defect all over again.
    """
    from noaap.adopt import examine

    for disc in (1, 2):
        for n, title in enumerate(["One", "Two"], 1):
            encode(tmp_path / "A Band" / f"An Album CD{disc}" / f"{n:02d} - {title}.mp3",
                   title=title, artist="A Band", album="An Album", album_artist="A Band",
                   track=str(n), disc=str(disc))

    taken = examine(tmp_path / "A Band" / "An Album CD1", folder(), tmp_path)

    assert taken.plan is None
    assert "not inside this folder" in (taken.refused or "")


def test_an_adopted_track_is_never_fetched_again_whatever_the_plan_says(discs, tmp_path):
    """The guard under the fix: even with a plan that names the file wrongly, the executor does not
    copy. It is the rule that makes this whole class of defect impossible rather than fixed once.
    """
    from noaap.download import load_plan, run

    album_dir, _ = adopt_it(tmp_path)
    as_1_5_0_adopted(album_dir)
    was = audio_in(tmp_path)

    run(load_plan(album_dir), album_dir, folder(), track_source=lambda t: folder(), download=True)

    assert audio_in(tmp_path) == was, "nothing was copied, though every track's file was 'missing'"
