"""Of two files of one recording, the one the release's length fits keeps the seat (§9, slice 199;
q24, R-671).

`Feuerschwanz — Das Elfte Gebot (YouTube)` holds `Kampfzwerg` twice: 1-04, 219.6 s, and the official
video at 2-12, 237.6 s, both stored as recording 63c35ba7, whose 1-04 on the release is 215.5 s. On
2026-10-14 the playlist listed the official video at 4 instead of the other: the read seated the 238-s
video on 1-04, and the 220-s file — now dropped from the source — went last. The seats followed
whichever video the playlist happened to list.
"""

from __future__ import annotations

import copy

from test_incremental import vol1

from noaap.plan import build_plan, merge_plans

REC, SLOT = "63c35ba7", 215.5


def an_album(dropped_s=219.6, listed_s=237.6, twin_rec=REC):
    """The stored plan: 13 tracks, 04 the 220-s Kampfzwerg, and its twin, the official video, at 14."""
    existing = build_plan(vol1())
    for t in existing.tracks:
        t.state, t.file_length = "done", 200.0
    four = existing.tracks[3]
    # each video the length of its file, as on the share (slice 206 asks the video's length)
    four.mbid, four.mb_length, four.file_length, four.duration = REC, SLOT, dropped_s, dropped_s
    twin = copy.deepcopy(four)
    twin.video_id, twin.number, twin.filename = "OFFICIALVID", 14, "14 twin.opus"
    twin.mbid, twin.file_length, twin.duration = twin_rec, listed_s, listed_s
    existing.tracks.append(twin)
    return existing, four, twin


def the_playlist_lists_the_twin(existing, four, twin):
    """The fresh read with the twin listed at 4 instead, seated by the release on 1-04."""
    coll = vol1()
    next(e for e in coll.entries if e.video_id == four.video_id).video_id = twin.video_id
    fresh = build_plan(coll)
    at4 = next(t for t in fresh.tracks if t.video_id == twin.video_id)
    at4.mbid, at4.mb_length = twin.mbid, SLOT
    return merge_plans(existing, fresh)


def seat(plan, video_id):
    return next((t.disc, t.number) for t in plan.tracks if t.video_id == video_id)


def test_the_file_the_release_s_length_fits_keeps_its_seat_and_the_listed_twin_goes_last():
    existing, four, twin = an_album()
    merged = the_playlist_lists_the_twin(existing, four, twin)

    assert seat(merged, four.video_id) == (1, 4), "the 220-s file keeps 1-04"
    assert seat(merged, twin.video_id) == (1, 14), "the 238-s video takes the last seat"
    assert any(n.startswith(f"1-04 {four.title} (219.6 s) keeps its seat — it is the release's length")
               for n in merged.seat_notes), merged.seat_notes


def test_when_the_playlist_lists_the_fitting_file_nothing_changes():
    existing, four, twin = an_album()
    fresh = build_plan(vol1())
    at4 = next(t for t in fresh.tracks if t.video_id == four.video_id)
    at4.mbid, at4.mb_length = REC, SLOT

    merged = merge_plans(existing, fresh)

    assert seat(merged, four.video_id) == (1, 4)
    assert seat(merged, twin.video_id) == (1, 14)
    assert not [n for n in merged.seat_notes if "the release's length" in n]


def test_a_dropped_track_of_another_recording_still_goes_last():
    existing, four, twin = an_album()
    four.mbid = "another"
    merged = the_playlist_lists_the_twin(existing, four, twin)

    assert seat(merged, twin.video_id) == (1, 4)
    assert seat(merged, four.video_id) == (1, 14), "slice 3: one the source dropped goes last"


def test_when_both_fit_the_listed_one_keeps_the_seat():
    existing, four, twin = an_album(listed_s=217.0)
    merged = the_playlist_lists_the_twin(existing, four, twin)

    assert seat(merged, twin.video_id) == (1, 4)
    assert seat(merged, four.video_id) == (1, 14)
