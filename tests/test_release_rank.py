"""Which of several fitting releases an album is (DESIGN §9, slice 147; R-513 item 4).

`enrich_release` took the **first** candidate that fitted, so the choice was whatever order the
search happened to return — an unofficial pressing or a 2021 reissue could stand in front of the
release the album actually is. Measured on the user's library: `Alice Cooper/Dragontown` has nine
editions, `Amon Amarth/Fate Of Norns` eight, `Böhse Onkelz/E.I.N.S` four.
"""

from __future__ import annotations

from typing import Any

from noaap.enrich import enrich_release, release_rank
from noaap.models import AlbumPlan, Kind, PlanTrack

TITLES = ["One", "Two", "Three", "Four", "Five"]
CREDIT = [{"name": "A Band", "artist": {"name": "A Band"}}]


def a_plan(titles=TITLES):
    return AlbumPlan(source_url="/m", source_id="/m", kind=Kind.OFFICIAL_ALBUM, album="An Album",
                     albumartist="A Band", year=None, cover_url=None, folder="A Band/An Album",
                     provider="folder",
                     tracks=[PlanTrack(video_id=f"/m/{n:02d}.flac", number=n, artist="A Band",
                                       title=t, filename=f"{n:02d}.flac", provenance={},
                                       state="done") for n, t in enumerate(titles, 1)])


def a_release(mbid, titles=TITLES, date="2001", status="Official"):
    return {"id": mbid, "title": "An Album", "date": date, "country": "DE", "status": status,
            "artist-credit": CREDIT, "release-group": {"id": "rg", "first-release-date": date},
            "media": [{"position": 1, "tracks": [
                {"position": n, "title": t, "artist-credit": CREDIT,
                 "recording": {"id": f"rec-{mbid}-{n}"}} for n, t in enumerate(titles, 1)]}]}


class Editions:
    """Several editions of one album, answered in whatever order the search likes."""

    def __init__(self, *editions: dict[str, Any]):
        self.editions = {e["id"]: e for e in editions}
        self.opened: list[str] = []

    def search_releases(self, artist, album):
        return [{"id": e["id"], "title": e["title"], "score": 100, "status": e["status"],
                 "country": e["country"], "date": e["date"],
                 "track-count": sum(len(m["tracks"]) for m in e["media"]),
                 "artist-credit": CREDIT} for e in self.editions.values()]

    def release(self, mbid):
        self.opened.append(mbid)
        return self.editions[mbid]

    def search_recordings(self, artist, title):
        return []

    def artist(self, name):
        return None

    def artist_albums(self, artist):
        return []


# -- the ranking itself --------------------------------------------------------------------------


def test_the_most_titles_matched_comes_first():
    cand = {"id": "a", "status": "Official", "date": "2001"}
    rel = a_release("a")
    many, few = {0: {}, 1: {}, 2: {}}, {0: {}}
    assert release_rank(cand, rel, many) < release_rank(cand, rel, few)


def test_official_beats_a_promo_at_the_same_fit():
    rel = a_release("a")
    one = release_rank({"id": "a", "status": "Official", "date": "2001"}, rel, {0: {}})
    two = release_rank({"id": "b", "status": "Promotion", "date": "2001"}, rel, {0: {}})
    assert one < two


def test_the_earliest_date_breaks_a_tie():
    rel = a_release("a")
    first = release_rank({"id": "a", "status": "Official", "date": "1999"}, rel, {0: {}})
    later = release_rank({"id": "b", "status": "Official", "date": "2021"}, rel, {0: {}})
    assert first < later


def test_a_release_with_no_date_sorts_last_not_first():
    rel = a_release("a")
    dated = release_rank({"id": "a", "status": "Official", "date": "2001"}, rel, {0: {}})
    undated = release_rank({"id": "b", "status": "Official", "date": ""}, rel, {0: {}})
    assert dated < undated


def test_the_id_settles_what_is_otherwise_equal():
    """So the answer does not depend on the order a search happened to return."""
    rel = a_release("a")
    one = release_rank({"id": "aaa", "status": "Official", "date": "2001"}, rel, {0: {}})
    two = release_rank({"id": "bbb", "status": "Official", "date": "2001"}, rel, {0: {}})
    assert one < two


def test_the_release_answers_where_the_candidate_is_silent():
    """A search result carries status and date; a pinned candidate is only an id."""
    rel = a_release("a", date="1998", status="Official")
    assert release_rank({"id": "a"}, rel, {0: {}})[1] is False, "official, from the release"
    assert release_rank({"id": "a"}, rel, {0: {}})[2] == "1998"


# -- what the pass does with it ------------------------------------------------------------------


def test_an_equal_fit_goes_to_the_earliest_pressing():
    """The search returns the 2021 reissue first and the album is the 2001 pressing.

    `release_candidates` already sorts by date, so first-fit would have answered this one too — the
    case is here to hold the ranking in agreement with that sort, not to prove it. What only the
    ranking can do is the next case: weigh how well each release actually fits, which the sort cannot
    know because it has opened none of them.
    """
    mb = Editions(a_release("reissue", date="2021"), a_release("first", date="2001"))
    plan = a_plan()

    assert enrich_release(plan, mb) is True
    assert plan.mbid == "first"
    assert len(mb.opened) == 2, "both were weighed"


def test_a_better_fit_beats_an_earlier_date():
    """Date only decides a tie: a release that matches more of the album wins outright."""
    nearly = a_release("old", titles=["One", "Two", "Three", "Four", "Nope"], date="1990")
    whole = a_release("new", date="2010")
    plan = a_plan()

    assert enrich_release(plan, Editions(nearly, whole)) is True
    assert plan.mbid == "new"


def test_an_unofficial_edition_loses_to_an_official_one():
    boot = a_release("boot", date="1999", status="Bootleg")
    real = a_release("real", date="2001", status="Official")

    plan = a_plan()
    assert enrich_release(plan, Editions(boot, real)) is True
    assert plan.mbid == "real"


def test_a_pinned_release_is_not_ranked_against_anything():
    mb = Editions(a_release("first", date="2001"), a_release("mine", date="2021"))
    plan = a_plan()
    plan.mbid, plan.provenance["mbid"] = "mine", "user"

    assert enrich_release(plan, mb) is True
    assert plan.mbid == "mine", "theirs, not the best of several"
    assert mb.opened == ["mine"]


def test_only_three_are_opened_however_many_there_are():
    """The budget is unchanged: the best **of the ones it can afford to open**, and the rest are
    listed for a person (slice 145) rather than fetched."""
    many = [a_release(f"rel-{n}", date=f"20{n:02d}") for n in range(1, 9)]
    mb = Editions(*many)
    plan = a_plan()

    assert enrich_release(plan, mb) is True
    assert len(mb.opened) == 3, mb.opened
    assert plan.mbid == "rel-1", "the earliest of the three that were opened"
