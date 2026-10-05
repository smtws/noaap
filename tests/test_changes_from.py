"""What a lookup would change, field by field (DESIGN §9, slice 142; R-507).

`update --dry-run` on an album already in the library returned at the merge — `0 new, 0 no longer in
the source` — and said nothing about the album's own fields: not the title it would rewrite, not the
numbers it would move, not the discs it would assign. The `NN would be retagged` lines are
`repair`'s, a different pass asking a different question.
"""

from __future__ import annotations

import copy

from noaap.models import AlbumPlan, Kind, PlanTrack
from noaap.plan import changes_from


def a_track(n, title, disc=1, mbid=None):
    return PlanTrack(video_id=f"v{n}", number=n, artist="A Band", title=title,
                     filename=f"{n:02d}.opus", provenance={}, disc=disc, state="done", mbid=mbid)


def a_plan(titles=("One", "Two"), **kw):
    base = dict(source_url="u", source_id="u", kind=Kind.OFFICIAL_ALBUM, album="An Album",
                albumartist="A Band", year=2001, cover_url=None, folder="A Band/An Album",
                tracks=[a_track(n, t) for n, t in enumerate(titles, 1)])
    return AlbumPlan(**{**base, **kw})


def test_nothing_changed_is_no_lines():
    was = a_plan()
    assert changes_from(was, copy.deepcopy(was)) == []


def test_an_album_field_is_one_line_each():
    was = a_plan()
    now = copy.deepcopy(was)
    now.album, now.year, now.mbid = "Another Album", 2002, "rel-1"

    assert changes_from(was, now) == ["album: An Album → Another Album",
                                      "year: 2001 → 2002",
                                      "mbid: nothing → rel-1"]


def test_an_empty_value_reads_as_nothing():
    was = a_plan(year=None)
    now = copy.deepcopy(was)
    now.year = 1999
    assert changes_from(was, now) == ["year: nothing → 1999"]


def test_a_renamed_track_names_its_number():
    was = a_plan()
    now = copy.deepcopy(was)
    now.tracks[1].title = "Two (Remastered)"
    assert changes_from(was, now) == ["02 title: Two → Two (Remastered)"]


def test_a_track_that_moves_says_where_from_and_to():
    was = a_plan(("One", "Two", "Three"))
    now = copy.deepcopy(was)
    now.tracks[2].disc, now.tracks[2].number = 2, 1
    assert changes_from(was, now) == ["Three: 1-03 → 2-01"]


def test_a_recording_id_arriving_is_worth_a_line():
    was = a_plan()
    now = copy.deepcopy(was)
    now.tracks[0].mbid = "rec-1"
    assert changes_from(was, now) == ["01 One: a recording id"]


def test_a_cover_that_would_arrive_is_named():
    was = a_plan()
    now = copy.deepcopy(was)
    now.cover_url = "https://coverartarchive.org/release/r/front-500"
    assert changes_from(was, now) == ["cover: from https://coverartarchive.org/release/r/front-500"]


def test_a_cover_going_away_is_not_a_line():
    """Only what would arrive: a lookup never takes a picture off an album."""
    was = a_plan(cover_url="https://example.com/a.jpg")
    now = copy.deepcopy(was)
    now.cover_url = None
    assert changes_from(was, now) == []


def test_a_track_matched_by_its_id_not_by_its_position():
    """A track that is simply new is named as that, rather than paired with somebody else."""
    was = a_plan(("One", "Two"))
    now = copy.deepcopy(was)
    now.tracks.append(a_track(3, "Three"))

    assert changes_from(was, now) == ["1-03 Three: new in the source"]


def test_a_track_that_left_the_source_is_named():
    was = a_plan(("One", "Two"))
    now = copy.deepcopy(was)
    now.tracks[1].in_source = False

    assert changes_from(was, now) == ["02 Two: no longer in the source"]


def test_the_lines_are_in_the_order_a_reader_reads_them():
    """The album's own fields first, then its tracks."""
    was = a_plan(("One", "Two"))
    now = copy.deepcopy(was)
    now.mbid = "rel-1"
    now.tracks[0].title = "One (Live)"

    assert changes_from(was, now) == ["mbid: nothing → rel-1", "01 title: One → One (Live)"]
