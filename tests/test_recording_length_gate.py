"""A per-track lookup does not take a recording longer than the video (§9, slice 204; q26, R-688).

`Evanescence — Fallen (Deluxe)`, 2026-10-14: the new `Going Under` video (218 s) was given `954b2a1f`,
the São Paulo 2023 live recording (261 s) — of MusicBrainz' ten answers, all scored 100, the only one
on an official album. The answers are the cached ones of that day.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_incremental import vol1

from noaap.enrich import enrich_track, pick_recording
from noaap.plan import build_plan

ANSWERS = json.loads((Path(__file__).parent.parent / "design-fixtures" / "mb_recordings_going_under.json").read_text())
RECS = ANSWERS["recordings"]


class Cached:
    def search_recordings(self, artist, title):
        return RECS if title == "Going Under" else []


class AsIfStudio(Cached):
    """The same answers with 954b2a1f's `live, …` taken off: the length rule alone, since slice 209
    refuses a live recording for a track that does not say live whatever its length."""

    def search_recordings(self, artist, title):
        return [{**r, "disambiguation": ""} if r["id"].startswith("954b2a1f") else r
                for r in super().search_recordings(artist, title)]


def a_track(seconds, override=None):
    t = build_plan(vol1()).tracks[0]
    t.artist, t.title, t.duration, t.source_override = "Evanescence", "Going Under", seconds, override
    return t


def test_without_the_video_s_length_the_live_recording_is_taken_as_before():
    assert pick_recording("Evanescence", "Going Under", RECS)["id"].startswith("954b2a1f")


def test_a_recording_longer_than_the_video_is_refused_and_the_next_is_taken():
    t = a_track(218.0)
    assert enrich_track(t, Cached())
    assert t.mbid.startswith("f9686f62") and t.mb_length is None


def test_a_shorter_recording_still_passes():
    """Film around the song: a 300-s video keeps the 261-s recording, whose length the trim cuts to."""
    t = a_track(300.0)
    assert enrich_track(t, AsIfStudio())
    assert t.mbid.startswith("954b2a1f") and t.mb_length == 261.4


@pytest.mark.parametrize("seconds", [258.0, 266.0])
def test_within_the_slack_it_passes(seconds):
    t = a_track(seconds)
    enrich_track(t, AsIfStudio())
    assert t.mbid.startswith("954b2a1f")


def test_a_track_whose_audio_is_another_video_s_is_not_measured_by_this_one():
    t = a_track(218.0, override="2qhLxfZJlbs")
    enrich_track(t, AsIfStudio())
    assert t.mbid.startswith("954b2a1f")


def test_where_every_answer_is_longer_the_spelling_is_still_confirmed_and_nothing_attached():
    class OnlyLonger:
        def search_recordings(self, artist, title):
            return [r for r in RECS if r["id"].startswith("954b2a1f")]
    t = a_track(218.0)
    t.title = "going under"
    assert enrich_track(t, OnlyLonger())
    assert (t.title, t.mbid, t.mb_length) == ("Going Under", None, None)
