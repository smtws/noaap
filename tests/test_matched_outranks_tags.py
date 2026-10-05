"""A matched release outranks the files' own tags (DESIGN §9, slice 148; R-515).

The user: *"normalizing a library may become quite a workload otherwise"*. Until this, `FIRSTHAND`
gave the files' own tags the last word over MusicBrainz for every adopted album — 1,312 of the
user's 1,523 — so identifying one brought its ids and left every name as the owner had typed it.
A release that has been **matched** (by name, by artist and by four fifths of its track titles) is
better information than a tag nobody has checked since; one that was **not** matched changes
nothing, and a value the **user** typed outranks both.
"""

from __future__ import annotations

from typing import Any

from noaap.enrich import _set, enrich_release
from noaap.models import AlbumPlan, Kind, PlanTrack, Provenance

CREDIT = [{"name": "A Band", "artist": {"name": "A Band"}}]
MINE = ["one", "two", "three", "four", "five"]
THEIRS = ["One", "Two", "Three", "Four", "Five"]


def a_plan(titles=MINE, **kw):
    plan = AlbumPlan(source_url="/m", source_id="/m", kind=Kind.OFFICIAL_ALBUM, album="an album",
                     albumartist="a band", year=None, cover_url=None, folder="a band/an album",
                     provider="folder",
                     tracks=[PlanTrack(video_id=f"/m/{n:02d}.flac", number=n, artist="a band",
                                       title=t, filename=f"{n:02d}.flac",
                                       provenance={"title": Provenance.FILE_TAGS,
                                                   "artist": Provenance.FILE_TAGS},
                                       state="done") for n, t in enumerate(titles, 1)],
                     **kw)
    plan.provenance.update({"album": Provenance.FILE_TAGS, "albumartist": Provenance.FILE_TAGS,
                            "year": Provenance.FILE_TAGS})
    plan.auto.update({"album": "an album", "albumartist": "a band"})
    for t in plan.tracks:
        t.auto.update({"title": t.title, "artist": t.artist})
    return plan


def a_release(mbid="rel-1", titles=THEIRS):
    return {"id": mbid, "title": "An Album", "date": "2001", "country": "DE", "status": "Official",
            "artist-credit": CREDIT, "release-group": {"id": "rg", "first-release-date": "2001"},
            "media": [{"position": 1, "tracks": [
                {"position": n, "title": t, "artist-credit": CREDIT,
                 "recording": {"id": f"rec-{n}"}} for n, t in enumerate(titles, 1)]}]}


class Matches:
    def __init__(self, titles=THEIRS):
        self.titles = titles

    def search_releases(self, artist, album):
        return [{"id": "rel-1", "title": "An Album", "score": 100, "status": "Official",
                 "country": "DE", "date": "2001", "track-count": len(self.titles),
                 "artist-credit": CREDIT}]

    def release(self, mbid):
        return a_release(mbid, self.titles)

    def search_recordings(self, artist, title):
        return []

    def artist(self, name):
        return None

    def artist_albums(self, artist):
        return []


# -- the rule ------------------------------------------------------------------------------------


def test_a_matched_release_writes_the_album_fields():
    plan = a_plan()

    assert enrich_release(plan, Matches()) is True

    assert plan.album == "An Album"
    assert plan.albumartist == "A Band"
    assert plan.year == 2001
    assert plan.provenance["album"] == Provenance.MB


def test_a_matched_release_writes_the_track_titles():
    plan = a_plan()

    enrich_release(plan, Matches())

    assert [t.title for t in plan.tracks] == THEIRS
    assert all(t.provenance["title"] == Provenance.MB for t in plan.tracks)


def test_nothing_matched_leaves_the_files_tags_alone():
    """An unmatched album keeps its taken-in values, as before."""
    class Nothing(Matches):
        def search_releases(self, artist, album):
            return []

    plan = a_plan()
    assert enrich_release(plan, Nothing()) is False
    assert plan.album == "an album"
    assert [t.title for t in plan.tracks] == MINE
    assert plan.provenance["album"] == Provenance.FILE_TAGS


def test_a_release_that_does_not_fit_leaves_them_alone_too():
    plan = a_plan()
    assert enrich_release(plan, Matches(titles=["Nope", "Nor", "This", "Or", "That"])) is False
    assert plan.album == "an album"
    assert [t.title for t in plan.tracks] == MINE


# -- what still outranks it ----------------------------------------------------------------------


def test_what_the_user_typed_is_restored_over_it():
    """`_set` writes and `merge_plans` puts the user's value back, which is where that rule lives
    (slice 29). This is the pairing the page relies on."""
    from noaap.plan import merge_plans

    stored = a_plan()
    stored.album, stored.provenance["album"] = "What I Call It", Provenance.USER
    fresh = a_plan()
    enrich_release(fresh, Matches())

    merged = merge_plans(stored, fresh)

    assert merged.album == "What I Call It"
    assert merged.mbid == "rel-1", "the release still arrives"


def test_set_without_a_match_keeps_a_file_tags_value_but_remembers_the_answer():
    plan = a_plan()
    _set(plan, "album", "An Album")
    assert plan.album == "an album"
    assert plan.auto["album"] == "An Album", "kept where a reset can reach it"


def test_set_with_a_match_writes_it():
    plan = a_plan()
    _set(plan, "album", "An Album", matched=True)
    assert plan.album == "An Album"
    assert plan.provenance["album"] == Provenance.MB


def test_a_field_the_files_never_had_is_filled_either_way():
    plan = a_plan()
    plan.provenance.pop("year", None)
    _set(plan, "year", 1999)
    assert plan.year == 1999
