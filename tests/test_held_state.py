"""The library's state lives in memory (DESIGN §9, slice 139; R-499, P103).

Measured on the user's library over NFS, 1,523 albums and 20,100 tracks: one `/api/state` cost
**107 seconds**, cold and warm alike, because it walked the library three times — `albums()` read
and parsed every plan, `missing()` read them all again and `stat`ed every one of the 20,100 files
(62.6 s of that), and `library_version()` ran an `rglob` over every directory to hash mtimes the
other two had already read. `album()` asked for the version too, so a cover request paid the same
`rglob`, and a page load fires one per card. The user's page took 8.3 s per poll on a local disk and
a request from the side waited 104 s behind the polling.
"""

from __future__ import annotations

import time

from test_incremental import opus_template
from test_web import library

from noaap.config import Config
from noaap.download import load_plan, save_plan
from noaap.web import App


def an_app(library, **kw):
    return App(Config(library_root=library, **kw), library)


# -- one walk, and reads come out of it ----------------------------------------------------------


def test_a_state_request_does_not_walk_the_library(library, monkeypatch):
    app = an_app(library)
    app.state()                      # the first caller pays for the first walk

    walks = []
    real = type(library).glob
    monkeypatch.setattr(type(library), "glob",
                        lambda self, pattern: (walks.append(pattern), real(self, pattern))[1])

    app.state()
    app.state()
    app.state()
    app.albums()                     # and the method behind it, which the panel and the API use
    app.missing()

    assert walks == [], walks


def test_the_version_and_the_albums_come_from_the_same_walk(library, monkeypatch):
    app = an_app(library)
    state = app.state()

    assert state["tracks_version"] == app.held().version
    assert state["albums"] is app.held().albums, "the same list, not a second reading of the disk"

    walked = []
    real = type(app.library).rglob
    monkeypatch.setattr(type(app.library), "rglob",
                        lambda self, pattern: (walked.append(pattern), real(self, pattern))[1])

    assert app.library_version() == state["tracks_version"]
    assert walked == [], "the version is the walk's, not a tree of its own"


def test_the_answer_says_how_old_it_is(library):
    app = an_app(library)
    state = app.state()

    assert state["held_at"] >= 0
    assert state["stale_after"] == app.STALE_SECONDS == 60


def test_a_cover_request_does_not_walk_the_library(library, monkeypatch):
    """`album()` called `library_version()`, which was an `rglob` of the whole tree — and a page
    load fires one cover request per card."""
    app = an_app(library)
    first = app.state()["albums"][0]["id"]
    app.album(first)

    walked = []
    real = type(library).rglob
    monkeypatch.setattr(type(library), "rglob",
                        lambda self, pattern: (walked.append(pattern), real(self, pattern))[1])

    for _ in range(5):
        assert app.album(first) is not None

    assert walked == [], walked


def test_an_album_panel_reads_the_plan_from_disk(library):
    """The grid may be a minute old; the panel is where a person edits, and an edit must start from
    what is on disk this second."""
    app = an_app(library)
    first = app.state()["albums"][0]["id"]
    album_dir, _ = app.album(first)
    plan = load_plan(album_dir)
    plan.album = "Changed on disk"
    save_plan(plan, album_dir)

    assert app.album(first)[1].album == "Changed on disk"


# -- what makes it stale -------------------------------------------------------------------------


def test_our_own_write_is_seen_at_once(library):
    """The minute bound is for changes made behind noaap's back. A page that cannot see what it has
    just done is broken, so a finished write job marks the model stale and the next read rescans."""
    app = an_app(library)
    before = app.state()["tracks_version"]
    album_dir = next(library.glob("*/*/.noaap.json")).parent
    plan = load_plan(album_dir)
    plan.album = "After the write"
    save_plan(plan, album_dir)

    app.library_changed()            # what the job lane calls when a write finishes

    after = app.state()
    assert after["tracks_version"] != before
    assert any(a["album"] == "After the write" for a in after["albums"])


def test_a_changed_library_throws_the_model_away(library, tmp_path):
    app = an_app(library)
    assert app.state()["albums"], "there is something in it to begin with"

    elsewhere = tmp_path / "another"
    elsewhere.mkdir()
    app.library = elsewhere
    app._held = type(app._held)()    # what the settings handler does

    assert app.state()["albums"] == []


def test_only_the_plans_that_changed_are_read_again(library, monkeypatch):
    app = an_app(library)
    app.state()

    read = []
    import noaap.web as web

    real = web.read_plan
    monkeypatch.setattr(web, "read_plan", lambda path, d: (read.append(path), real(path, d))[1])

    app.rescan()
    assert read == [], "nothing changed, so nothing was parsed again"

    album_dir = next(library.glob("*/*/.noaap.json")).parent
    plan = load_plan(album_dir)
    plan.album = "Only this one"
    save_plan(plan, album_dir)
    app.rescan()

    assert [p.parent for p in read] == [album_dir], read


def test_one_walk_at_a_time(library):
    """Two readers must not both walk: the second takes what is held."""
    app = an_app(library)
    app.state()
    app._rescanning.acquire()
    try:
        held = app._held
        assert app.rescan() is held, "it did not walk, it answered"
    finally:
        app._rescanning.release()


# -- the per-track sweep is not on this path -----------------------------------------------------


def test_missing_is_not_measured_by_a_page_request(library):
    """One `stat` per track is 62.6 s on the user's library, so a poll never asks."""
    app = an_app(library)

    assert app.state()["settings"]["missing"] == {"albums": 0, "tracks": 0, "where": [],
                                                  "measured": None}


def test_a_sweep_that_was_asked_for_measures_it(library):
    app = an_app(library)
    album_dir = next(library.glob("*/*/.noaap.json")).parent
    plan = load_plan(album_dir)
    gone = album_dir / plan.tracks[0].filename
    gone.unlink()

    held = app.rescan(measure=True)

    assert held.missing["albums"] == 1 and held.missing["tracks"] == 1
    assert held.missing["measured"], "and it says when it was taken"
    assert app.state()["settings"]["missing"]["tracks"] == 1

