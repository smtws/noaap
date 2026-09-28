"""Noticing that something arrived (DESIGN §9, slice 59).

**No case here waits.** The clock is an argument, so a file that is still being copied, an album
arriving one track at a time and a download client's temporary name are all driven by moving time
forward — which is also the only way to test a twenty-second window in a suite that must stay fast.
"""
from __future__ import annotations

import json
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


# -- what may be watched, and what may not ------------------------------------------------------------


def test_the_two_shapes_may_not_be_nested(tmp_path):
    """R-200, ruling 1. An intake folder inside a library would take every drop twice — once as an
    arrival and once as the library's own album — and a library inside an intake folder would have
    the intake pass copy the library into itself."""
    from noaap.config import Watch, watch_trouble

    library = tmp_path / "library"

    assert watch_trouble([Watch("drop", tmp_path / "drop")], library) == []
    assert "may not hold" in watch_trouble([Watch("drop", tmp_path)], library)[0]
    assert "may not hold" in watch_trouble([Watch("drop", library / "inside")], library)[0]


def test_a_library_watching_itself_is_the_other_shape(tmp_path):
    """And needs no intake at all."""
    from noaap.config import Watch, watch_trouble

    library = tmp_path / "library"

    assert watch_trouble([Watch("mine", library, "library")], library) == []


def test_two_watched_folders_may_not_be_nested_either(tmp_path):
    from noaap.config import Watch, watch_trouble

    trouble = watch_trouble([Watch("a", tmp_path / "a"), Watch("b", tmp_path / "a" / "b")], None)

    assert trouble and "may not be nested" in trouble[0]


def test_a_watch_is_read_from_the_config_file(tmp_path):
    from noaap.config import load

    (tmp_path / "config.toml").write_text(
        '[[watch]]\nname = "drop"\nfolder = "~/Musik/drop"\n\n'
        '[[watch]]\nname = "mine"\nfolder = "~/Music"\nshape = "library"\n')

    cfg = load(tmp_path / "config.toml")

    assert [(w.name, w.shape) for w in cfg.watches] == [("drop", "intake"), ("mine", "library")]
    assert cfg.watch("mine").folder == Path.home() / "Music"
    assert cfg.watch("nobody") is None
    assert load(tmp_path / "missing.toml").watches == [], "nothing is watched unless it is configured"


def test_the_state_file_is_the_users_own_business(tmp_path):
    """Its notes are the shape of somebody's music collection (R-200, ruling 5)."""
    from noaap.watch import read_state, write_state

    path = write_state({"drop": {"looked": "2026-09-29"}}, tmp_path / "watch-state.json")

    assert path.stat().st_mode & 0o777 == 0o600
    assert read_state(path) == {"drop": {"looked": "2026-09-29"}}
    assert read_state(tmp_path / "gone.json") == {}, "no state is not an error"


# -- the one way in: a name and a path inside it -------------------------------------------------------


@pytest.fixture
def served(tmp_path, monkeypatch):
    """An App whose config names one intake folder and one adopted library."""
    from noaap.config import Config, Watch
    from noaap.web import App

    drop, library = tmp_path / "drop", tmp_path / "library"
    (drop / "A Band" / "An Album").mkdir(parents=True)
    library.mkdir()
    cfg = Config(musicbrainz=False, lyrics=False)
    cfg.watches = [Watch("drop", drop, "intake"), Watch("mine", library, "library")]
    return App(cfg, library), drop, library


@pytest.mark.parametrize("body,why", [
    ({"watch": "nobody", "album": "x"}, "no watched folder"),
    ({"watch": "drop", "album": "/etc"}, "named by its path inside"),
    ({"watch": "drop", "album": ""}, "named by its path inside"),
    ({"watch": "drop", "album": "../../etc"}, "not inside the watched folder"),
    ({"watch": "drop", "album": "A Band/Nothing Here"}, "no such folder"),
])
def test_an_arrival_is_refused_unless_it_is_inside_a_configured_folder(served, body, why):
    """R-200, ruling 2. The action never takes an absolute path, and a path that climbs out of the
    root is refused after resolving — which is what stops `..` and a symlink pointing elsewhere."""
    app, _, _ = served

    with pytest.raises(ValueError, match=why):
        app.submit("arrived", body)


def test_an_arrival_inside_the_folder_becomes_a_job(served):
    app, _, _ = served

    job = app.submit("arrived", {"watch": "drop", "album": "A Band/An Album"})

    assert job.kind == "watch" and job.lane == "write"
    assert job.label == "Take in drop: A Band/An Album", "the watch and the album, never a path"


def test_the_label_never_carries_a_path(served, tmp_path):
    """R-200, ruling 5: nothing that reaches a log line the page shows names the user's home."""
    app, _, _ = served

    job = app.submit("arrived", {"watch": "drop", "album": "A Band/An Album"})

    assert str(tmp_path) not in job.label and "/home/" not in job.label


# -- one look, and what it leads to -----------------------------------------------------------------


def a_run(tmp_path, shape="intake", settle=20):
    from noaap.watch import Run, Watcher

    folder = tmp_path / "drop"
    folder.mkdir(exist_ok=True)
    return Run(name="drop", folder=folder, shape=shape, watcher=Watcher(settle=settle))


def an_album(run, *names: str) -> None:
    album = run.folder / "A Band" / "An Album"
    album.mkdir(parents=True, exist_ok=True)
    for name in names or ("01 - One.mp3",):
        (album / name).write_bytes(b"x" * 10)


AUDIO = (".mp3", ".flac", ".opus")


def test_a_settled_album_is_offered_once(tmp_path):
    from noaap.watch import once

    run = a_run(tmp_path)
    an_album(run)
    asked: list[tuple[str, str]] = []

    assert once(run, AUDIO, 0, lambda n, a: asked.append((n, a)) or True) == []
    assert once(run, AUDIO, 30, lambda n, a: asked.append((n, a)) or True) == ["A Band/An Album"]
    assert once(run, AUDIO, 60, lambda n, a: asked.append((n, a)) or True) == []
    assert asked == [("drop", "A Band/An Album")]


def test_an_arrival_the_app_would_not_take_is_offered_again_and_then_left(tmp_path):
    """Three tries, then it is said out loud and not repeated — a watcher that asks for ever is a
    watcher nobody can read the log of."""
    from noaap.watch import TRIES, once

    run = a_run(tmp_path)
    an_album(run)
    said: list[str] = []
    tries = 0

    for second in range(0, 300, 30):
        got = once(run, AUDIO, second, lambda n, a: False, log=said.append)
        tries += 1 if got == [] and second >= 20 else 0

    assert run.waiting["A Band/An Album"] == TRIES
    assert any("was not taken after 3 tries" in line for line in said)


def test_a_folder_that_is_not_there_is_said_once_and_not_again(tmp_path):
    from noaap.watch import once

    run = a_run(tmp_path)
    run.folder.rmdir()
    said: list[str] = []

    for second in (0, 30, 60):
        assert once(run, AUDIO, second, lambda n, a: True, log=said.append) == []

    assert said == ["drop: the folder is not there"]


def test_a_folder_that_comes_back_is_said_too(tmp_path):
    from noaap.watch import once

    run = a_run(tmp_path)
    run.folder.rmdir()
    said: list[str] = []
    once(run, AUDIO, 0, lambda n, a: True, log=said.append)
    run.folder.mkdir()

    once(run, AUDIO, 30, lambda n, a: True, log=said.append)

    assert said == ["drop: the folder is not there", "drop: the folder is back"]


def test_a_loose_file_is_named_and_left(tmp_path):
    from noaap.watch import once

    run = a_run(tmp_path)
    (run.folder / "stray.mp3").write_bytes(b"x")
    said: list[str] = []
    asked: list[str] = []

    once(run, AUDIO, 0, lambda n, a: asked.append(a) or True, log=said.append)
    got = once(run, AUDIO, 30, lambda n, a: asked.append(a) or True, log=said.append)

    assert got == [] and asked == []
    assert any("put them in a folder" in line for line in said)


def test_what_is_written_down_names_the_watch_and_never_a_path(tmp_path):
    """R-200, ruling 5."""
    from noaap.watch import keep, once

    run = a_run(tmp_path)
    an_album(run)
    once(run, AUDIO, 0, lambda n, a: True)

    state = keep([run], "2026-09-29 00:00")

    assert set(state) == {"drop"}
    assert str(tmp_path) not in json.dumps(state)
