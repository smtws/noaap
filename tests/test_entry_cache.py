"""An unchanged playlist is read from the entry cache (§9, slice 200; q22 (ii), R-674).

A deep check read every video of every YouTube album again — one yt-dlp extract each, ≈2.2 s on the
user's connection, 3 to 5 h of the 11.5-h library-wide check of 2026-10-13 — for playlists that had not
changed. The cache answers only where the listing has exactly the plan's ids, in their order, and
every video is cached, inside its time and as the listing still describes it.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import time
from pathlib import Path
from typing import ClassVar

import pytest

from noaap import youtube as youtube_mod
from noaap.config import Config, cache_dir
from noaap.youtube import YouTube

FIXTURES = Path(__file__).parent.parent / "design-fixtures"
URL = "https://www.youtube.com/playlist?list=PL1SYtzVzb0GJJ5KixpEZjWvXjV96uIyd3"


class Counting:
    """The playlist as the flat listing gives it, and one full read per video, counted."""

    listing = json.loads((FIXTURES / "vol1.json").read_text())
    videos = 0
    skip: ClassVar[set[str]] = set()

    def __init__(self, params):
        self.params = params

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if "list=" in url:
            return copy.deepcopy(type(self).listing)
        type(self).videos += 1
        vid = url.rsplit("=", 1)[-1] if "=" in url else url
        if vid in type(self).skip:
            from yt_dlp.utils import DownloadError
            raise DownloadError("ERROR: Sign in to confirm your age")
        flat = next(e for e in type(self).listing["entries"] if e["id"] == vid)
        return {"id": vid, "title": flat["title"], "duration": flat.get("duration"), "channel": "A Channel",
                "artist": "Enemy Inside", "track": flat["title"], "album": "Vol. 1"}


@pytest.fixture
def yt(monkeypatch):
    monkeypatch.setattr(youtube_mod, "YoutubeDL", Counting)
    Counting.listing = json.loads((FIXTURES / "vol1.json").read_text())
    Counting.videos, Counting.skip = 0, set()
    return YouTube(Config(concurrency=1))


def ids():
    return [e["id"] for e in Counting.listing["entries"]]


def test_an_unchanged_playlist_is_read_from_the_cache_the_second_time(yt):
    first = yt.fetch(URL, known_ids=ids())
    asked = Counting.videos
    second = yt.fetch(URL, known_ids=ids())

    assert asked == len(ids()) and Counting.videos == asked, "the second read asks no video"
    assert second.entries == first.entries


def test_without_the_plan_s_ids_every_video_is_read(yt):
    yt.fetch(URL, known_ids=ids())
    yt.fetch(URL)
    assert Counting.videos == 2 * len(ids())


@pytest.mark.parametrize("change", ["another id", "reordered", "one missing"])
def test_ids_that_are_not_the_plan_s_in_its_order_are_a_full_read(yt, change):
    yt.fetch(URL, known_ids=ids())
    known = ids()
    if change == "another id":
        known[3] = "F4vgTOIB4HQ"          # Das Elfte Gebot: the playlist lists another video at 4
    elif change == "reordered":
        known[1], known[2] = known[2], known[1]
    else:
        known = known[:-1]
    yt.fetch(URL, known_ids=known)
    assert Counting.videos == 2 * len(ids())


def test_one_video_not_cached_is_a_full_read(yt):
    yt.fetch(URL, known_ids=ids())
    with sqlite3.connect(cache_dir() / "youtube-entries.sqlite3") as db:
        db.execute("DELETE FROM entries WHERE id = ?", (ids()[5],))
    yt.fetch(URL, known_ids=ids())
    assert Counting.videos == 2 * len(ids())


def test_an_expired_entry_is_a_full_read(yt):
    yt.fetch(URL, known_ids=ids())
    with sqlite3.connect(cache_dir() / "youtube-entries.sqlite3") as db:
        db.execute("UPDATE entries SET at = ? WHERE id = ?", (time.time() - 31 * 86400, ids()[0]))
    yt.fetch(URL, known_ids=ids())
    assert Counting.videos == 2 * len(ids())


@pytest.mark.parametrize("field", ["title", "duration"])
def test_a_video_the_listing_now_describes_otherwise_is_a_full_read(yt, field):
    yt.fetch(URL, known_ids=ids())
    e = Counting.listing["entries"][2]
    e[field] = e[field] + " (Remastered)" if field == "title" else (e[field] or 0) + 30
    again = yt.fetch(URL, known_ids=ids())
    assert Counting.videos == 2 * len(ids())
    assert getattr(again.entries[2], field) == e[field], "and the new read is what the check sees"


def test_a_skipped_video_is_never_cached(yt):
    Counting.skip = {ids()[4]}
    first = yt.fetch(URL, known_ids=ids())
    assert first.entries[4].skipped
    Counting.skip = set()
    yt.fetch(URL, known_ids=ids())
    assert Counting.videos == 2 * len(ids()), "the album is read whole again, and the skipped video with it"


def test_no_cache_when_it_is_turned_off(monkeypatch):
    monkeypatch.setattr(youtube_mod, "YoutubeDL", Counting)
    Counting.listing = json.loads((FIXTURES / "vol1.json").read_text())
    Counting.videos, Counting.skip = 0, set()
    yt = YouTube(Config(concurrency=1, entry_days=0))
    yt.fetch(URL, known_ids=ids())
    yt.fetch(URL, known_ids=ids())
    assert Counting.videos == 2 * len(ids())


def test_a_pass_hands_the_plan_s_ids_to_the_source_and_a_lone_fetch_does_not(tmp_path):
    from test_incremental import FakeYouTube, vol1

    from noaap.download import run
    from noaap.plan import build_plan
    from noaap.service import Service

    template = tmp_path / "t.opus"
    template.write_bytes(b"")

    class Keeping(FakeYouTube):
        keeps_entries = True
        asked: ClassVar[list] = []

        def collection(self, url, known_ids=None):
            type(self).asked.append(known_ids)
            return vol1()

    plan = build_plan(vol1())
    plan.source_state = {"ids": [e.video_id for e in vol1().entries], "modified": "20260828"}
    run(plan, tmp_path / plan.folder, FakeYouTube(template), download=False)
    svc = Service(Config(musicbrainz=False, lyrics=False), tmp_path, yt=Keeping(template))

    svc.update_all(report_only=True, deep=True)
    svc.fetch(plan.source_url, report_only=True)

    assert Keeping.asked == [plan.source_state["ids"], None]
