"""A title that differs only as a typo does (DESIGN §9, slice 149; R-517).

Measured over 70 of the user's 177 unmatched albums: an exact `key(core())` match leaves 17 with a
candidate and no fit; **edit distance alone reaches 5 of them**, and every pair it takes is a typo,
a dropped letter, a diacritic or mojibake in the owner's own tags. `contains` would have reached 5
more and taken `Milk - Exclusive Track` for `Milk` and `Maybe - Remix` for
`Maybe (remixed by Dust of Basement)` — different recordings — so it is not here.
"""

from __future__ import annotations

import pytest

from noaap.enrich import core, edits, enrich_release, near_enough
from noaap.models import AlbumPlan, Kind, PlanTrack
from noaap.text import key

CREDIT = [{"name": "A Band", "artist": {"name": "A Band"}}]


def near(a: str, b: str) -> bool:
    return near_enough(key(core(a)), key(core(b)))


# -- the test itself -----------------------------------------------------------------------------


@pytest.mark.parametrize("mine, theirs", [
    ("Dunler Ort", "Dunkler Ort"),              # a dropped letter
    ("Toudion", "Tourdion"),
    ("Scarazula", "Scuarazula"),
    ("Zeit zu gehen", "Zeit zu gehn"),
    ("Hafrue", "Havfrue"),
    ("Na Lama-sa", "Na Lámasa"),                # diacritics the owner's tags lack
    ("Meister der LÃ¼gen", "Meister der Lügen"),  # mojibake in the owner's own tags
    ("Sister Sarah", "Sister Sara"),
    # after `key(core())` these are `justwannabegod` and `ijustwannabegod`: one character, the
    # dropped leading "I". I had this one down as a guess in I-385 and it is not — it is a typo.
    ("Just Wanna Be God", "I Just Wanna Be God"),
])
def test_a_typo_is_the_same_song(mine, theirs):
    assert near(mine, theirs)


@pytest.mark.parametrize("mine, theirs", [
    ("Milk - Exclusive Track", "Milk"),                        # a different recording
    ("Maybe - Remix", "Maybe (remixed by Dust of Basement)"),  # also a different recording
    ("Somewhere", "Somewhere in the Jungle"),
    ("I Am The Sentinel", "The Sentinel"),
    ("One", "One More Time"),
])
def test_a_different_name_is_a_different_song(mine, theirs):
    assert not near(mine, theirs)


def test_one_character_is_always_allowed_however_short():
    assert near("One", "Ones")
    assert near("Go", "Ga")
    assert not near("Go", "Up")


def test_a_short_title_can_afford_only_the_one():
    """A seventh of four characters is nothing, so `Fuor` for `Four` — a transposition, which is two
    changes — is refused. Short names carry no slack and should not."""
    assert not near("Fuor", "Four")
    assert near("Fuur", "Four"), "one character, though"


def test_a_longer_title_may_differ_more():
    """A seventh of the longer, so a long name survives two or three stray characters."""
    assert near("The Drinking Loving Dancers", "The Drinking Loving Dancer")
    assert edits("abc", "xyz") == 3
    assert not near("abc", "xyz")


def test_nothing_is_near_nothing():
    assert not near_enough("", "")
    assert not near_enough("something", "")


# -- in the seating ------------------------------------------------------------------------------


def a_plan(titles):
    return AlbumPlan(source_url="/m", source_id="/m", kind=Kind.OFFICIAL_ALBUM, album="An Album",
                     albumartist="A Band", year=None, cover_url=None, folder="A Band/An Album",
                     provider="folder",
                     tracks=[PlanTrack(video_id=f"/m/{n:02d}.flac", number=n, artist="A Band",
                                       title=t, filename=f"{n:02d}.flac", provenance={},
                                       state="done") for n, t in enumerate(titles, 1)])


def a_release(titles, mbid="rel-1"):
    return {"id": mbid, "title": "An Album", "date": "2001", "country": "DE", "status": "Official",
            "artist-credit": CREDIT, "release-group": {"id": "rg", "first-release-date": "2001"},
            "media": [{"position": 1, "tracks": [
                {"position": n, "title": t, "artist-credit": CREDIT,
                 "recording": {"id": f"rec-{n}"}} for n, t in enumerate(titles, 1)]}]}


class Answers:
    def __init__(self, titles):
        self.titles = titles

    def search_releases(self, artist, album):
        return [{"id": "rel-1", "title": "An Album", "score": 100, "status": "Official",
                 "country": "DE", "date": "2001", "track-count": len(self.titles),
                 "artist-credit": CREDIT}]

    def release(self, mbid):
        return a_release(self.titles, mbid)

    def search_recordings(self, artist, title):
        return []

    def artist(self, name):
        return None

    def artist_albums(self, artist):
        return []


def test_an_album_two_typos_short_now_matches():
    """Five files, two misspelled as the user's really are: 3 of 5 exact is under the bar, 5 is over."""
    plan = a_plan(["Dunler Ort", "Toudion", "Scarazula", "Havfrue", "Tourdion II"])
    mb = Answers(["Dunkler Ort", "Tourdion", "Scuarazula", "Havfrue", "Tourdion II"])

    assert enrich_release(plan, mb) is True
    assert [t.title for t in plan.tracks] == ["Dunkler Ort", "Tourdion", "Scuarazula",
                                              "Havfrue", "Tourdion II"]


def test_an_exact_name_is_seated_before_any_typo():
    """So a near pair can never take a seat an exact name wanted."""
    plan = a_plan(["Dunkler Ort", "Dunler Ort"])
    mb = Answers(["Dunkler Ort", "Dunkler Ort II"])

    enrich_release(plan, mb)
    assert plan.tracks[0].title == "Dunkler Ort", "the exact one kept its seat"


def test_two_files_near_the_same_track_are_both_left_alone():
    """Ambiguity is what looseness costs: `_seats_by_whole_title` exists because `Dreams
    (Deep crowl Mix)` once matched the plain `Dreams` and the two were swapped."""
    plan = a_plan(["Dunler Ort", "Dunkier Ort", "One", "Two", "Three"])
    mb = Answers(["Dunkler Ort", "Nothing Like It", "One", "Two", "Three"])

    enrich_release(plan, mb)

    assert plan.tracks[0].title == "Dunler Ort", "neither typo was seated"
    assert plan.tracks[1].title == "Dunkier Ort"


def test_a_file_near_two_tracks_is_left_alone():
    # `Havfru` is one character from each of `Havfrue` and `Havfrua`
    plan = a_plan(["Havfru", "One", "Two", "Three", "Five"])
    mb = Answers(["Havfrue", "Havfrua", "One", "Two", "Three"])

    enrich_release(plan, mb)

    assert plan.tracks[0].title == "Havfru", "it was near both, so it took neither"
