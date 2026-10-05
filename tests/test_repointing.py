"""A plan says where its album is (DESIGN §9, slice 125; R-470).

A staged take-in runs the ordinary pass against the **staging copy**, so the folder provider recorded
that copy's path: 154 plans on the user's share name `~/noaap-nas-run/staging/batch/<album>` as their
`source_id`, `source_url` and `cover_url`, and that folder was deleted when the run ended. Nothing
was lost — the cover is beside the album — but a cover address that cannot be read keeps its album
out of every skip for ever (slice 124), and 49 albums of the collection were in that state.
"""

from __future__ import annotations

import shutil

import pytest
from test_intake import QUIET

from noaap import intake, staged
from noaap.config import Config
from noaap.download import load_plan, point_at, points_elsewhere, save_plan
from noaap.service import Service


def an_album(folder, titles, tone, artist="A Band", album="An Album"):
    from mutagen import File as MFile

    folder.mkdir(parents=True, exist_ok=True)
    for n, title in enumerate(titles, 1):
        path = folder / f"{n:02d} {title}.opus"
        shutil.copy(tone, path)
        audio = MFile(path)
        audio["title"], audio["artist"] = [title], [artist]
        audio["albumartist"], audio["album"] = [artist], [album]
        audio["tracknumber"] = [str(n)]
        audio.save()
    return folder


def a_library(root, **settings):
    cfg = Config(library_root=root, musicbrainz=False, lyrics=False, **settings)
    return Service(cfg, root, log=lambda s: None)


# -- what the question is ------------------------------------------------------------------------


def test_a_plan_naming_another_folder_points_elsewhere(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    album = an_album(root / "A Band" / "An Album", ["One"], one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    plan = load_plan(album)
    assert points_elsewhere(plan, album) is False, "a plain take-in records the album itself"

    plan.source_id = plan.source_url = "/gone/staging/batch/A Band/An Album"
    plan.cover_url = "/gone/staging/batch/A Band/An Album/cover.jpg"

    assert points_elsewhere(plan, album) is True
    assert point_at(plan, album) is True
    assert plan.source_id == plan.source_url == str(album)
    assert plan.cover_url == str(album), "no cover beside it, so the folder is the address"


def test_a_cover_beside_the_album_becomes_the_address(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    album = an_album(root / "A Band" / "An Album", ["One"], one_second_of_sound)
    (album / "cover.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    plan = load_plan(album)
    plan.cover_url = "/gone/staging/batch/A Band/An Album/cover.jpg"
    plan.source_id = plan.source_url = "/gone/staging/batch/A Band/An Album"

    point_at(plan, album)

    assert plan.cover_url == str(album / "cover.jpg")


def test_a_fetched_albums_url_is_never_touched(tmp_path, one_second_of_sound):
    """`source_url` is a URL for everything but a folder, and `provider` is what says which."""
    root = tmp_path / "collection"
    album = an_album(root / "A Band" / "An Album", ["One"], one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    plan = load_plan(album)
    plan.provider = "youtube"
    plan.source_url = "https://www.youtube.com/playlist?list=OLAK5uy_abc"
    plan.source_id = "OLAK5uy_abc"
    plan.cover_url = "https://i.ytimg.com/vi/abc/maxresdefault.jpg"

    assert points_elsewhere(plan, album) is False
    assert point_at(plan, album) is False


# -- the staged pass writes it right -------------------------------------------------------------


def test_a_staged_take_in_records_the_shares_own_folder(tmp_path, one_second_of_sound):
    """The defect, at its source — and it needs the **rename** to reproduce.

    The provider records the folder it read, which is the staging copy's; `intake` then relocates the
    album into the scheme and saves the plan in the new folder, and nothing updates the address it
    recorded before the move. Where the folder's name already matched the scheme there was no move
    and the path happened to come out right, which is why a first version of this case passed with
    the fix removed. The 154 real plans are all albums the scheme renamed — `asp` → `ASP` and kin.
    """
    share = tmp_path / "share"
    an_album(share / "a band" / "an album", ["One", "Two"], one_second_of_sound)
    staging = tmp_path / "staging"

    staged.take_in_staged(a_library(share), share, QUIET, staging=staging,
                          batch_size=10_000_000, dry_run=False, log=lambda s: None)

    album = share / "A Band" / "An Album"
    assert album.is_dir(), "the scheme renamed it, which is what makes this case the real one"
    plan = load_plan(album)
    assert plan is not None
    assert str(staging) not in plan.source_id, plan.source_id
    assert str(staging) not in plan.source_url, plan.source_url
    assert str(staging) not in str(plan.cover_url), plan.cover_url
    assert plan.source_id == str(album)
    assert points_elsewhere(plan, album) is False


# -- and a repair fixes what is already there ----------------------------------------------------


def test_repair_re_points_a_plan_and_says_how_many(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    album = an_album(root / "A Band" / "An Album", ["One"], one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    plan = load_plan(album)
    plan.source_id = plan.source_url = "/gone/staging/batch/A Band/An Album"
    plan.cover_url = "/gone/staging/batch/A Band/An Album/cover.jpg"
    save_plan(plan, album)

    said = []
    service = a_library(root, retag_adopted=True)
    service.log = said.append
    service.repair(dry_run=True)

    assert [line for line in said if "plan(s) would be re-pointed" in line], said
    assert load_plan(album).source_id.startswith("/gone"), "a check writes nothing"

    service.repair()

    assert load_plan(album).source_id == str(album)
    assert [line for line in said if "re-pointed" in line]


def test_an_album_whose_plan_is_right_is_still_skipped(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    an_album(root / "A Band" / "An Album", ["One"], one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)

    said = []
    service = a_library(root, retag_adopted=True)
    service.log = said.append
    service.repair(dry_run=True)

    assert not [line for line in said if "re-pointed" in line], said
    assert not [line for line in said if line.startswith("=== ")], said


def test_an_album_taken_in_from_a_folder_elsewhere_keeps_pointing_at_it(tmp_path,
                                                                        one_second_of_sound):
    """The case the first version of this broke: a folder-sourced plan whose source really is
    somewhere else. `repair --find-moved` follows that folder when it moves and writes the new path
    into the plan — and re-pointing on "not the album's own folder" overwrote what it had found.

    The question is narrower: a path that is **gone**, or one a staged pass's index claims.
    """
    root = tmp_path / "collection"
    album = an_album(root / "A Band" / "An Album", ["One"], one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    elsewhere = tmp_path / "intake" / "A Band" / "An Album"
    elsewhere.mkdir(parents=True)
    plan = load_plan(album)
    plan.source_id = plan.source_url = str(elsewhere)

    assert points_elsewhere(plan, album) is False, "it is somewhere else and it is there"
    assert point_at(plan, album) is False
    assert plan.source_id == str(elsewhere)


def test_a_staging_folder_that_is_still_there_is_recognised_by_its_index(tmp_path,
                                                                        one_second_of_sound):
    """The other half: a staged pass's copy may still be on the disk. Its index is what says so —
    no name is assumed, and a folder of the owner's called `batch` is not a staging folder."""
    from noaap.download import STAGED_INDEX

    root = tmp_path / "collection"
    album = an_album(root / "A Band" / "An Album", ["One"], one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    staging = tmp_path / "staging"
    copy = staging / "batch" / "A Band" / "An Album"
    copy.mkdir(parents=True)
    plan = load_plan(album)
    plan.source_id = plan.source_url = str(copy)

    assert points_elsewhere(plan, album) is False, "no index yet: just a folder somewhere"

    (staging / STAGED_INDEX).write_text("{}")

    assert points_elsewhere(plan, album) is True
    assert point_at(plan, album) is True
    assert plan.source_id == str(album)


# -- a track's own id, and a move that settles in one pass (§9, slice 130; R-484 item 1) ---------


def test_a_tracks_id_naming_a_deleted_staging_file_is_re_pointed(tmp_path, one_second_of_sound):
    """The state 33 of the user's tracks were in: the file is beside the plan, the id names the
    staging copy the staged pass read it from, and that folder is gone."""
    from noaap.download import tracks_elsewhere

    root = tmp_path / "collection"
    album = an_album(root / "A Band" / "An Album", ["One", "Two"], one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    plan = load_plan(album)
    assert tracks_elsewhere(plan, album) == [], "a plain take-in records the file itself"
    for track in plan.tracks:
        track.video_id = f"/gone/staging/batch/A Band/An Album/{track.filename}"
        track.sync_candidates()

    assert len(tracks_elsewhere(plan, album)) == 2
    assert points_elsewhere(plan, album) is True
    assert point_at(plan, album) is True

    for track in plan.tracks:
        assert track.video_id == str(album / track.filename)
        assert [c.ref for c in track.candidates] == [track.video_id], "no stale candidate left over"
        assert track.chosen == track.video_id


def test_a_track_whose_file_really_is_elsewhere_keeps_pointing_at_it(tmp_path, one_second_of_sound):
    """The narrowing, per track: gone or under a staged index, never "not the album's own folder"."""
    from noaap.download import tracks_elsewhere

    root = tmp_path / "collection"
    album = an_album(root / "A Band" / "An Album", ["One"], one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    elsewhere = tmp_path / "intake" / "A Band" / "An Album"
    elsewhere.mkdir(parents=True)
    source = elsewhere / "01 One.opus"
    shutil.copy(one_second_of_sound, source)
    plan = load_plan(album)
    plan.source_id = plan.source_url = str(elsewhere)
    plan.tracks[0].video_id = str(source)
    plan.tracks[0].sync_candidates()

    assert tracks_elsewhere(plan, album) == []
    assert point_at(plan, album) is False
    assert plan.tracks[0].video_id == str(source)


def test_the_source_and_the_tracks_are_gated_apart(tmp_path, one_second_of_sound):
    """A source that is right and a track that is not: only the track moves."""
    root = tmp_path / "collection"
    album = an_album(root / "A Band" / "An Album", ["One"], one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    elsewhere = tmp_path / "intake" / "A Band" / "An Album"
    elsewhere.mkdir(parents=True)
    plan = load_plan(album)
    plan.source_id = plan.source_url = str(elsewhere)   # really there, so never touched
    plan.tracks[0].video_id = "/gone/staging/batch/A Band/An Album/01 One.opus"
    plan.tracks[0].sync_candidates()

    assert point_at(plan, album) is True
    assert plan.source_id == str(elsewhere), "the album's own address was right and stays"
    assert plan.tracks[0].video_id == str(album / plan.tracks[0].filename)


def test_what_the_source_last_looked_like_moves_with_the_ids(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    album = an_album(root / "A Band" / "An Album", ["One"], one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    plan = load_plan(album)
    stale = f"/gone/staging/batch/A Band/An Album/{plan.tracks[0].filename}"
    plan.tracks[0].video_id = stale
    plan.tracks[0].sync_candidates()
    plan.source_state = {"ids": [stale], "modified": "20261004"}

    point_at(plan, album)

    assert plan.source_state["ids"] == [str(album / plan.tracks[0].filename)]


def test_a_move_and_its_re_point_are_one_pass(tmp_path, one_second_of_sound):
    """The three-applies bug. `repair` asks where the album is **before** the skip, which is before
    the folder moves and before the files are renamed — so the plan was left naming the folder it had
    just been moved out of, and only the next apply fixed it."""
    root = tmp_path / "collection"
    album = an_album(root / "a band" / "an album", ["One"], one_second_of_sound,
                     artist="A Band", album="An Album")
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    assert (root / "A Band" / "An Album").is_dir(), "the scheme renamed it on the way in"

    # put it back under the owner's spelling, with every address naming that folder: the state an
    # album is in when a repair is about to move it
    shutil.move(root / "A Band" / "An Album", album)
    shutil.rmtree(root / "A Band")
    plan = load_plan(album)
    plan.folder = "a band/an album"
    plan.source_id = plan.source_url = str(album)
    plan.cover_url = str(album)
    for track in plan.tracks:
        track.video_id = str(album / track.filename)
        track.sync_candidates()
    save_plan(plan, album)

    service = a_library(root, retag_adopted=True, rename_adopted=True)
    service.log = lambda s: None
    service.repair()

    moved = root / "A Band" / "An Album"
    assert moved.is_dir(), "the repair moved it into the scheme"
    after = load_plan(moved)
    assert after.source_id == str(moved), after.source_id
    assert points_elsewhere(after, moved) is False, "one pass, not three"
    for track in after.tracks:
        assert track.video_id == str(moved / track.filename), track.video_id
