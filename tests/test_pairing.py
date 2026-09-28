"""Which track is which, across two libraries (DESIGN §9, slice 54).

Pairing is where a merge can do its worst: one wrong pair replaces a recording with a different
song. So these cases are mostly about what the matcher **refuses** to conclude.
"""
from __future__ import annotations

from pathlib import Path

from noaap.models import AlbumPlan, Kind, PlanTrack
from noaap.pairing import How, Side, pair, sides


def track(title: str, artist: str = "A Band", mbid: str | None = None, filename: str = "") -> PlanTrack:
    return PlanTrack(video_id=f"id-{title}", number=1, artist=artist, title=title,
                     filename=filename or f"{title}.opus", provenance={}, mbid=mbid)


def album(name: str, *tracks: PlanTrack, provider: str = "youtube") -> AlbumPlan:
    return AlbumPlan(source_url=f"x://{name}", source_id=name, kind=Kind.OFFICIAL_ALBUM,
                     album=name, albumartist="A Band", year=None, cover_url=None,
                     folder=name, tracks=list(tracks), provider=provider)


def side(plan: AlbumPlan, n: int = 0, where: str = "/library") -> Side:
    return Side(plan, plan.tracks[n], Path(where))


# -- what pairs ------------------------------------------------------------------------------------


def test_a_recording_id_is_the_strongest_key():
    mine = album("Intake", track("Different Spelling", mbid="rec-1"))
    theirs = album("Library", track("A Quite Different Title", mbid="rec-1"))

    found = pair([side(mine)], [side(theirs)])

    assert found.counts()["pairs"] == 1
    assert found.pairs[0].how is How.RECORDING


def test_names_pair_what_has_no_recording_id():
    mine = album("Intake", track("Hey Living People"))
    theirs = album("Library", track("hey living people"))

    found = pair([side(mine)], [side(theirs)])

    assert found.pairs[0].how is How.NAME, "the key is normalised, not literal"


def test_a_different_artist_is_a_different_song():
    mine = album("Intake", track("Behind Blue Eyes", artist="Limp Bizkit"))
    theirs = album("Library", track("Behind Blue Eyes", artist="The Who"))

    found = pair([side(mine)], [side(theirs)])

    assert found.counts() == {"pairs": 0, "recording id": 0, "artist and title": 0,
                              "ambiguous": 0, "unpaired": 1}


# -- what it refuses to conclude ---------------------------------------------------------------------


def test_a_key_that_names_several_tracks_is_not_a_pair():
    """A song on three compilations. Picking one of them is the most damaging thing a merge can
    do, so it is shown instead — with all the candidates, so a person can settle it."""
    mine = album("Intake", track("Nothing Else Matters"))
    theirs = album("Library", track("Nothing Else Matters"), track("Nothing Else Matters"))

    found = pair([side(mine)], [side(theirs, 0), side(theirs, 1)])

    assert found.counts()["pairs"] == 0 and found.counts()["ambiguous"] == 1
    assert len(found.ambiguous[0][1]) == 2, "and it keeps every one of them for the report"


def test_an_ambiguous_strong_key_does_not_fall_through_to_a_weak_one():
    """Measured on the real material: 8 tracks of 770 took a name pair after their recording id
    was ambiguous. A weaker key cannot resolve what a stronger one could not."""
    mine = album("Intake", track("Ding", mbid="rec-1"))
    two_recordings = album("Library", track("Ding", mbid="rec-1"), track("Ding (live)", mbid="rec-1"))

    found = pair([side(mine)], [side(two_recordings, 0), side(two_recordings, 1)])

    assert found.counts()["pairs"] == 0
    assert found.counts()["ambiguous"] == 1


def test_what_the_other_library_does_not_have_is_simply_unpaired():
    mine = album("Intake", track("An Album Track"))

    found = pair([side(mine)], [])

    assert found.counts()["unpaired"] == 1
    assert found.unpaired[0].track.title == "An Album Track"


def test_a_track_with_neither_a_recording_id_nor_a_name_pairs_with_nothing():
    nameless = album("Intake", PlanTrack(video_id="x", number=1, artist="", title="",
                                         filename="x.opus", provenance={}))
    theirs = album("Library", track("Something"))

    found = pair([side(nameless)], [side(theirs)])

    assert found.counts()["unpaired"] == 1


# -- what a side knows about itself ------------------------------------------------------------------


def test_a_side_knows_whether_its_audio_is_actually_there(tmp_path):
    plan = album("Library", track("Here", filename="here.opus"), track("Gone", filename="gone.opus"))
    (tmp_path / "here.opus").write_bytes(b"not really audio, but it exists")

    here, gone = Side(plan, plan.tracks[0], tmp_path), Side(plan, plan.tracks[1], tmp_path)

    assert here.present is True and gone.present is False


def test_sides_walks_a_whole_library():
    library = [(Path("/a"), album("One", track("t1"), track("t2"))),
               (Path("/b"), album("Two", track("t3")))]

    assert [s.track.title for s in sides(library)] == ["t1", "t2", "t3"]
    assert [s.album_dir.name for s in sides(library)] == ["a", "a", "b"]
