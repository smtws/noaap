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
from pathlib import Path

from test_incremental import opus_template
from test_web import library

from noaap.config import Config
from noaap.download import load_plan, save_plan
from noaap.web import App


def counting_io(monkeypatch):
    """Every path a walk or a patch touches on the disk, in order. `stat` and `glob` are each a
    round trip to the share, which is what made the user's `/api/state` 8 seconds (R-524)."""
    where: list[str] = []
    real_stat, real_glob = Path.stat, Path.glob

    def stat(self, *a, **k):
        where.append(str(self))
        return real_stat(self, *a, **k)

    def glob(self, pattern, *a, **k):
        where.append(f"{self}/{pattern}")
        return real_glob(self, pattern, *a, **k)

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(Path, "glob", glob)
    return where


def an_app(library, **kw):
    return App(Config(library_root=library, **kw), library)


# -- one walk, and reads come out of it ----------------------------------------------------------


def test_a_state_request_does_not_walk_the_library(library, monkeypatch):
    app = an_app(library)
    app.state()                      # the first caller pays for the first walk

    import threading

    # the background thread walks on its own schedule and is not what this is about (slice 152)
    app._stop_scanning.set()

    walks = []
    real = type(library).glob
    monkeypatch.setattr(type(library), "glob",
                        lambda self, pattern: (walks.append((threading.current_thread().name,
                                                             pattern)), real(self, pattern))[1])

    here = threading.current_thread().name
    app.state()
    app.state()
    app.state()
    app.albums()                     # and the method behind it, which the panel and the API use
    app.missing()

    assert [w for w in walks if w[0] == here] == [], walks


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
    just done is broken — so a finished write job **patches its own album** into what is held, and
    the next read sees it without walking the library (§9, slice 152)."""
    app = an_app(library)
    before = app.state()["tracks_version"]
    album_dir = next(library.glob("*/*/.noaap.json")).parent
    source_id = load_plan(album_dir).source_id
    plan = load_plan(album_dir)
    plan.album = "After the write"
    save_plan(plan, album_dir)

    app.wrote_one(source_id)         # what the job lane calls when a write finishes

    after = app.state()
    assert after["tracks_version"] != before
    assert any(a["album"] == "After the write" for a in after["albums"])


def test_a_state_request_never_walks_however_stale_it_is_told_it_may_be(library, monkeypatch):
    """The 8-second `/api/state` the user hit (R-523/R-524): `held` walked whenever anything had
    been written, and every write set that flag, so nearly every request paid a full walk — three
    thousand `stat`s over a busy NFS share. A page's answer must not depend on the disk's load."""
    app = an_app(library)
    app.state()                                 # the first caller may walk; it has nothing else

    import threading

    walkers = []
    monkeypatch.setattr(app, "_walk",
                        lambda *a, **k: walkers.append(threading.current_thread().name) or app._held)
    app.library_changed()                       # a write by somebody who did not say which album

    here = threading.current_thread().name
    app.state()
    app.state()
    assert here not in walkers, \
        f"a read must answer from what is held; it walked on the request's own thread ({walkers})"


def test_a_write_that_does_not_name_its_album_still_asks_for_a_walk(library):
    app = an_app(library)
    app.state()
    app._dirty.clear()

    app.wrote_one(None)                         # `update` over the whole library names no album
    assert app._dirty.is_set()


def test_patching_one_album_does_not_read_the_others(library):
    """What the patch is for: three I/O calls instead of three thousand."""
    app = an_app(library)
    app.state()
    album_dir = next(library.glob("*/*/.noaap.json")).parent
    source_id = load_plan(album_dir).source_id
    others = {d: v for d, v in app._held.plans.items() if d != album_dir}

    plan = load_plan(album_dir)
    plan.album = "Patched"
    save_plan(plan, album_dir)
    app.wrote_one(source_id)

    assert any(a["album"] == "Patched" for a in app.state()["albums"])
    for d, (when, kept) in others.items():
        assert app._held.plans[d][0] == when, f"{d} was not touched"
        assert app._held.plans[d][1] is kept, f"{d}'s plan object was not read again"


def test_a_patch_and_a_walk_agree_on_the_version(library):
    """If they disagreed, the page would re-fetch the track index on every write (or never)."""
    app = an_app(library)
    app.state()
    app._stop_scanning.set()        # else the background walk recomputes it and proves nothing
    album_dir = next(library.glob("*/*/.noaap.json")).parent
    source_id = load_plan(album_dir).source_id

    plan = load_plan(album_dir)
    plan.album = "Both ways"
    save_plan(plan, album_dir)

    app.wrote_one(source_id)
    patched = app.state()["tracks_version"]
    app.rescan()
    assert app.state()["tracks_version"] == patched


def test_an_album_removed_under_a_patch_leaves_the_model(library):
    import shutil

    app = an_app(library)
    app.state()
    album_dir = next(library.glob("*/*/.noaap.json")).parent
    source_id = load_plan(album_dir).source_id
    assert any(a["id"] == source_id for a in app.state()["albums"])

    shutil.rmtree(album_dir)
    app.wrote_one(source_id)

    assert not any(a["id"] == source_id for a in app.state()["albums"])
    assert source_id not in app._held.index


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


# -- a cover or a track never waits behind a rescan (R-499 item 4) -------------------------------


def test_a_cover_does_not_wait_for_a_rescan(library):
    """A page load fires one cover request per card. None of them may queue behind a walk, so they
    are answered from what is held even while the model is stale.

    Counted **per thread**: the rescan a stale model is owed happens on the scanner's thread, which
    is the whole point — what must not happen is the walk being done by the request.
    """
    import threading

    import pytest

    app = an_app(library)
    first = app.state()["albums"][0]["id"]
    app.library_changed()            # the model is stale, and a rescan is due

    mine = threading.current_thread()
    walked = []
    real = type(library).glob

    def counting(self, pattern):
        if threading.current_thread() is mine:
            walked.append(pattern)
        return real(self, pattern)

    with pytest.MonkeyPatch.context() as m:
        m.setattr(type(library), "glob", counting)
        assert app.album(first) is not None
        app.cover(first)

    assert walked == ["cover.*"], walked   # one listing of that album's own folder, and no walk


def test_nothing_is_read_while_the_walker_holds_its_lock(library):
    """The walk's lock is the walker's alone: a reader never takes it, so a rescan over a slow
    filesystem cannot stop a track from playing."""
    app = an_app(library)
    first = app.state()["albums"][0]["id"]

    app._rescanning.acquire()        # as if a walk were in progress
    try:
        start = time.monotonic()
        assert app.album(first) is not None
        assert app.state()["albums"]
        assert time.monotonic() - start < 1.0
    finally:
        app._rescanning.release()


def test_a_slow_share_does_not_reach_the_state_request(library, monkeypatch):
    """The user's case, counted rather than timed (R-524). Every `stat` and every `glob` of a walk
    is a round trip to the share, so the thing that made `/api/state` take 8 seconds is that it did
    them at all. However much has been written since, a state request must do **none**."""
    app = an_app(library)
    app.state()                                 # the first caller walks; it has nothing else
    app._stop_scanning.set()                    # the background thread is not what is measured here
    app._dirty.clear()

    trips = counting_io(monkeypatch)
    app.library_changed()                       # "something was written", the old trigger to walk
    for _ in range(3):
        app.state()

    assert trips == [], f"a state request went to the disk {len(trips)} time(s): {trips[:5]}"


def test_the_patch_reads_one_album_where_the_walk_reads_the_library(library, monkeypatch):
    """And what replaced it: seeing your own write costs this album's I/O, not the library's. On the
    user's 1,523 albums the walk is one glob plus two `stat`s each — about three thousand trips."""
    app = an_app(library)
    album_dir = next(library.glob("*/*/.noaap.json")).parent
    # a few more albums, so "one album" and "the library" are different sizes at all
    for n in range(2, 6):
        other = library / "My Dark Lullabies" / f"Vol. {n} - Copied"
        other.mkdir(parents=True, exist_ok=True)
        plan = load_plan(album_dir)
        plan.album = f"Vol. {n} - Copied"
        plan.source_id = f"{plan.source_id}-{n}"
        plan.source_url = f"{plan.source_url}-{n}"
        save_plan(plan, other)
    app.state()
    app._stop_scanning.set()
    source_id = load_plan(album_dir).source_id

    patch_trips = counting_io(monkeypatch)
    app.album_changed(album_dir)
    patch_only = list(patch_trips)          # snapshot: the counter below wraps this one

    walk_trips = counting_io(monkeypatch)
    before_walk = len(walk_trips)
    app.rescan()
    walked = len(walk_trips) - before_walk

    assert all(str(where).startswith(str(album_dir)) for where in patch_only), \
        f"the patch looked outside its album: {patch_only}"
    assert len(patch_only) < walked, f"patch {len(patch_only)} trips, walk {walked}"
    assert any(a["id"] == source_id for a in app.state()["albums"])


def test_a_burst_of_writes_costs_one_walk_not_twenty(library):
    """Since a read never walks, the background thread is the only thing that does — and every write
    marks the library dirty. An `update` writing album after album would have it walking back to
    back, seconds of the share's attention each time, for nothing a page waits on (§9, slice 152).
    `QUIET_SECONDS` is the floor between two walks. The margin here is wide on purpose: the claim is
    "a burst coalesces", not a number of milliseconds."""
    app = an_app(library)
    app.STALE_SECONDS, app.QUIET_SECONDS = 0.01, 0.2
    walks = []
    real = app._walk
    app._walk = lambda *a, **k: (walks.append(1), real(*a, **k))[1]
    app.state()                                     # starts the scanner, pays the first walk
    walks.clear()

    for _ in range(20):
        app.library_changed()
        time.sleep(0.005)
    time.sleep(0.1)
    app._stop_scanning.set()

    assert len(walks) <= 3, f"{len(walks)} walks for twenty writes in 0.1 s"
