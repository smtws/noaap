"""A file that already says what the plan wants is not rewritten (R-410, ruling 7).

455 files of the user's collection would have been read, copied beside themselves, replaced and
verified to end up holding exactly what they already held — because the *plan* had no record of
their tags, which a freshly adopted album never has. The record was what was stale; the file was
right. And thousands more differed from the plan in nothing but a leading zero, which is noaap's own
spelling of a number and not a reason to rewrite somebody's file.
"""

from __future__ import annotations

import shutil

import pytest
from mutagen import File as MFile
from test_collisions import a_service
from test_intake import QUIET

from noaap import intake
from noaap.download import PLAN_FILE, would_do
from noaap.tag import only_padding


@pytest.fixture
def already_right(tmp_path, one_second_of_sound):
    """A collection taken in once, with its plans removed: every value right, no record of it."""
    root = tmp_path / "collection"
    album = root / "Aphelion" / "Nocturnes"
    album.mkdir(parents=True)
    for n, title in enumerate(["First", "Second"], 1):
        path = album / f"{n:02d} {title}.opus"
        shutil.copy(one_second_of_sound, path)
        audio = MFile(path)
        audio["title"], audio["album"] = [title], ["Nocturnes"]
        audio["artist"] = audio["albumartist"] = ["Aphelion"]
        audio["tracknumber"] = [f"{n:02d}"]        # the owner's padding
        audio["tracktotal"] = audio["totaltracks"] = ["02"]
        audio.save()
    return root, album


def test_a_leading_zero_is_not_a_reason_to_rewrite(already_right):
    root, album = already_right
    before = sorted(p.stat().st_mtime_ns for p in album.glob("*.opus"))

    said = []
    done = intake.take_in(a_service(root), root, QUIET, dry_run=False, log=said.append)

    assert done.retagged == 0, said
    # the names follow the scheme, the bytes are untouched: no file was written to
    assert sorted(p.stat().st_mtime_ns for p in album.glob("*.opus")) == before


def test_the_dry_run_says_nothing_about_it_either(already_right):
    root, album = already_right
    done = intake.take_in(a_service(root), root, QUIET, dry_run=True, log=lambda s: None)
    assert not [line for line in done.would if "would be rewritten" in line], done.would
    assert not [line for line in done.would if "would be retagged" in line], done.would
    assert done.retagged == 0


def test_a_real_change_is_still_written(already_right):
    """The guard is about files that are right, not about files that are wrong."""
    root, album = already_right
    one = sorted(album.glob("*.opus"))[0]
    audio = MFile(one)
    del audio["album"]              # a field that is empty, which the plan fills from the others
    audio.save()

    done = intake.take_in(a_service(root), root, QUIET, dry_run=True, log=lambda s: None)
    lines = [line for line in done.would if "would be retagged" in line]
    assert len(lines) == 1 and "album nothing → “Nocturnes”" in lines[0], done.would


def test_the_record_catches_up_so_the_next_pass_asks_nothing(already_right):
    """The plan is written with the signature the file already answers to."""
    root, album = already_right
    intake.take_in(a_service(root), root, QUIET, dry_run=False, log=lambda s: None)
    from noaap.download import load_plan
    plan = load_plan(album)
    assert all(t.tagged for t in plan.tracks), "every track's tags are recorded"
    assert (album / PLAN_FILE).is_file()


def test_only_padding_is_about_numbers_and_nothing_else():
    have = {"tracknumber": "01", "title": "Dein Blut"}
    assert only_padding(have, {"tracknumber": "1"}, ["tracknumber"])
    assert not only_padding(have, {"title": "Dein blut"}, ["title"])
    assert not only_padding(have, {"tracknumber": "2"}, ["tracknumber"])
    assert not only_padding(have, {}, [])
