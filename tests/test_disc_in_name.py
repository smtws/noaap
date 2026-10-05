"""A folder that is one disc of a set (DESIGN §9, slice 146; R-513 item 3).

39 of the first 517 albums of the user's library are one disc kept as its own folder:
`Requiembryo (CD 1)`, `Horror Vacui (CD1)`, `The Better Life [Deluxe Edition] Disc 1`. No release is
called that, so the search either returns nothing or returns the whole set, whose track count the
filter then rejects — `Requiembryo (CD 1)`: 6 releases returned, one with a matching core title,
**0 passed**, because its 28 tracks are the 2-CD set against 13 files.
"""

from __future__ import annotations

import pytest

from noaap.enrich import disc_in_name, enrich_release, match_release_tracks, media_of
from noaap.models import AlbumPlan, Kind, PlanTrack

CREDIT = [{"name": "ASP", "artist": {"name": "ASP"}}]
ONE = [f"Song {n}" for n in range(1, 14)]          # 13 tracks, like the user's CD 1
TWO = [f"Live {n}" for n in range(1, 16)]          # 15, like the user's CD 2


def a_plan(titles, album="Requiembryo (CD 1)"):
    return AlbumPlan(source_url="/m", source_id="/m", kind=Kind.OFFICIAL_ALBUM, album=album,
                     albumartist="ASP", year=None, cover_url=None, folder=f"ASP/{album}",
                     provider="folder",
                     tracks=[PlanTrack(video_id=f"/m/{n:02d}.flac", number=n, artist="ASP",
                                       title=t, filename=f"{n:02d}.flac", provenance={},
                                       state="done") for n, t in enumerate(titles, 1)])


def two_discs(mbid="rel-1"):
    return {"id": mbid, "title": "Requiembryo", "date": "2007", "country": "DE",
            "artist-credit": CREDIT, "release-group": {"id": "rg", "first-release-date": "2007"},
            "media": [{"position": d, "tracks": [
                {"position": n, "title": t, "artist-credit": CREDIT,
                 "recording": {"id": f"rec-{d}-{n}"}} for n, t in enumerate(titles, 1)]}
                for d, titles in enumerate((ONE, TWO), 1)]}


class Whole:
    """MusicBrainz, which knows the set and nothing called `… (CD 1)`."""

    def __init__(self):
        self.asked: list[str] = []

    def search_releases(self, artist, album):
        self.asked.append(album)
        if album != "Requiembryo":
            return []
        return [{"id": "rel-1", "title": "Requiembryo", "score": 100, "status": "Official",
                 "country": "DE", "date": "2007", "track-count": 28, "artist-credit": CREDIT}]

    def release(self, mbid):
        return two_discs(mbid)

    def search_recordings(self, artist, title):
        return []

    def artist(self, name):
        return None

    def artist_albums(self, artist):
        return []


# -- reading the marker --------------------------------------------------------------------------


@pytest.mark.parametrize("name, want", [
    ("Requiembryo (CD 1)", ("Requiembryo", 1)),
    ("Horror Vacui (CD1)", ("Horror Vacui", 1)),
    ("The Better Life [Deluxe Edition] Disc 1", ("The Better Life [Deluxe Edition]", 1)),
    ("Interim Works Compendium (CD 2)", ("Interim Works Compendium", 2)),
    ("An Album, Part 2", ("An Album", 2)),
    ("An Album Teil 3", ("An Album", 3)),
    ("An Album disk 2", ("An Album", 2)),
])
def test_a_marker_at_the_end_is_read(name, want):
    assert disc_in_name(name) == want


@pytest.mark.parametrize("name", [
    "Dragontown",
    "Disintegration",
    "Requiembryo: Der schwarze Schmetterling, Teil V",   # a roman numeral is somebody's title
    "Vol. 1 - Heavy Sleeping",                           # not at the end: it is the album's name
    "Zaubererbruder , Der Krabat-Liederzyklus 1",         # a bare number is far too little to go on
    "CD 1",                                              # the whole name: it names nothing to ask for
    "Part 2",
    "",
])
def test_what_is_not_a_marker_is_left_alone(name):
    assert disc_in_name(name) is None


# -- what it buys --------------------------------------------------------------------------------


def test_a_bracketed_marker_was_never_the_name_but_the_count():
    """`core` already strips `(CD 1)`, so the search always asked the right question — and the
    answer was thrown away by the track-count test, 28 against 13. The second ask is the same text;
    what it buys is dropping that test and letting one medium answer."""
    plan, mb = a_plan(ONE), Whole()

    assert enrich_release(plan, mb) is True
    assert mb.asked == ["Requiembryo", "Requiembryo"], mb.asked
    assert plan.mbid == "rel-1"
    assert {t.disc for t in plan.tracks} == {1}
    assert [t.number for t in plan.tracks] == list(range(1, 14))


def test_an_unbracketed_marker_really_changes_the_question():
    """`core` keeps `Disc 1`, so the first ask is for a release nobody has."""
    plan, mb = a_plan(ONE, album="Requiembryo Disc 1"), Whole()

    assert enrich_release(plan, mb) is True
    assert mb.asked == ["Requiembryo Disc 1", "Requiembryo"], mb.asked
    assert {t.disc for t in plan.tracks} == {1}


def test_the_second_disc_takes_its_own_numbers():
    plan, mb = a_plan(TWO, album="Requiembryo (CD 2)"), Whole()

    assert enrich_release(plan, mb) is True
    assert {t.disc for t in plan.tracks} == {2}
    assert [t.number for t in plan.tracks] == list(range(1, 16))


def test_a_marker_that_names_the_wrong_disc_still_finds_the_right_one():
    """A marker somebody typed is not evidence enough to refuse the album."""
    plan, mb = a_plan(TWO, album="Requiembryo (CD 1)"), Whole()

    assert enrich_release(plan, mb) is True
    assert {t.disc for t in plan.tracks} == {2}, "the medium that fits, not the one named"


def test_part_of_a_disc_is_refused_and_listed():
    """`ASP/Requiembryo (CD 2)` is really seven files that are the *tail* of a 15-track medium.
    Seating them as six tracks of disc 2 and one of disc 1, renumbered, is worse than refusing."""
    plan, mb = a_plan(TWO[8:], album="Requiembryo (CD 2)"), Whole()

    assert enrich_release(plan, mb) is False
    assert plan.mbid is None
    assert {t.disc for t in plan.tracks} == {1}, "nothing was moved"
    assert plan.offered and plan.offered[0]["media"] == [13, 15]


def test_an_album_with_no_marker_is_not_asked_twice():
    plan, mb = a_plan(ONE, album="Requiembryo"), Whole()

    enrich_release(plan, mb)

    assert mb.asked == ["Requiembryo"], "one search, as before"


# -- the medium helper ---------------------------------------------------------------------------


def test_each_medium_carries_its_own_disc_number():
    media = media_of(two_discs())
    assert [one[0]["disc"] for one in media] == [1, 2]
    assert [len(one) for one in media] == [13, 15]


def test_the_named_medium_comes_first_and_the_rest_behind_it():
    media = media_of(two_discs(), only=2)
    assert [one[0]["disc"] for one in media] == [2, 1]


def test_without_a_medium_the_whole_release_is_one_list():
    got = match_release_tracks(a_plan(ONE + TWO, album="Requiembryo"), two_discs())
    assert got is not None and len(got) == 28
