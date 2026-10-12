"""A cover taken from the album's own files says so, and names the folder (DESIGN §9, slice 185; q10).

The line read `a cover was saved beside the album from /mnt/…/Sagittarius/The Kingdom Come`: an
address-shaped path where every other cover line has a web address, and nothing to say the picture
came out of the album's own files.
"""

from __future__ import annotations

from test_cover_stays_true import BLUE, GROUP, RED, RELEASE, a_repair, taken_in

from noaap import download
from noaap.download import MAYBE_COVER, _would_save, caa_group_front, load_plan, save_plan


def with_a_picture_in_its_files(album):
    """No cover beside the album, a picture inside the first track — and, as Sagittarius had, a
    stray picture under a name of its own, which is the user's and is left alone."""
    from noaap.tag import tag_file

    (album / "sagottarius.jpg").write_bytes(RED)
    plan = load_plan(album)
    first = plan.tracks[0]
    tag_file(album / first.filename, plan, first, cover=BLUE)
    plan.cover_url = str(album)
    plan.tracks[0].video_id = str(album / "an old name.opus")   # something else for the pass to do
    plan.tracks[0].sync_candidates()
    save_plan(plan, album)


def test_the_check_names_the_files_and_the_folder(tmp_path, one_second_of_sound):
    album = taken_in(tmp_path / "collection", one_second_of_sound)
    with_a_picture_in_its_files(album)

    said = a_repair(tmp_path / "collection", dry_run=True)

    want = f"a cover would be saved beside the album from the files in {album}, if one of them holds a picture"
    assert [line for line in said if line.strip() == want], said


def test_the_apply_names_the_files_and_the_folder(tmp_path, one_second_of_sound):
    album = taken_in(tmp_path / "collection", one_second_of_sound)
    with_a_picture_in_its_files(album)

    said = a_repair(tmp_path / "collection", dry_run=False)

    assert (album / "cover.jpg").read_bytes() == BLUE, said
    assert (album / "sagottarius.jpg").read_bytes() == RED, "the stray picture is left as it is"
    lines = [line.strip() for line in said if "a cover was saved beside the album" in line]
    assert lines == [f"a cover was saved beside the album from the files in {album}"], said


def test_an_address_behind_the_files_is_still_named_as_one(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "caa_answers", lambda url: None)
    album = tmp_path / "A Band" / "An Album"

    said = _would_save([str(album), caa_group_front(GROUP)], album)

    assert said == (f"a cover would be saved beside the album from the files in {album}, "
                    f"if one of them holds a picture, else from {caa_group_front(GROUP)}")


def test_a_web_address_reads_as_before(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "caa_answers", lambda url: None)
    url = f"https://coverartarchive.org/release/{RELEASE}/front-500"

    assert _would_save([url], tmp_path) == f"a cover would be saved beside the album from {url}"


def test_the_files_alone_still_promise_nothing(tmp_path):
    """The check's total counts a cover only where an address answers (slice 182)."""
    album = tmp_path / "A Band" / "An Album"

    line = _would_save([str(album)], album)

    assert MAYBE_COVER in line and ", else from " not in line
