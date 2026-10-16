"""Of several releases that match, the one the files are by their lengths (§9, slice 212; q35, R-746).

`Limp Bizkit — Chocolate Starfish and the Hot Dog Flavored Water`, the 2000 playlist: the 2024 clean
edition matched all 15 titles (it alone spells `Getcha Groove On (Dirt Road mix)`), the 2000 pressing
14, and the pass pinned the clean one, whose `The One` is 222 s against the 343-s file. `Gold Cobra`:
the year-only worldwide clean edition beat the dated European one on the string `2011` < `2011-06-24`.
"""

from __future__ import annotations

from test_release_rank import CREDIT, Editions, a_plan

from noaap.enrich import enrich_release, release_rank

TITLES = ["Intro", "Hot Dog", "The One", "Getcha Groove On (Dirt Road Mix)", "Hold On"]
FILES = [79, 231, 344, 260, 348]


def edition(mbid, titles, lengths, date, disambiguation="", country="XE"):
    return {"id": mbid, "title": "An Album", "date": date, "country": country, "status": "Official",
            "disambiguation": disambiguation, "artist-credit": CREDIT,
            "release-group": {"id": "rg", "first-release-date": "2000"},
            "media": [{"position": 1, "tracks": [
                {"position": n, "title": t, "artist-credit": CREDIT,
                 **({"length": int(sec * 1000)} if sec else {}),
                 "recording": {"id": f"rec-{mbid}-{n}"}} for n, (t, sec) in enumerate(zip(titles, lengths, strict=True), 1)]}]}


class Disambiguated(Editions):
    def search_releases(self, artist, album):
        found = super().search_releases(artist, album)
        for one in found:
            one["disambiguation"] = self.editions[one["id"]]["disambiguation"]
        return found


def the_playlist(lengths=FILES):
    plan = a_plan(TITLES)
    for t, sec in zip(plan.tracks, lengths, strict=True):
        t.duration = sec
    return plan


CLEAN_2024 = edition("clean", [*TITLES[:3], "Getcha Groove On (Dirt Road mix)", "Hold On"],
                     [78.8, 230.3, 222.4, 268.7, 345.8], "2024-11-15", "clean", "XW")
FIRST_2000 = edition("first", [*TITLES[:3], "Getcha Groove On", "Hold On"],
                     [78.8, 230.2, 343.4, 269.5, 347.6], "2000-10-16")


def test_the_release_the_files_are_beats_one_that_matches_more_titles():
    plan = the_playlist()
    assert enrich_release(plan, Disambiguated(CLEAN_2024, FIRST_2000)) is True
    assert plan.mbid == "first", "The One is 344 s, as the 2000 pressing has it"


def test_without_lengths_the_titles_decide_as_before():
    plan = the_playlist([None] * 5)
    assert enrich_release(plan, Disambiguated(CLEAN_2024, FIRST_2000)) is True
    assert plan.mbid == "clean"


def test_a_release_with_no_lengths_leaves_the_order_as_before():
    silent = edition("first", [t["title"] for t in FIRST_2000["media"][0]["tracks"]], [None] * 5, "2000-10-16")
    plan = the_playlist()
    assert enrich_release(plan, Disambiguated(CLEAN_2024, silent)) is True
    assert plan.mbid == "clean"


def test_files_cut_elsewhere_than_every_release_leave_the_order_as_before():
    """`Mono Inc. — Symphonic Live`: the files are cut where no release cuts them (slice 198)."""
    plan = the_playlist([500, 500, 500, 500, 500])
    assert enrich_release(plan, Disambiguated(CLEAN_2024, FIRST_2000)) is True
    assert plan.mbid == "clean"


def test_the_pick_says_when_most_lengths_do_not_fit():
    plan = the_playlist([500, 500, 500, 500, 500])
    enrich_release(plan, Disambiguated(CLEAN_2024))
    assert "MusicBrainz: release clean — 0 of 5 lengths fit the files" in plan.seat_notes


def test_a_tie_goes_to_the_dated_edition_not_the_clean_year_only_one():
    gold = edition("gold-xw", TITLES, FILES, "2011", "clean", "XW")
    explicit = edition("gold-xe", TITLES, FILES, "2011-06-24")
    plan = the_playlist()
    assert enrich_release(plan, Disambiguated(gold, explicit)) is True
    assert plan.mbid == "gold-xe"


def test_a_full_date_goes_before_its_bare_year():
    rel = edition("x", TITLES, FILES, "2011")
    bare = release_rank({"id": "a", "status": "Official", "date": "2011"}, rel, {0: {}})
    full = release_rank({"id": "b", "status": "Official", "date": "2011-06-24"}, rel, {0: {}})
    assert full < bare
