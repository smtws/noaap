"""Noticing that something arrived (DESIGN §9, slice 59).

**No case here waits.** The clock is an argument, so a file that is still being copied, an album
arriving one track at a time and a download client's temporary name are all driven by moving time
forward — which is also the only way to test a twenty-second window in a suite that must stay fast.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from noaap.watch import Seen, Watcher, albums, look, loose, skip

A, B = "A Band/An Album/01 - One.mp3", "A Band/An Album/02 - Two.mp3"


def album(*files: str, size: int = 100, mtime: int = 1) -> dict[str, Seen]:
    return {f: Seen(size, mtime) for f in files}


# -- what is ignored before anything is timed --------------------------------------------------


@pytest.mark.parametrize("name", [
    "album/song.mp3.part", "album/song.crdownload", "album/song.tmp", "album/~song.mp3",
    ".hidden/song.mp3", "album/.song.mp3", ".recycle/20260101-abc/audio.opus",
    "album/.parts/song.opus", "album/.originals/aaaaaaaaaaa.opus",
])
def test_what_is_not_an_arrival(name):
    """A client writing under a temporary name, noaap's own working folders, anything hidden."""
    assert skip(Path(name)) is True


def test_what_is(tmp_path):
    assert skip(Path("A Band/An Album/01 - One.mp3")) is False


# -- the settle window -------------------------------------------------------------------------


def test_a_folder_that_has_stopped_moving_is_handed_over():
    watcher = Watcher(settle=20)

    assert watcher.step(album(A), now=0) == [], "just arrived: it may still be growing"
    assert watcher.step(album(A), now=19) == [], "still inside the window"
    assert watcher.step(album(A), now=20) == ["A Band/An Album"]


def test_a_file_that_is_still_growing_never_settles():
    """A copy in progress: the size changes, so the window starts again every time."""
    watcher = Watcher(settle=20)

    for second, size in ((0, 100), (15, 200), (30, 300), (45, 400)):
        assert watcher.step(album(A, size=size), now=second) == []

    assert watcher.step(album(A, size=400), now=65) == ["A Band/An Album"]


def test_an_album_arriving_one_track_at_a_time_waits_for_the_last_one():
    """The window belongs to the **folder**, not to each file, which is what carries this."""
    watcher = Watcher(settle=20)

    watcher.step(album(A), now=0)
    assert watcher.step(album(A, B), now=15) == [], "a second track arrived: start again"
    assert watcher.step(album(A, B), now=30) == [], "still inside the window that track opened"
    assert watcher.step(album(A, B), now=35) == ["A Band/An Album"]


def test_a_temporary_name_that_is_renamed_starts_its_own_clock():
    """`look` never sees the temporary name, so the rename is simply an arrival."""
    watcher = Watcher(settle=20)

    assert watcher.step({}, now=0) == []
    assert watcher.step(album(A), now=10) == [], "the rename happened at 10, not at 0"
    assert watcher.step(album(A), now=29) == []
    assert watcher.step(album(A), now=30) == ["A Band/An Album"]


def test_a_folder_is_handed_over_once_and_not_again():
    watcher = Watcher(settle=20)
    watcher.step(album(A), now=0)

    assert watcher.step(album(A), now=20) == ["A Band/An Album"]
    assert watcher.step(album(A), now=40) == [], "nothing changed, so there is nothing to say"
    assert watcher.step(album(A, B), now=41) == [], "but a new track opens it again"
    assert watcher.step(album(A, B), now=61) == ["A Band/An Album"]


def test_a_file_that_went_away_is_a_change_like_any_other():
    """Noticed, never acted on: nothing the watcher does removes anything (R-199, decision 3)."""
    watcher = Watcher(settle=20)
    watcher.step(album(A, B), now=0)
    watcher.step(album(A, B), now=20)

    assert watcher.step(album(A), now=30) == [], "the folder changed, so the window starts again"
    assert watcher.step(album(A), now=50) == ["A Band/An Album"]


def test_a_failed_arrival_can_be_handed_over_again():
    watcher = Watcher(settle=20)
    watcher.step(album(A), now=0)
    watcher.step(album(A), now=20)

    watcher.forget("A Band/An Album")

    assert watcher.step(album(A), now=25) == ["A Band/An Album"]


# -- an album is a folder, and a loose file is not an album --------------------------------------


def test_a_loose_file_at_the_root_is_reported_and_left():
    watcher = Watcher(settle=20)
    watcher.step({"stray.mp3": Seen(1, 1)}, now=0)

    ready = watcher.step({"stray.mp3": Seen(1, 1)}, now=20)

    assert loose(ready) == ["."] and albums(ready) == []


# -- and what one look at a real folder finds ------------------------------------------------------


def test_a_look_is_a_stat_per_audio_file(tmp_path):
    (tmp_path / "A Band" / "An Album").mkdir(parents=True)
    (tmp_path / "A Band" / "An Album" / "01 - One.mp3").write_bytes(b"x" * 10)
    (tmp_path / "A Band" / "An Album" / "cover.jpg").write_bytes(b"x")
    (tmp_path / "A Band" / "An Album" / "02 - Two.mp3.part").write_bytes(b"x")
    (tmp_path / ".recycle").mkdir()
    (tmp_path / ".recycle" / "audio.mp3").write_bytes(b"x")

    found = look(tmp_path, (".mp3", ".flac", ".opus"))

    assert list(found) == ["A Band/An Album/01 - One.mp3"], "not the cover, not the part, not the bin"
    assert found["A Band/An Album/01 - One.mp3"].size == 10


# -- across a restart -------------------------------------------------------------------------------


def test_what_was_handed_over_is_not_handed_over_again_after_a_restart():
    """A restart is not a reason to re-import a library."""
    watcher = Watcher(settle=20)
    watcher.step(album(A), now=0)
    watcher.step(album(A), now=20)

    again = Watcher.restored(watcher.state(), settle=20, now=100)

    assert again.step(album(A), now=120) == []


def test_an_arrival_still_being_written_survives_a_restart_and_settles_after_it():
    """And a restart is not a reason to lose one, either."""
    watcher = Watcher(settle=20)
    watcher.step(album(A, size=100), now=0)

    again = Watcher.restored(watcher.state(), settle=20, now=100)

    assert again.step(album(A, size=100), now=110) == [], "the window starts afresh, not expired"
    assert again.step(album(A, size=100), now=120) == ["A Band/An Album"]
