"""A per-track lookup gives the same answer on every pass (§9, slice 193; q17, R-650).

`Various Artists/Classic Rock Hits` is an adopted compilation with no matched release, so every
deep pass looks its tracks up one by one. Two of them flip-flopped across versions:

* 32 `We're Not Gonna Take It (Single Version)`, with a typographic apostrophe in the file.
  MusicBrainz has no `(Single Version)` of it, so `enrich_track` "kept our title" — by writing MusicBrainz' spelling with our bracket put back.
  1.50.0 met a recording spelled with the typographic one and wrote that; 1.56 to 1.68 meet one
  spelled `'` and want it back.
* 49 `Milk and Alcohol`: the 2026-10-10 apply stored recording 35084d78 (170.9 s), and every pass
  since searched again and offered a031426b (179.9 s), because a fresh read of a folder knows no ids.
"""

from __future__ import annotations

from typing import Any

from noaap.enrich import enrich, enrich_track
from noaap.models import AlbumPlan, Kind, PlanTrack, Provenance

TWISTED = [{"name": "Twisted Sister", "artist": {"name": "Twisted Sister"}}]
FEELGOOD = [{"name": "Dr. Feelgood", "artist": {"name": "Dr. Feelgood"}}]


class Search:
    """MusicBrainz as it answers today: one recording for each search."""

    def __init__(self):
        self.asked: list[str] = []

    def search_recordings(self, artist: str, title: str) -> list[dict[str, Any]]:
        self.asked.append(title)
        if "Gonna" in title:
            return [{"id": "rec-twisted", "title": "We're Not Gonna Take It", "artist-credit": TWISTED,
                     "releases": [], "length": 220000}]
        return [{"id": "a031426b", "title": "Milk and Alcohol", "artist-credit": FEELGOOD,
                 "releases": [], "length": 179900}]

    def search_releases(self, artist, album):
        return []

    def release(self, mbid):
        return {}

    def artist(self, name):
        return None


def a_track(n: int, artist: str, title: str) -> PlanTrack:
    t = PlanTrack(video_id=f"/m/{n:02d}.mp3", number=n, artist=artist, title=title, filename=f"{n:02d}.mp3",
                  provenance={"artist": Provenance.FILE_TAGS, "title": Provenance.FILE_TAGS}, state="done")
    t.auto.update(artist=artist, title=title)
    return t


def classic_rock_hits() -> AlbumPlan:
    """The fresh read of the folder, as the update pass hands it to `enrich`."""
    plan = AlbumPlan(source_url="/m", source_id="/m", kind=Kind.COMPILATION, album="Classic Rock Hits",
                     albumartist="Various Artists", year=None, cover_url=None,
                     folder="Various Artists/Classic Rock Hits", provider="folder",
                     tracks=[a_track(32, "Twisted Sister", "We’re Not Gonna Take It (Single Version)"),
                             a_track(49, "Dr. Feelgood", "Milk and Alcohol")])
    plan.tracks[1].known_recording = "35084d78"   # what the stored plan holds since 2026-10-10
    return plan


def test_a_kept_bracket_keeps_the_file_s_spelling():
    t = classic_rock_hits().tracks[0]

    enrich_track(t, Search())

    assert t.title == "We’re Not Gonna Take It (Single Version)", "not MusicBrainz' apostrophe"
    assert t.provenance["title"] == Provenance.FILE_TAGS
    assert not t.mbid, "and still no recording id: MusicBrainz has no (Single Version) of it"


def test_the_whole_album_twice_says_the_same():
    """Two passes in a row, the shape of the dry that kept offering the same two changes."""
    for _ in range(2):
        plan = classic_rock_hits()
        mb = Search()
        enrich(plan, mb)
        assert plan.tracks[0].title == "We’re Not Gonna Take It (Single Version)"
        assert plan.tracks[1].mbid is None, "the stored recording stands: the merge keeps 35084d78"
        assert plan.tracks[1].mb_length is None
        assert "Milk and Alcohol" not in mb.asked, "a track with a recording is not searched again"


def test_a_track_without_a_recording_is_still_looked_up():
    plan = classic_rock_hits()
    plan.tracks[1].known_recording = None
    enrich(plan, Search())
    assert plan.tracks[1].mbid == "a031426b"
