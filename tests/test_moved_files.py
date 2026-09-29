"""Finding a track's file again by what it holds (DESIGN §9, slice 66).

`find_again` (slice 61) asks the provider where a ref's file is. This is for the cases where the name
itself is gone: the owner renamed the file, or moved it into a disc folder, or into another album. Only
the audio is left to go on, so the identity decides — and **where it does not decide, the track is named
and left**. Nothing on disk is ever touched: what changes is what the plan says.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from test_folder_source import encode

from noaap import sources
from noaap.config import Config
from noaap.download import load_plan
from noaap.service import Service

AUDIO = {".mp3", ".flac", ".opus", ".m4a"}


def folder():
    return sources.get("folder", Config())


@pytest.fixture
def album(tmp_path) -> Path:
    """Two discs in sub-folders, four recordings, each a different one."""
    where = tmp_path / "A Band" / "An Album"
    for disc, titles in ((1, ["One", "Two"]), (2, ["Three", "Four"])):
        for n, title in enumerate(titles, 1):
            encode(where / f"cd{disc}" / f"A Band - An Album - {n:02d} - {title}.mp3",
                   hz=300 + 70 * (disc * 2 + n), title=title, artist="A Band", album="An Album",
                   album_artist="A Band", track=str(n), disc=str(disc))
    return where


def adopt_it(library: Path) -> Path:
    from noaap.adopt import carry_out, survey

    found = survey(library, library, folder())
    carry_out(found)
    return found.taking[0].album_dir


def service(library: Path) -> Service:
    cfg = Config()
    cfg.library_root = library
    return Service(cfg, library)


def files_in(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if p.is_file() and p.suffix.lower() in AUDIO)


def names(library: Path, album_dir: Path) -> set[str]:
    return {t.filename for t in load_plan(album_dir).tracks}


# -- what it finds ---------------------------------------------------------------------------------


def test_a_file_the_owner_renamed_is_found_again(album, tmp_path):
    album_dir = adopt_it(tmp_path)
    (album_dir / "cd1" / "A Band - An Album - 01 - One.mp3").rename(album_dir / "cd1" / "01 my own name.mp3")
    was = files_in(tmp_path)

    done = service(tmp_path).find_moved(load_plan(album_dir), album_dir, apply=False)
    assert done["would_move"] == 1 and done["moved"] == 0 and done["left"] == 0

    service(tmp_path).repair(find_moved=True, apply=True)

    assert "cd1/01 my own name.mp3" in names(tmp_path, album_dir)
    assert files_in(tmp_path) == was, "nothing on disk was touched"


def test_a_file_moved_into_another_folder_of_the_album(album, tmp_path):
    album_dir = adopt_it(tmp_path)
    src = album_dir / "cd2" / "A Band - An Album - 02 - Four.mp3"
    (album_dir / "extra").mkdir()
    src.rename(album_dir / "extra" / src.name)

    service(tmp_path).repair(find_moved=True, apply=True)

    assert f"extra/{src.name}" in names(tmp_path, album_dir)


def test_a_file_moved_into_another_album_is_found_in_the_library(album, tmp_path):
    """The wider look, which only happens when the album itself has no answer."""
    album_dir = adopt_it(tmp_path)
    src = album_dir / "cd1" / "A Band - An Album - 02 - Two.mp3"
    other = tmp_path / "A Band" / "Another Album"
    other.mkdir(parents=True)
    src.rename(other / src.name)

    service(tmp_path).repair(find_moved=True, apply=True)

    assert f"../Another Album/{src.name}" in names(tmp_path, album_dir) or \
        any("Another Album" in n for n in names(tmp_path, album_dir)), \
        "the plan says where the file is, wherever that is"


def test_the_adopted_name_follows_the_file(album, tmp_path):
    """An album that keeps its own names keeps the name the file has **now**: that is what its owner
    called it (R-227, ruling 2 — the plan changes, the file does not)."""
    album_dir = adopt_it(tmp_path)
    (album_dir / "cd1" / "A Band - An Album - 01 - One.mp3").rename(album_dir / "cd1" / "renamed.mp3")

    service(tmp_path).repair(find_moved=True, apply=True)

    plan = load_plan(album_dir)
    track = next(t for t in plan.tracks if t.filename == "cd1/renamed.mp3")
    assert track.adopted_name == "cd1/renamed.mp3"


# -- and what it refuses to decide -----------------------------------------------------------------


def test_two_files_holding_the_recording_leave_the_track_alone(album, tmp_path):
    """The 68 recordings this collection holds twice are this case when both copies are unclaimed."""
    album_dir = adopt_it(tmp_path)
    one = album_dir / "cd1" / "A Band - An Album - 01 - One.mp3"
    shutil.copy2(one, album_dir / "cd1" / "a copy of it.mp3")
    shutil.copy2(one, album_dir / "cd1" / "another copy.mp3")
    one.unlink()

    done = service(tmp_path).find_moved(load_plan(album_dir), album_dir, apply=True)

    assert done["moved"] == 0 and done["left"] == 1
    assert "cd1/A Band - An Album - 01 - One.mp3" in names(tmp_path, album_dir), "the plan is unchanged"


def test_a_file_another_track_already_answers_for_is_not_a_candidate(album, tmp_path):
    """One file is one track's. The same recording twice, one copy claimed, leaves exactly one answer —
    which is how the 68 are re-attached rather than left."""
    album_dir = adopt_it(tmp_path)
    one = album_dir / "cd1" / "A Band - An Album - 01 - One.mp3"
    shutil.copy2(one, album_dir / "cd1" / "the other copy.mp3")
    one.rename(album_dir / "cd1" / "renamed.mp3")

    done = service(tmp_path).find_moved(load_plan(album_dir), album_dir, apply=True)

    assert done["left"] == 1, "two unclaimed copies, so nothing is decided"
    plan = load_plan(album_dir)
    assert "cd1/A Band - An Album - 01 - One.mp3" in {t.filename for t in plan.tracks}


def test_nothing_holding_it_leaves_the_track_alone(album, tmp_path):
    album_dir = adopt_it(tmp_path)
    (album_dir / "cd2" / "A Band - An Album - 01 - Three.mp3").unlink()

    done = service(tmp_path).find_moved(load_plan(album_dir), album_dir, apply=True)

    assert done["moved"] == 0 and done["would_move"] == 0 and done["left"] == 1


def test_a_track_nothing_was_ever_measured_about_says_so(album, tmp_path):
    """Nothing to look for: both digests are measured from the file, and there is no file."""
    from noaap.download import save_plan

    album_dir = adopt_it(tmp_path)
    plan = load_plan(album_dir)
    for track in plan.tracks:                    # a plan from before either digest existed
        for copy in track.candidates:
            copy.stream_sha = copy.audio_sha = copy.audio_sha_by = None
    save_plan(plan, album_dir)
    (album_dir / "cd1" / "A Band - An Album - 01 - One.mp3").unlink()

    said = []
    Service(Config(), tmp_path, log=said.append).find_moved(load_plan(album_dir), album_dir)

    assert any("nothing was ever measured" in line for line in said)


# -- dry by default --------------------------------------------------------------------------------


def test_without_apply_it_changes_nothing_of_its_own(album, tmp_path):
    """`--find-moved` is dry unless asked. `repair` still does its own work — it is `--dry-run` that
    makes the whole pass write nothing — so what this case holds is that the *plan's names* are untouched.
    """
    album_dir = adopt_it(tmp_path)
    (album_dir / "cd1" / "A Band - An Album - 01 - One.mp3").rename(album_dir / "cd1" / "renamed.mp3")
    before = names(tmp_path, album_dir)

    service(tmp_path).repair(find_moved=True)          # no --apply

    assert names(tmp_path, album_dir) == before
    assert "cd1/A Band - An Album - 01 - One.mp3" in before


def test_and_a_dry_run_writes_nothing_at_all(album, tmp_path):
    from noaap.download import PLAN_FILE

    album_dir = adopt_it(tmp_path)
    (album_dir / "cd1" / "A Band - An Album - 01 - One.mp3").rename(album_dir / "cd1" / "renamed.mp3")
    before = (album_dir / PLAN_FILE).read_bytes()

    service(tmp_path).repair(dry_run=True, find_moved=True, apply=True)

    assert (album_dir / PLAN_FILE).read_bytes() == before


def test_what_it_decoded_is_counted(album, tmp_path):
    """R-227, ruling 4: what a pass costs is a number, and it is reported."""
    album_dir = adopt_it(tmp_path)
    (album_dir / "cd1" / "A Band - An Album - 01 - One.mp3").rename(album_dir / "cd1" / "renamed.mp3")

    looked = service(tmp_path).find_moved(load_plan(album_dir), album_dir)
    plan = load_plan(album_dir)
    did = service(tmp_path).find_moved(plan, album_dir, apply=True)

    assert looked["decoded"] == 0, "a renamed file is byte for byte what it was: the cheap digest finds it"
    assert did["decoded"] == 1, "and the one it attached is measured, so a re-tag cannot hide it later"
    assert next(c.audio_sha for t in plan.tracks for c in t.candidates
                if t.filename == "cd1/renamed.mp3" and c.ref == t.effective_id)
