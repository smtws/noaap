"""The releases a lookup weighed and could not fit (DESIGN §9, slice 145; R-513 item 2).

Measured on the user's library: of the first 517 albums, **168 found no release**, and 111 of those
are a release MusicBrainz *has* — refused over a handful of song names. `Alice Cooper/Dragontown`
is the shape: nine candidates pass the name filter, every one of them the right 12 tracks, and each
is refused because 8 of 12 titles fit where 10 are needed. The pass said `0/0 tracks matched` and
nothing at all about the nine releases it had just weighed, which is what made the user think
identification only works when MusicBrainz has nothing similar.
"""

from __future__ import annotations

from typing import Any

from noaap.enrich import enrich, enrich_release, offered_from, says_offer
from noaap.models import AlbumPlan, Kind, PlanTrack

MINE = ["Triggerman", "Deeper", "Dragontown", "Sex, Death and, Money", "Fantasy Man",
        "Somewhere", "Disgraceland", "Sister Sarah", "Every Woman Has A Name",
        "Just Wanna Be God", "It's Much Too Late", "I Am The Sentinel"]
THEIRS = ["Triggerman", "Deeper", "Dragontown", "Sex, Death and Money", "Fantasy Man",
          "Somewhere in the Jungle", "Disgraceland", "Sister Sara", "Every Woman Has a Name",
          "I Just Wanna Be God", "It’s Much Too Late", "The Sentinel"]
CREDIT = [{"name": "Alice Cooper", "artist": {"name": "Alice Cooper"}}]


def a_plan(titles=MINE):
    return AlbumPlan(
        source_url="/music/Alice Cooper/Dragontown", source_id="/music/Alice Cooper/Dragontown",
        kind=Kind.OFFICIAL_ALBUM, album="Dragontown", albumartist="Alice Cooper", year=2001,
        cover_url=None, folder="Alice Cooper/Dragontown", provider="folder",
        tracks=[PlanTrack(video_id=f"/music/{n:02d}.flac", number=n, artist="Alice Cooper",
                          title=t, filename=f"{n:02d}.flac", provenance={}, state="done")
                for n, t in enumerate(titles, 1)])


def a_release(mbid, titles=THEIRS, date="2001-09-18", country="US"):
    return {"id": mbid, "title": "Dragontown", "date": date, "country": country,
            "artist-credit": CREDIT, "release-group": {"id": "rg-1", "first-release-date": "2001"},
            "media": [{"position": 1, "tracks": [
                {"position": n, "title": t, "artist-credit": CREDIT,
                 "recording": {"id": f"rec-{n}"}} for n, t in enumerate(titles, 1)]}]}


class NineEditions:
    """What MusicBrainz really answers for Dragontown: nine editions, all twelve tracks."""

    def __init__(self, titles=THEIRS, how_many=9):
        self.titles, self.how_many = titles, how_many
        self.opened: list[str] = []

    def search_releases(self, artist, album):
        return [{"id": f"rel-{n}", "title": "Dragontown", "score": 100, "status": "Official",
                 "country": "US", "date": f"20{n:02d}", "track-count": 12,
                 "artist-credit": CREDIT} for n in range(1, self.how_many + 1)]

    def release(self, mbid):
        self.opened.append(mbid)
        return a_release(mbid, self.titles, date=f"20{mbid.split('-')[1]:0>2}")

    def search_recordings(self, artist, title):
        return []

    def artist(self, name):
        return None

    def artist_albums(self, artist):
        return []


# -- what gets recorded --------------------------------------------------------------------------


def test_every_candidate_is_listed_not_only_the_ones_opened():
    """The search's own answer carries each candidate's shape, so all nine are listed for the cost
    of the three the pass opens."""
    plan, mb = a_plan(), NineEditions()

    assert enrich_release(plan, mb) is False
    assert len(mb.opened) == 3, "still only three requests"
    assert len(plan.offered) == 9, [o["id"] for o in plan.offered]


def test_the_ones_opened_say_how_near_they_came():
    plan, mb = a_plan(), NineEditions()
    enrich_release(plan, mb)

    opened = [o for o in plan.offered if o["matched"] is not None]
    assert len(opened) == 3
    assert all(o["matched"] == 8 and o["needed"] == 10 for o in opened), opened


def test_the_nearest_is_listed_first():
    plan = a_plan()
    mb = NineEditions()
    enrich_release(plan, mb)

    assert plan.offered[0]["matched"] == 8, "the ones that were weighed come before the rest"
    assert plan.offered[-1]["matched"] is None


def test_a_release_that_fits_leaves_nothing_to_choose():
    plan, mb = a_plan(), NineEditions(titles=MINE)      # the archive agrees with the folder

    assert enrich_release(plan, mb) is True
    assert plan.offered == [], "something fitted, so there is nothing for a person to decide"


def test_a_lookup_that_fits_clears_an_older_list():
    plan = a_plan()
    plan.offered = [offered_from({"id": "rel-old", "title": "Dragontown"})]

    assert enrich_release(plan, NineEditions(titles=MINE)) is True
    assert plan.offered == []


def test_nothing_found_at_all_is_an_empty_list_not_a_lie():
    class Nothing(NineEditions):
        def search_releases(self, artist, album):
            return []

    plan = a_plan()
    assert enrich_release(plan, Nothing()) is False
    assert plan.offered == []


# -- how it is said ------------------------------------------------------------------------------


def test_the_line_says_the_shape_and_the_fit():
    one = offered_from({"id": "rel-2", "title": "Dragontown", "date": "2001", "country": "DE",
                        "track-count": 12}, a_release("rel-2"), matched=8, needed=10)

    assert says_offer(one) == ("  rel-2 'Dragontown' (2001, DE) 12 track(s) — "
                               "8 of 10 titles fitted")


def test_a_candidate_never_opened_says_so():
    one = offered_from({"id": "rel-9", "title": "Dragontown", "date": "", "country": "",
                        "track-count": 12})
    assert "not opened" in says_offer(one)
    assert "no date" in says_offer(one)


def test_a_multi_disc_candidate_says_its_media():
    rel = a_release("rel-x")
    rel["media"].append({"position": 2, "tracks": [{"position": 1, "title": "Live",
                                                    "artist-credit": CREDIT,
                                                    "recording": {"id": "r"}}]})
    one = offered_from({"id": "rel-x", "title": "Requiembryo"}, rel, matched=3, needed=10)
    assert "12/1 track(s)" in says_offer(one), says_offer(one)


def test_the_pass_says_the_number_then_the_list():
    said: list[str] = []
    plan = a_plan()

    enrich(plan, NineEditions(), progress=said.append)

    headline = next(i for i, line in enumerate(said) if "release(s) weighed, none fitted" in line)
    assert "9 release(s)" in said[headline]
    assert "pin one" in said[headline]
    assert sum(1 for line in said[headline + 1:] if "titles fitted" in line) == 3


def test_the_dry_run_diff_says_it_too():
    from noaap.plan import changes_from

    was = a_plan()
    now = a_plan()
    now.offered = [offered_from({"id": f"rel-{n}", "title": "Dragontown"}) for n in range(1, 10)]

    assert changes_from(was, now) == ["9 release(s) weighed, none fitted — one of them can be pinned"]
