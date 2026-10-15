"""Another version of the song is refused on every pick, either way (§9, slice 209; q31, R-724).

A first lookup took the 2006 Philadelphia bootleg of `Bring Me to Life` as readily as slice 206's
re-ask did before q29d; `noaap recording` offered `Watching Over Me (Alive in Athens 1999)`,
`Elegy (Dvd)` and `Where You Are (Club Version)` as the album take (round I, 2026-10-15).
"""

from __future__ import annotations

import pytest
from test_incremental import vol1

from noaap.complete import Slot, rank
from noaap.enrich import enrich_track
from noaap.plan import build_plan


def answer(rid, title, seconds, disambiguation=""):
    return {"id": rid, "title": title, "length": int(seconds * 1000), "score": 100, "disambiguation": disambiguation,
            "artist-credit": [{"name": "Evanescence", "joinphrase": "", "artist": {"id": "e", "name": "Evanescence"}}],
            "releases": []}


class Answers:
    def __init__(self, *recs):
        self.recs = list(recs)

    def search_recordings(self, artist, title):
        return self.recs


def a_track(title="Bring Me to Life", seconds=254.0):
    t = build_plan(vol1()).tracks[0]
    t.artist, t.title, t.duration, t.mbid = "Evanescence", title, seconds, None
    return t


def test_a_first_lookup_does_not_take_the_philadelphia_bootleg():
    t = a_track()
    enrich_track(t, Answers(answer("e408cf50", "Bring Me to Life", 252.0, "live, 2006-10-10: The Electric Factory, Philadelphia"),
                            answer("studio01", "Bring Me to Life", 236.0)), album="Fallen (Deluxe Edition)")
    assert t.mbid == "studio01"


def test_where_only_another_version_answers_nothing_is_attached():
    t = a_track()
    enrich_track(t, Answers(answer("e408cf50", "Bring Me to Life", 252.0, "live, 2006-10-10: Philadelphia")),
                 album="Fallen (Deluxe Edition)")
    assert t.mbid is None and t.title == "Bring Me to Life"


def slot(title, seconds, artist="Iced Earth"):
    return Slot(disc=1, number=11, title=title, artist=artist, recording="r", length=seconds)


def hit(ref, title, seconds):
    return {"ref": ref, "title": title, "channel": "someone", "length": seconds}


def test_alive_in_athens_is_not_the_album_take():
    ranked = rank(slot("Watching Over Me", 295.0),
                  [hit("athens", "Iced Earth - Watching over me (Alive in Athens 1999)", 300.0),
                   hit("studio", "Iced Earth - Watching Over Me", 290.0)], "Something Wicked This Way Comes")
    assert [h["ref"] for h in ranked] == ["studio"]


@pytest.mark.parametrize("title", ["Leaves' Eyes - Elegy (Dvd)", "Deine Lakaien - Where You Are (Club Version)"])
def test_dvd_and_club_version_are_other_versions(title):
    artist, rest = title.split(" - ", 1)
    s = slot(rest.split(" (")[0], 300.0, artist)
    assert rank(s, [hit("x", title, 300.0)], "An Album") == []


def test_on_a_live_album_a_hit_that_does_not_say_live_is_refused_too():
    s = slot("Stossgebet", 244.0, "Powerwolf")
    ranked = rank(s, [hit("studio", "Powerwolf - Stossgebet", 245.0),
                      hit("live", "Powerwolf - Stossgebet (Live at Olympiahalle)", 244.0)],
                  "Wildlive (Live at Olympiahalle)")
    assert [h["ref"] for h in ranked] == ["live"]
