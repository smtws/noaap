"""The release a person says this album is (DESIGN §9, slice 137; R-489).

`mbid` was the one album field no edit could hold: `merge_plans` took the fresh value
unconditionally and nothing ever wrote a provenance for it, so a hand-set release was gone at the
next update. The user's own case is `DOMINUM — Night is Calling`, a rip of **disc 1 of a 2-CD
release**: the search can only ever answer with the 13-track single-disc edition, because a release
that is half present is exactly what a search is built to reject.
"""

from __future__ import annotations

import copy

import pytest

from noaap.enrich import enrich, enrich_release, match_release_tracks, pinned_release
from noaap.models import AlbumPlan, Kind, PlanTrack, Provenance
from noaap.plan import merge_plans
from noaap.service import apply_user_edits, release_id_in, reset_field

TWO_DISCS = "84dfc64c-5abb-4992-872f-d7e2f668c2b1"
ONE_DISC = "feeb0508-2df2-4dcb-a613-7a84a6f5ef6d"
CREDIT = [{"name": "DOMINUM", "artist": {"name": "DOMINUM"}}]


def a_track(n, title):
    return PlanTrack(video_id=f"/music/{n:02d} {title}.flac", number=n, artist="DOMINUM",
                     title=title, filename=f"{n:02d} {title}.flac", provenance={}, state="done")


def a_plan(titles, **kw):
    return AlbumPlan(source_url="/music", source_id="/music", kind=Kind.OFFICIAL_ALBUM,
                     album="Night is Calling", albumartist="DOMINUM", year=None, cover_url=None,
                     folder="DOMINUM/Night is Calling",
                     tracks=[a_track(n, t) for n, t in enumerate(titles, 1)],
                     provider="folder", **kw)


FIRST = [f"Song {n}" for n in range(1, 14)]
SECOND = [f"Live {n}" for n in range(1, 14)]


def a_release(mbid, media, title="Night is Calling"):
    return {
        "id": mbid, "title": title, "date": "2026-07-03", "artist-credit": CREDIT,
        "release-group": {"id": "rg", "first-release-date": "2026-07-01"},
        "media": [{"position": n, "tracks": [
            {"position": i, "title": t, "artist-credit": CREDIT, "length": 200000,
             "recording": {"id": f"rec-{n}-{i}", "length": 200000}}
            for i, t in enumerate(one, 1)]} for n, one in enumerate(media, 1)],
    }


class StubMB:
    """Answers for exactly the two releases, and counts what was asked."""

    def __init__(self):
        self.searched = 0
        self.opened: list[str] = []
        self.releases = {TWO_DISCS: a_release(TWO_DISCS, [FIRST, SECOND]),
                         ONE_DISC: a_release(ONE_DISC, [FIRST])}

    def search_releases(self, artist, album):
        self.searched += 1
        return [{"id": ONE_DISC, "title": "Night is Calling", "score": 100,
                 "track-count": 13, "status": "Official", "country": "XW",
                 "artist-credit": CREDIT, "date": "2026-07-03"}]

    def release(self, mbid):
        self.opened.append(mbid)
        return self.releases.get(mbid)

    def search_recordings(self, artist, title):
        return []

    def artist(self, name):
        return None     # this double answers about releases, not about artists (§9, slice 138)

    def artist_albums(self, artist):
        return []


# -- what a pin is ------------------------------------------------------------------------------


def test_without_a_pin_the_search_decides():
    plan, mb = a_plan(FIRST), StubMB()

    assert pinned_release(plan) is None
    assert enrich_release(plan, mb) is True

    assert mb.searched == 1 and mb.opened == [ONE_DISC]
    assert plan.mbid == ONE_DISC
    assert "mbid" not in plan.provenance, "a search leaves no pin behind"


def test_a_pinned_release_is_opened_and_never_searched_for():
    plan, mb = a_plan(FIRST), StubMB()
    plan.mbid, plan.provenance["mbid"] = TWO_DISCS, Provenance.USER

    assert pinned_release(plan) == TWO_DISCS
    assert enrich_release(plan, mb) is True

    assert mb.searched == 0, "a pin is not a guess, so nothing is searched for"
    assert mb.opened == [TWO_DISCS]
    assert plan.mbid == TWO_DISCS


def test_one_disc_of_two_answers_for_a_pinned_release_only():
    """13 files against 13 + 13. The release-side test exists to stop a search pairing an album with
    a release twice its size by accident; a person who named the release has made no accident."""
    plan = a_plan(FIRST)
    both = a_release(TWO_DISCS, [FIRST, SECOND])

    assert match_release_tracks(plan, both) is None, "a search refuses it, and should"
    matched = match_release_tracks(plan, both, pinned=True)
    assert matched is not None and len(matched) == 13
    assert {m["disc"] for m in matched.values()} == {1}, "disc 1, and disc 2 is simply not here"


def test_the_pinned_release_numbers_the_tracks_it_does_cover():
    plan, mb = a_plan(FIRST), StubMB()
    plan.mbid, plan.provenance["mbid"] = TWO_DISCS, Provenance.USER

    enrich_release(plan, mb)

    assert [t.number for t in plan.tracks] == list(range(1, 14))
    assert {t.disc for t in plan.tracks} == {1}
    assert all(t.mbid for t in plan.tracks)


def test_a_pin_that_does_not_answer_changes_nothing_and_says_so():
    plan, mb = a_plan(["Something else entirely"]), StubMB()
    plan.mbid, plan.provenance["mbid"] = TWO_DISCS, Provenance.USER
    said: list[str] = []

    stats = enrich(plan, mb, progress=said.append)

    assert stats["release"] == 0
    assert plan.year is None, "nothing was taken from it"
    assert [line for line in said if "release pinned by you" in line], said
    assert [line for line in said if "does not answer for this album" in line], said


# -- a pin outlives an update --------------------------------------------------------------------


def test_merge_keeps_a_pinned_release():
    existing = a_plan(FIRST)
    existing.mbid, existing.provenance["mbid"] = TWO_DISCS, Provenance.USER
    fresh = copy.deepcopy(existing)
    fresh.mbid, fresh.provenance = ONE_DISC, {}

    merged = merge_plans(existing, fresh)

    assert merged.mbid == TWO_DISCS
    assert merged.provenance["mbid"] == Provenance.USER


def test_merge_takes_the_fresh_release_when_nothing_is_pinned():
    existing = a_plan(FIRST)
    existing.mbid = TWO_DISCS          # whatever a past search found
    fresh = copy.deepcopy(existing)
    fresh.mbid = ONE_DISC

    assert merge_plans(existing, fresh).mbid == ONE_DISC


# -- how a person sets it ------------------------------------------------------------------------


@pytest.mark.parametrize("said, want", [
    (TWO_DISCS, TWO_DISCS),
    (f"https://musicbrainz.org/release/{TWO_DISCS}", TWO_DISCS),
    (f"  https://musicbrainz.org/release/{TWO_DISCS}/cover-art  ", TWO_DISCS),
    ("https://musicbrainz.org/release-group/8e91799a-2924-4a35-8760-03bd8cda12f1", None),
    ("84dfc64c", None),
    ("not an id", None),
    ("", None),
])
def test_what_counts_as_a_release_id(said, want):
    """A link is what a person has in hand, so the id is taken out of one — and a release group is a
    different thing with no media, which nothing could be matched against."""
    assert release_id_in(said) == want


def test_an_edit_pins_it_and_an_empty_field_hands_it_back():
    plan = a_plan(FIRST)
    plan.mbid = ONE_DISC

    apply_user_edits(plan, {"mbid": f"https://musicbrainz.org/release/{TWO_DISCS}"})

    assert plan.mbid == TWO_DISCS and plan.provenance["mbid"] == Provenance.USER

    apply_user_edits(plan, {"mbid": ""})

    assert plan.mbid is None and "mbid" not in plan.provenance, "back to searching"


def test_an_edit_that_is_not_a_release_id_changes_nothing():
    plan = a_plan(FIRST)
    plan.mbid, plan.provenance["mbid"] = TWO_DISCS, Provenance.USER

    apply_user_edits(plan, {"mbid": "https://musicbrainz.org/release-group/8e91799a-2924-4a35-8760-03bd8cda12f1"})

    assert plan.mbid == TWO_DISCS and plan.provenance["mbid"] == Provenance.USER


def test_reset_says_whether_there_was_a_pin():
    plan = a_plan(FIRST)
    assert reset_field(plan, "mbid") is False
    plan.mbid, plan.provenance["mbid"] = TWO_DISCS, Provenance.USER
    assert reset_field(plan, "mbid") is True
    assert plan.mbid is None


# -- the pin has to reach the plan that is actually enriched (R-490) ------------------------------


def test_the_pin_reaches_the_fetch_not_only_the_merge(tmp_path, one_second_of_sound):
    """Found by the real run, after P101 was released. The pin is on the plan **on disk** and the
    enrichment happens on the **fresh** plan the pass builds from the source — so `Night is Calling`
    was pinned to the 2-CD release and the pass still logged *looking for the release* and took the
    search's answer. `merge_plans` then kept the pinned id, so the plan named one release while its
    names, numbers and discs came from another. A pin that only survives the merge is not a pin.
    """
    from test_intake import QUIET
    from test_repointing import a_library, an_album

    from noaap import intake
    from noaap.download import load_plan, save_plan

    root = tmp_path / "collection"
    an_album(root / "DOMINUM" / "Night is Calling", FIRST, one_second_of_sound,
             artist="DOMINUM", album="Night is Calling")
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    album = root / "DOMINUM" / "Night is Calling"
    plan = load_plan(album)
    apply_user_edits(plan, {"mbid": TWO_DISCS})
    save_plan(plan, album)

    said: list[str] = []
    mb = StubMB()
    service = a_library(root)
    service.cfg.musicbrainz = True
    service._mb = mb
    service.log = said.append
    service.fetch(str(album), report_only=True)

    assert mb.searched == 0, said
    assert mb.opened == [TWO_DISCS], said
    assert [line for line in said if "the release you pinned" in line], said
    assert [line for line in said if "release pinned by you, and it answers" in line], said


def test_an_album_with_no_pin_still_searches(tmp_path, one_second_of_sound):
    from test_intake import QUIET
    from test_repointing import a_library, an_album

    from noaap import intake

    root = tmp_path / "collection"
    an_album(root / "DOMINUM" / "Night is Calling", FIRST, one_second_of_sound,
             artist="DOMINUM", album="Night is Calling")
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)

    said: list[str] = []
    mb = StubMB()
    service = a_library(root)
    service.cfg.musicbrainz = True
    service._mb = mb
    service.log = said.append
    service.fetch(str(root / "DOMINUM" / "Night is Calling"), report_only=True)

    assert mb.searched == 1, said
    assert not [line for line in said if "pinned" in line], said
