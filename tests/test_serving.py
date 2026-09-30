"""What the server does before the first byte (DESIGN §9, slice 90).

The user: *"initial play after refresh takes forever to start (like 10 seconds) on many tracks."*
Measured on a library the size of theirs (`docs/qa-catalog.md`, CD): every request that names an album
read the plans until it found it — 6 ms for the first album, **155 ms for the last** — and a refresh
fired one cover request per card, each of them uncacheable, so **52 MB** and about eighteen seconds of
JSON parsing stood between the press of play and the first byte of audio. These are the rules that came
out of it.
"""
from __future__ import annotations

import email.utils
import shutil
import threading
import time

import httpx
import pytest
from test_incremental import FakeYouTube, opus_template, vol1

from noaap.config import Config
from noaap.download import load_plan, save_plan
from noaap.plan import build_plan
from noaap.web import App, _Handler, _Server

#: the smallest real PNG, so that the cover route has something it recognises
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000"
                    "a49444154789c6300010000050001od0a2db40000000049454e44ae426082".replace("o", "6"))


@pytest.fixture
def many(tmp_path, opus_template):
    """Five albums, so that "the last one" means something."""
    for n in range(1, 6):
        plan = build_plan(vol1())
        plan.source_id = f"album-{n}"
        plan.album = f"Album {n}"
        plan.albumartist = f"Artist {n}"
        plan.folder = f"Artist {n}/Album {n}"
        album_dir = tmp_path / plan.folder
        album_dir.mkdir(parents=True)
        for track in plan.tracks[:2]:
            track.state = "done"
            track.filename = f"{track.number:02d} {track.title}.opus"
            shutil.copy(opus_template, album_dir / track.filename)
        plan.tracks = plan.tracks[:2]
        (album_dir / "cover.png").write_bytes(PNG)
        save_plan(plan, album_dir)
    return tmp_path


@pytest.fixture
def serving(many, opus_template):
    app = App(Config(musicbrainz=False), many, port=0)
    srv = app.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}", timeout=10) as client:
        yield app, client
    srv.shutdown()


# -- an album is found by an index, not by reading the library ---------------------------------------


def test_an_album_is_found_without_reading_the_others(many, monkeypatch):
    app = App(Config(musicbrainz=False), many, port=0)

    assert app.album("album-5") is not None            # builds the index
    monkeypatch.setattr("noaap.web.iter_plans",
                        lambda _lib: (_ for _ in ()).throw(AssertionError("walked the library")))

    for wanted in ("album-1", "album-3", "album-5"):
        found = app.album(wanted)
        assert found is not None and found[1].source_id == wanted


def test_a_new_album_is_found_after_the_library_changes(many):
    app = App(Config(musicbrainz=False), many, port=0)
    assert app.album("album-1") is not None
    assert app.album("album-9") is None

    plan = load_plan(many / "Artist 1" / "Album 1")
    plan.source_id = "album-9"
    (many / "Artist 9" / "Album 9").mkdir(parents=True)
    save_plan(plan, many / "Artist 9" / "Album 9")

    found = app.album("album-9")
    assert found is not None and found[0].name == "Album 9"


def test_an_album_whose_folder_went_away_is_looked_for_again(many):
    app = App(Config(musicbrainz=False), many, port=0)
    assert app.album("album-2") is not None
    shutil.move(many / "Artist 2" / "Album 2", many / "Artist 2" / "Moved")

    found = app.album("album-2")

    assert found is not None and found[0].name == "Moved", "the index is a shortcut, not the truth"


# -- what the client already has is not sent again ----------------------------------------------------


def test_a_cover_may_be_cached_and_is_answered_with_304(serving):
    _, c = serving
    first = c.get("/api/cover?id=album-3")

    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-cache", "no-store made a refresh fetch every cover"
    assert "last-modified" in first.headers

    again = c.get("/api/cover?id=album-3", headers={"If-Modified-Since": first.headers["last-modified"]})

    assert again.status_code == 304 and not again.content
    assert again.headers["content-length"] == "0"


def test_an_audio_file_is_answered_with_304_too(serving):
    app, c = serving
    track = app.album("album-3")[1].tracks[0].video_id
    first = c.get(f"/api/audio?id=album-3&v={track}")
    assert first.status_code == 200 and first.content

    again = c.get(f"/api/audio?id=album-3&v={track}",
                  headers={"If-Modified-Since": first.headers["last-modified"]})

    assert again.status_code == 304 and not again.content


def test_a_range_request_is_never_answered_with_304(serving):
    """A player asking for a piece it does not have must be given the piece."""
    app, c = serving
    track = app.album("album-3")[1].tracks[0].video_id
    first = c.get(f"/api/audio?id=album-3&v={track}")

    piece = c.get(f"/api/audio?id=album-3&v={track}",
                  headers={"Range": "bytes=0-99", "If-Modified-Since": first.headers["last-modified"]})

    assert piece.status_code == 206 and len(piece.content) == 100


def test_a_file_that_changed_is_sent_again(serving, many, opus_template):
    app, c = serving
    track = app.album("album-3")[1].tracks[0]
    path = many / "Artist 3" / "Album 3" / track.filename
    first = c.get(f"/api/audio?id=album-3&v={track.video_id}")
    later = email.utils.formatdate(time.time() + 50, usegmt=True)
    import os

    os.utime(path, (time.time() + 5, time.time() + 5))   # written after the client's copy

    assert c.get(f"/api/audio?id=album-3&v={track.video_id}",
                 headers={"If-Modified-Since": first.headers["last-modified"]}).status_code == 200
    # and a client claiming a time in the future is still told nothing changed
    assert c.get(f"/api/audio?id=album-3&v={track.video_id}",
                 headers={"If-Modified-Since": later}).status_code == 304


def test_a_header_nobody_can_parse_is_ignored(serving):
    _, c = serving
    answer = c.get("/api/cover?id=album-3", headers={"If-Modified-Since": "not a date at all"})

    assert answer.status_code == 200 and answer.content


# -- the log: a slow request says so, a client that left does not -------------------------------------


def test_a_slow_request_is_logged_with_its_path(serving, monkeypatch, caplog):
    _, c = serving
    monkeypatch.setattr(_Handler, "SLOW", 0.0)

    def said() -> bool:
        return any("slow request" in r.getMessage() and "/api/state" in r.getMessage()
                   for r in caplog.records)

    with caplog.at_level("WARNING"):
        c.get("/api/state")
        # the line is written by the thread that served the request, after the answer went out: the
        # client is back before the server has finished, so this waits rather than races it
        for _ in range(100):
            if said():
                break
            time.sleep(0.02)

    assert said(), caplog.text


def test_a_quick_request_says_nothing(serving, caplog):
    _, c = serving
    with caplog.at_level("WARNING"):
        c.get("/api/cover?id=album-1")
        time.sleep(0.2)   # long enough for a line to appear if there were one
    assert not [r for r in caplog.records if "slow request" in r.getMessage()]


@pytest.mark.parametrize("gone", [ConnectionResetError, BrokenPipeError, TimeoutError])
def test_a_client_that_left_is_one_line_and_no_traceback(many, caplog, gone):
    """A media element opens, seeks and abandons connections constantly; the journal was full of
    tracebacks for it since the server started speaking HTTP/1.1 (R-304)."""
    app = App(Config(musicbrainz=False), many, port=0)
    server = app.make_server()
    assert isinstance(server, _Server)

    with caplog.at_level("DEBUG"):
        try:
            raise gone("the player hung up")
        except gone:
            server.handle_error(None, ("127.0.0.1", 1234))

    assert [r for r in caplog.records if "client went away" in r.getMessage()]
    assert not [r for r in caplog.records if r.exc_info], "no traceback for a client that left"
    server.server_close()


def test_a_real_error_still_shows_its_traceback(many, caplog):
    app = App(Config(musicbrainz=False), many, port=0)
    server = app.make_server()

    with caplog.at_level("ERROR"):
        try:
            raise ValueError("something of ours is wrong")
        except ValueError:
            server.handle_error(None, ("127.0.0.1", 1234))

    assert [r for r in caplog.records if r.exc_info], "our own bugs are still reported in full"
    server.server_close()


# -- what the header is told about the bin (§9, slice 91) ---------------------------------------------


def test_the_state_counts_the_bin_without_reading_it(serving, many, monkeypatch):
    """The page polls this, so it may not read a file per entry (§9, slice 91)."""
    from noaap.recycle import bin_track
    from noaap.web import App

    app, c = serving
    assert c.get("/api/state").json()["recycled"] == 0

    album_dir, plan = app.album("album-2")
    track = plan.tracks[0]
    bin_track(many, album_dir, plan, track, reason="tested", audio=album_dir / track.filename)

    assert c.get("/api/state").json()["recycled"] == 1
    assert len(c.get("/api/recycle").json()["entries"]) == 1

    # and the count never opens an entry: the full listing is what does that
    monkeypatch.setattr("noaap.recycle.resolved",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("read an entry")))
    assert App(Config(musicbrainz=False), many, port=0).recycled() == 1


def test_a_library_with_no_bin_counts_zero(serving):
    _, c = serving
    assert c.get("/api/state").json()["recycled"] == 0
