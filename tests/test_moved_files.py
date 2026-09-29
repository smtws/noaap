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


def test_a_file_moved_into_another_album_is_named_and_not_taken_back(album, tmp_path):
    """The wider look tells somebody where their file went, and changes nothing.

    `filename` is a path **relative to the album folder**; a plan naming a file in another album would
    point outside itself, which slices 60 and 61 exist to prevent. Found live: re-attaching across albums
    left a plan naming a file it could then not find at all.
    """
    album_dir = adopt_it(tmp_path)
    src = album_dir / "cd1" / "A Band - An Album - 02 - Two.mp3"
    other = tmp_path / "A Band" / "Another Album"
    other.mkdir(parents=True)
    src.rename(other / src.name)
    before = names(tmp_path, album_dir)

    said = []
    done = Service(Config(), tmp_path, log=said.append).find_moved(load_plan(album_dir), album_dir,
                                                                   apply=True)

    assert done["moved"] == 0 and done["left"] == 1
    assert names(tmp_path, album_dir) == before, "the plan is unchanged"
    assert any("its audio is now in another album" in line and "Another Album" in line
               for line in said), said


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


def plan_hashes(library: Path) -> dict[str, str]:
    import hashlib

    return {str(p.relative_to(library)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(library.glob("*/*/.ytalbum.json"))}


@pytest.mark.parametrize("asked", [{"find_moved": True}, {"strays": True},
                                   {"find_moved": True, "strays": True}])
def test_a_dry_finding_is_a_dry_pass(album, tmp_path, asked):
    """R-234, and it was wrong before: only the *finding* held back, while the rest of `repair` ran and
    saved every plan it tidied. Somebody who asks what would happen got a library that had been written
    to — measured on a real one as a hash over all 132 plans changing. So the hash of every plan is what
    this case holds, for each dry form.
    """
    album_dir = adopt_it(tmp_path)
    (album_dir / "cd1" / "A Band - An Album - 01 - One.mp3").rename(album_dir / "cd1" / "renamed.mp3")
    before = plan_hashes(tmp_path)
    assert before, "there is a plan to hash"

    service(tmp_path).repair(**asked)                  # no --apply

    assert plan_hashes(tmp_path) == before
    assert "cd1/A Band - An Album - 01 - One.mp3" in names(tmp_path, album_dir)


def test_and_with_apply_the_whole_pass_acts(album, tmp_path):
    album_dir = adopt_it(tmp_path)
    (album_dir / "cd1" / "A Band - An Album - 01 - One.mp3").rename(album_dir / "cd1" / "renamed.mp3")
    before = plan_hashes(tmp_path)

    service(tmp_path).repair(find_moved=True, apply=True)

    assert plan_hashes(tmp_path) != before
    assert "cd1/renamed.mp3" in names(tmp_path, album_dir)


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


# -- the folder an album was taken in from (§9, slice 66) -------------------------------------------


@pytest.fixture
def taken_in(tmp_path) -> tuple[Path, Path, Path]:
    """An album in an intake folder, fetched into a library: its files here, its refs over there."""
    intake = tmp_path / "intake" / "A Band" / "An Album"
    for n, title in enumerate(["One", "Two"], 1):
        encode(intake / f"{n:02d} - {title}.mp3", hz=440 + 90 * n, title=title, artist="A Band",
               album="An Album", album_artist="A Band", track=str(n))
    library = tmp_path / "library"
    library.mkdir()
    out = service(library).fetch(str(intake))
    assert out.status == "ok", out.message
    return intake, library, library / out.plan.folder


def test_an_album_keeps_its_own_files_and_points_at_the_intake(taken_in):
    from noaap.moved import source_of

    intake, library, album_dir = taken_in
    plan = load_plan(album_dir)

    assert source_of(plan) == str(intake)
    assert all((album_dir / t.filename).is_file() for t in plan.tracks), "its own files are here"
    assert all(str(intake) in t.video_id for t in plan.tracks), "and its refs are over there"


def test_a_source_folder_that_moved_is_found_again(taken_in):
    intake, library, album_dir = taken_in
    somewhere = intake.parent.parent.parent / "somewhere else"
    intake.parent.parent.rename(somewhere)          # the whole intake tree moved
    assert not intake.is_dir()

    service(library).repair(find_moved=True, under=somewhere, apply=True)   # the real path, which saves

    plan = load_plan(album_dir)
    assert str(somewhere) in plan.source_id
    assert all(str(somewhere) in t.video_id for t in plan.tracks), "the provider minted the new refs"
    assert all((album_dir / t.filename).is_file() for t in plan.tracks), "and nothing here moved"


def test_the_measurements_of_a_track_follow_its_new_ref(taken_in):
    """The ref changes because the file's *address* changed; what was measured about that file did not."""
    intake, library, album_dir = taken_in
    plan = load_plan(album_dir)
    service(library).find_moved(plan, album_dir)     # nothing lost, but it measures nothing either
    before = {t.title: next(c.stream_sha for c in t.candidates if c.ref == t.effective_id)
              for t in plan.tracks}
    somewhere = intake.parent.parent.parent / "elsewhere"
    intake.parent.parent.rename(somewhere)

    service(library).repair(find_moved=True, under=somewhere, apply=True)

    after = load_plan(album_dir)
    for track in after.tracks:
        mine = next(c for c in track.candidates if c.ref == track.effective_id)
        assert mine.ref == track.video_id
        assert before[track.title] == mine.stream_sha, "the same file, under its new address"


def test_half_an_album_is_not_a_source(taken_in):
    """A provider names a collection by a folder, so all of it must be there or none of it is."""
    intake, library, album_dir = taken_in
    somewhere = intake.parent.parent.parent / "partial"
    intake.parent.parent.rename(somewhere)
    next((somewhere / "A Band" / "An Album").glob("*.mp3")).unlink()

    said = []
    done = Service(Config(), library, log=said.append).find_source(
        load_plan(album_dir), album_dir, somewhere, apply=True)

    assert done["sources"] == 0 and done["left"] == 1
    assert any("holds" in line for line in said), said
    assert str(intake) in load_plan(album_dir).source_id, "the plan is untouched"


def test_an_album_whose_source_is_there_is_not_looked_at(taken_in):
    intake, library, album_dir = taken_in

    assert service(library).find_source(load_plan(album_dir), album_dir, intake.parent) == {}


def test_an_adopted_album_is_its_own_source_and_is_never_re_sourced(album, tmp_path):
    """Its source *is* the folder it sits in, so there is nothing to look for."""
    album_dir = adopt_it(tmp_path)

    assert service(tmp_path).find_source(load_plan(album_dir), album_dir, tmp_path) == {}


def test_an_intake_tree_that_holds_the_recording_twice_is_refused(taken_in):
    """Measured live: the collection holds `Cannibal Corpses` both as its own album and as a track of
    another, so two files under the new root held the same recording and the source was left alone."""
    intake, library, album_dir = taken_in
    somewhere = intake.parent.parent.parent / "twice"
    intake.parent.parent.rename(somewhere)
    doubled = somewhere / "A Band" / "The Same Songs Again"
    doubled.mkdir()
    copies = sorted((somewhere / "A Band" / "An Album").glob("*.mp3"))
    assert copies, "the fixture's own album, or this case tests nothing"
    for one in copies:
        shutil.copy2(one, doubled / one.name)

    said = []
    done = Service(Config(), library, log=said.append).find_source(
        load_plan(album_dir), album_dir, somewhere, apply=True)

    assert done["sources"] == 0 and done["left"] == 1
    assert any("2 files under twice hold" in line for line in said), said
