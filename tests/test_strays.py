"""Removing what 1.4.0 and 1.5.0 wrongly wrote (DESIGN §9, slice 63).

Those versions copied a disc-folder track into its album's root, under the name the plan held. The
files are still there, in somebody's music folder, and noaap put them there — so this takes them out,
and **only when it can prove each one is a copy**: the album is adopted and keeps its discs in
sub-folders, the name is one the plan points at or one noaap's scheme would write, the **decoded**
audio is a disc file's, and that disc file is one the plan holds. Anything less is reported and left.

Nothing is deleted: a stray goes to the bin with the file it was a copy of and the evidence named.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_folder_source import encode

from noaap import sources
from noaap.config import Config
from noaap.download import PLAN_FILE, load_plan, save_plan
from noaap.service import Service

AUDIO = {".mp3", ".flac", ".opus", ".m4a"}


def folder():
    return sources.get("folder", Config())


@pytest.fixture
def discs(tmp_path) -> Path:
    """Two discs in sub-folders, nothing in the album's root — the shape 1.5.0 broke."""
    album = tmp_path / "In Extremo" / "An Album"
    for disc, titles in ((1, ["One", "Two"]), (2, ["Three", "Four"])):
        for n, title in enumerate(titles, 1):
            # a different recording per track: with one sine for all of them every file decodes alike,
            # and "which file is this a copy of" has no answer
            encode(album / f"cd{disc}" / f"In Extremo - An Album - {n:02d} - {title}.mp3",
                   hz=300 + 50 * (disc * 2 + n), title=title, artist="In Extremo", album="An Album",
                   album_artist="In Extremo", track=str(n), disc=str(disc))
    return album


def adopt_it(library: Path) -> tuple[Path, object]:
    from noaap.adopt import carry_out, survey

    found = survey(library, library, folder())
    carry_out(found)
    return found.taking[0].album_dir, found.taking[0].plan


def as_1_5_0_damaged(album_dir: Path, appended: bool = False) -> list[str]:
    """What 1.5.0 left behind: the plan names each file by its bare name, and the file was copied into
    the album's root under that name. With `appended`, a second pass has also read the copies as new
    tracks, which is what a user who ran `update` twice has."""
    import shutil

    plan = load_plan(album_dir)
    made = []
    for track in plan.tracks:
        here = album_dir / track.filename
        bare = Path(track.filename).name
        shutil.copy2(here, album_dir / bare)          # the copy 1.5.0 made
        track.filename = track.adopted_name = bare    # and the plan as 1.5.0 wrote it
        made.append(bare)
    if appended:
        from dataclasses import replace
        for bare in list(made):
            fresh = replace(plan.tracks[0], video_id=str(album_dir / bare), filename=bare,
                            title=f"{bare} again", adopted_name=None, adopted_tags=None,
                            candidates=[], chosen=None)
            plan.tracks.append(fresh)
    save_plan(plan, album_dir)
    return made


def audio_in(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if p.is_file() and p.suffix.lower() in AUDIO and ".recycle" not in p.parts)


def service(library: Path) -> Service:
    cfg = Config()
    cfg.library_root = library
    return Service(cfg, library)


# -- what it finds -----------------------------------------------------------------------------------


def test_a_copy_in_the_album_root_is_found_by_its_audio(discs, tmp_path):
    from noaap.strays import look

    album_dir, _ = adopt_it(tmp_path)
    as_1_5_0_damaged(album_dir)

    found, left = look(album_dir, load_plan(album_dir), lambda f: folder().collection(str(f)))

    assert [s.where for s in found] == sorted(s.where for s in found)
    assert len(found) == 4 and left == []
    assert {s.copy_of for s in found} == {f"cd{d}/In Extremo - An Album - {n:02d} - {t}.mp3"
                                          for d, n, t in ((1, 1, "One"), (1, 2, "Two"),
                                                          (2, 1, "Three"), (2, 2, "Four"))}
    assert all(s.digest and s.length for s in found), "the evidence is measured, not assumed"


def test_a_file_of_the_owners_own_is_left_alone(discs, tmp_path):
    """Four ways short of proof, and each one keeps the file where it is."""
    from noaap.strays import look

    album_dir, _ = adopt_it(tmp_path)
    as_1_5_0_damaged(album_dir)
    # (1) a name nothing in this album accounts for
    encode(album_dir / "my own mix.mp3", hz=1000, title="Mine", artist="In Extremo", album="An Album")
    # (2) a name noaap's own scheme would write — the other disc form of track 1 — but audio of its own
    other_form = "In Extremo - An Album - 1-01 - One.mp3"
    encode(album_dir / other_form, hz=1100, title="One", artist="In Extremo", album="An Album",
           album_artist="In Extremo", track="1", disc="1")

    found, left = look(album_dir, load_plan(album_dir), lambda f: folder().collection(str(f)))

    assert len(found) == 4
    why = {one.where: one.why for one in left}
    assert "my own mix.mp3" in why and "neither one noaap would have written" in why["my own mix.mp3"]
    assert why[other_form] == "no file in a disc folder has this audio", \
        "its name is one noaap would write, so only the audio can answer"


def test_a_disc_file_the_plan_does_not_hold_proves_nothing(discs, tmp_path):
    """The fourth condition: the copy's twin must be a file this album's plan actually holds."""
    from noaap.strays import look

    album_dir, _ = adopt_it(tmp_path)
    as_1_5_0_damaged(album_dir)
    plan = load_plan(album_dir)
    for track in plan.tracks:
        if track.disc == 2:  # it still names the file, but no longer holds the disc file itself
            track.video_id, track.candidates, track.chosen = "aaaaaaaaaaa", [], None
    save_plan(plan, album_dir)

    found, left = look(album_dir, load_plan(album_dir), lambda f: folder().collection(str(f)))

    assert len(found) == 2, "disc 1 is still provable"
    assert [one.why for one in left] == ["its copy “cd2/In Extremo - An Album - 01 - Three.mp3” is "
                                         "not a file this plan holds",
                                         "its copy “cd2/In Extremo - An Album - 02 - Four.mp3” is "
                                         "not a file this plan holds"]


def test_an_album_without_disc_folders_is_not_looked_at(tmp_path):
    """The first condition. A flat adopted album has no root/sub-folder distinction to reason from."""
    from noaap.strays import look

    album = tmp_path / "A Band" / "Flat"
    for n, title in enumerate(["One", "Two"], 1):
        encode(album / f"A Band - Flat - {n:02d} - {title}.mp3", hz=400 + 60 * n, title=title, artist="A Band",
               album="Flat", album_artist="A Band", track=str(n))
    album_dir, _ = adopt_it(tmp_path)

    assert look(album_dir, load_plan(album_dir), lambda f: folder().collection(str(f))) == ([], [])


# -- and what it does about it ----------------------------------------------------------------------


def test_dry_by_default(discs, tmp_path, capsys):
    album_dir, _ = adopt_it(tmp_path)
    as_1_5_0_damaged(album_dir)
    was = audio_in(tmp_path)

    service(tmp_path).repair(strays=True)

    assert audio_in(tmp_path) == was
    assert not (tmp_path / ".recycle").exists()


def test_apply_moves_them_to_the_bin_and_nothing_is_deleted(discs, tmp_path):
    from noaap.recycle import entries

    album_dir, _ = adopt_it(tmp_path)
    made = as_1_5_0_damaged(album_dir)

    service(tmp_path).repair(strays=True, apply=True)

    assert audio_in(tmp_path) == sorted(f"In Extremo/An Album/cd{d}/In Extremo - An Album - {n:02d} - {t}.mp3"
                                        for d, n, t in ((1, 1, "One"), (1, 2, "Two"),
                                                        (2, 1, "Three"), (2, 2, "Four"))), \
        "the owner's files, and only those"
    binned = entries(tmp_path)
    assert len(binned) == len(made) == 4
    data = json.loads((tmp_path / ".recycle" / binned[0].id / "bin.json").read_text())
    assert data["evidence"]["copy_of"].startswith("cd")
    assert data["evidence"]["decoded_sha256"] and data["evidence"]["length"]


def test_the_plan_points_at_the_owners_files_again(discs, tmp_path):
    from noaap.download import lost_files

    album_dir, _ = adopt_it(tmp_path)
    as_1_5_0_damaged(album_dir)

    service(tmp_path).repair(strays=True, apply=True)

    plan = load_plan(album_dir)
    assert len(plan.tracks) == 4
    assert all("/" in t.filename for t in plan.tracks)
    assert lost_files(album_dir, plan) == []
    assert {t.filename for t in plan.tracks} == {t.adopted_name for t in plan.tracks}


def test_a_track_that_existed_only_because_of_a_copy_goes_with_it(discs, tmp_path):
    """A user who ran `update` twice also has the copies as tracks of the album."""
    album_dir, _ = adopt_it(tmp_path)
    as_1_5_0_damaged(album_dir, appended=True)
    assert len(load_plan(album_dir).tracks) == 8

    service(tmp_path).repair(strays=True, apply=True)

    plan = load_plan(album_dir)
    assert len(plan.tracks) == 4, "the four that were only copies are gone"
    assert all(t.adopted_name for t in plan.tracks), "and the owner's four are all that is left"


def test_afterwards_the_album_can_be_given_back(discs, tmp_path):
    """Which it could not while it was damaged: an appended track has no record of a name, and the
    undo refuses the whole album rather than half-restore it."""
    from noaap.adopt import give_back

    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())
    album_dir, _ = adopt_it(tmp_path)
    as_1_5_0_damaged(album_dir, appended=True)
    assert give_back(album_dir, load_plan(album_dir), tmp_path)["failed"] == 4

    service(tmp_path).repair(strays=True, apply=True)
    done = give_back(album_dir, load_plan(album_dir), tmp_path)

    assert done["failed"] == 0
    left = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")
                  if p.is_file() and ".recycle" not in p.parts)
    assert left == before, "the collection is as it was, and the bin holds the copies"
