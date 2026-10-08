"""The releases a lookup weighed and could not fit (DESIGN §9, slice 145; R-513 item 2).

Measured on the user's library: of the first 517 albums, 177 found no release, and many of those are
a release MusicBrainz *has* — refused over a handful of song names. The pass said `0/0 tracks
matched` and nothing at all about the releases it had just weighed, which is what made the user
think identification only works when MusicBrainz has nothing similar.

`Alice Cooper/Dragontown` used to be the shape: nine candidates, every one the right 12 tracks, each
refused because 8 of 12 titles fit where 10 are needed. **Since slice 149 it fits** — `Sister Sarah`/`Sister
Sara` and `Just Wanna Be God`/`I Just Wanna Be God` are each one edit, which takes it to
exactly 10 — so it is kept here as the case that leaves nothing to decide.

What still refuses, and so still needs the list, is `Depeche Mode/Music For The Masses`: 12 editions,
3 opened, 11 of 12 titles fitted. The three that miss are a medley (`Pimpf` against `Pimpf /
Interlude #1: Mission Impossible`) and two mixes the folder writes with a dash where MusicBrainz
uses brackets (`… - Aggro Mix` against `… (Aggro mix)`) — far past any edit bound, and none of them
something a distance can rescue.
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


def credit_for(name):
    return [{"name": name, "artist": {"name": name}}]


# Music For The Masses, as the folder has it and as MusicBrainz has it (the real pair of lists).
MASSES = ["Never Let Me Down Again", "The Things You Said", "Strangelove", "Sacred", "Little 15",
          "Behind The Wheel", "I Want You Now", "To Have And To Hold", "Nothing", "Pimpf",
          "Agent Orange", "Never Let Me Down Again - Aggro Mix",
          "To Have And To Hold - Spanish Taster", "Pleasure, Little Treasure"]
MASSES_THEIRS = ["Never Let Me Down Again", "The Things You Said", "Strangelove", "Sacred",
                 "Little 15", "Behind the Wheel", "I Want You Now", "To Have and to Hold",
                 "Nothing", "Pimpf / Interlude #1: Mission Impossible", "Agent Orange",
                 "Never Let Me Down Again (Aggro mix)", "To Have and to Hold (Spanish Taster)",
                 "Pleasure, Little Treasure (Glitter mix)"]


def a_plan(titles=MINE, album="Dragontown", artist="Alice Cooper", year=2001):
    return AlbumPlan(
        source_url=f"/music/{artist}/{album}", source_id=f"/music/{artist}/{album}",
        kind=Kind.OFFICIAL_ALBUM, album=album, albumartist=artist, year=year,
        cover_url=None, folder=f"{artist}/{album}", provider="folder",
        tracks=[PlanTrack(video_id=f"/music/{n:02d}.flac", number=n, artist=artist,
                          title=t, filename=f"{n:02d}.flac", provenance={}, state="done")
                for n, t in enumerate(titles, 1)])


def a_release(mbid, titles=THEIRS, date="2001-09-18", country="US", title="Dragontown",
              credit=CREDIT):
    return {"id": mbid, "title": title, "date": date, "country": country,
            "artist-credit": credit, "release-group": {"id": "rg-1", "first-release-date": "2001"},
            "media": [{"position": 1, "tracks": [
                {"position": n, "title": t, "artist-credit": credit,
                 "recording": {"id": f"rec-{n}"}} for n, t in enumerate(titles, 1)]}]}


class Editions:
    """What MusicBrainz really answers for one of these albums: N editions, all the same tracks."""

    def __init__(self, titles=THEIRS, how_many=9, album="Dragontown", artist="Alice Cooper"):
        self.titles, self.how_many, self.album = titles, how_many, album
        self.credit = credit_for(artist)
        self.opened: list[str] = []

    def search_releases(self, artist, album):
        return [{"id": f"rel-{n}", "title": self.album, "score": 100, "status": "Official",
                 "country": "US", "date": f"20{n:02d}", "track-count": len(self.titles),
                 "artist-credit": self.credit} for n in range(1, self.how_many + 1)]

    def release(self, mbid):
        self.opened.append(mbid)
        return a_release(mbid, self.titles, date=f"20{mbid.split('-')[1]:0>2}",
                         title=self.album, credit=self.credit)

    def search_recordings(self, artist, title):
        return []

    def artist(self, name):
        return None

    def artist_albums(self, artist):
        return []


# -- what gets recorded --------------------------------------------------------------------------


def masses():
    """The real still-refusing case: twelve editions, eleven of twelve titles fitted."""
    return (a_plan(MASSES, album="Music For The Masses", artist="Depeche Mode", year=1987),
            Editions(MASSES_THEIRS, how_many=12, album="Music For The Masses",
                         artist="Depeche Mode"))


def test_every_candidate_is_listed_not_only_the_ones_opened():
    """The search's own answer carries each candidate's shape, so all twelve are listed for the cost
    of the three the pass opens."""
    plan, mb = masses()

    assert enrich_release(plan, mb) is False
    assert len(mb.opened) == 3, "still only three requests"
    assert len(plan.offered) == 12, [o["id"] for o in plan.offered]


def test_the_ones_opened_say_how_near_they_came():
    plan, mb = masses()
    enrich_release(plan, mb)

    opened = [o for o in plan.offered if o["matched"] is not None]
    assert len(opened) == 3
    assert all(o["matched"] == 11 and o["needed"] == 12 for o in opened), opened


def test_the_nearest_is_listed_first():
    plan, mb = masses()
    enrich_release(plan, mb)

    assert plan.offered[0]["matched"] == 11, "the ones that were weighed come before the rest"
    assert plan.offered[-1]["matched"] is None


def test_a_release_that_fits_leaves_nothing_to_choose():
    plan, mb = a_plan(), Editions()     # Dragontown, which fits since slice 149

    assert enrich_release(plan, mb) is True
    assert plan.offered == [], "something fitted, so there is nothing for a person to decide"


def test_a_lookup_that_fits_clears_an_older_list():
    plan = a_plan()
    plan.offered = [offered_from({"id": "rel-old", "title": "Dragontown"})]

    assert enrich_release(plan, Editions(titles=MINE)) is True
    assert plan.offered == []


def test_nothing_found_at_all_is_an_empty_list_not_a_lie():
    class Nothing(Editions):
        def search_releases(self, artist, album):
            return []

    plan = a_plan(MASSES, album="Music For The Masses", artist="Depeche Mode")
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
    plan, mb = masses()

    enrich(plan, mb, progress=said.append)

    headline = next(i for i, line in enumerate(said) if "release(s) weighed, none fitted" in line)
    assert "12 release(s)" in said[headline]
    assert "pin one" in said[headline]
    assert sum(1 for line in said[headline + 1:] if "titles fitted" in line) == 3


def test_the_releases_it_weighed_are_not_a_change_to_apply():
    """They were a line in the diff until R-530, so that a check mentioned them at all. Then
    `merge_plans` stopped dropping them (slice 158) and an album where *nothing* fitted reported
    "1 change(s)" and offered an apply that writes nothing to anybody's files. They are a question
    for the person, and they reach the panel as the outcome's `offered`."""
    from noaap.plan import changes_from

    was = a_plan()
    now = a_plan()
    now.offered = [offered_from({"id": f"rel-{n}", "title": "Dragontown"}) for n in range(1, 10)]

    assert changes_from(was, now) == [], "nothing here would be written to a file"
