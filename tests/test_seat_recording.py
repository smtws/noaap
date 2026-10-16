"""An outlier in a release that otherwise fits keeps the seat and not the id (§9, slice 213; q36,
R-755, R-756): more than `FAR_LENGTH` (30 s) off its seat's recording.

`Limp Bizkit — Gold Cobra` (pinned `dd3e8e1d`): 03 is the 284-s music video on the seat of a 233-s
recording, and it took that recording's id with the seat; `Chocolate Starfish`'s 12 `Boiler` (a
301-s video on a 420-s seat) did the same. Where the album is cut elsewhere as a whole — `Mono Inc.
— Symphonic Live`, slice 198 — the ids stay. The withheld recording stays on the track for
`noaap recording` (slice 207), which takes the full-length audio and the id with it.
"""

from __future__ import annotations

import copy

from test_incremental import opus_template  # fixture
from test_release_lengths import Disambiguated, edition, the_playlist
from test_same_slot import album  # fixture
from test_take_recordings import Searching, a_service, prinzessin

from noaap.download import load_plan, save_plan
from noaap.enrich import enrich_release
from noaap.plan import merge_plans

TITLES = ["Intro", "Hot Dog", "The One", "Getcha Groove On (Dirt Road Mix)", "Hold On"]
FILES = [79, 231, 344, 260, 348]
# the release fits four of five: `The One` is a 284-s video on a 233-s seat (Gold Cobra's 03)
EDIT = [79, 231, 284, 260, 348]
RELEASE = edition("rel", TITLES, [78.8, 230.3, 233.0, 259.5, 347.6], "2011-06-24")


def picked(lengths=EDIT, release=RELEASE):
    plan = the_playlist(lengths)
    assert enrich_release(plan, Disambiguated(release)) is True
    return plan, plan.tracks[2]


def test_a_video_edit_on_a_fitting_release_keeps_the_seat_and_not_the_id():
    plan, one = picked()
    assert (one.number, one.title, one.mbid, one.mb_length) == (3, "The One", None, None)
    assert one.seat_recording == {"id": "rec-rel-3", "length": 233.0}
    assert "1-03 The One: keeps the release's seat — its recording is 233 s, the file 284 s; no id" in plan.seat_notes


def test_a_recording_longer_than_the_file_is_withheld_too():
    """`Boiler`: a 301-s video on the seat of a 420-s recording."""
    plan, one = picked([79, 231, 301, 260, 348], edition("rel", TITLES, [78.8, 230.3, 420.3, 259.5, 347.6], "2000"))
    assert (one.mbid, one.seat_recording) == (None, {"id": "rec-rel-3", "length": 420.3})


def test_within_far_length_the_seats_id_stays():
    """`My Way`: a 293-s single edit on a 273-s seat — 20 s, inside the 30 s `complete` calls the same
    recording's reach; a live album drifts as much (slice 192), and keeps its ids (R-756)."""
    plan, one = picked([79, 231, 293, 260, 348], edition("rel", TITLES, [78.8, 230.3, 273.0, 259.5, 347.6], "2000"))
    assert (one.mbid, one.mb_length, one.seat_recording) == ("rec-rel-3", 273.0, None)
    assert not [n for n in plan.seat_notes if "no id" in n]


def test_the_tracks_that_fit_keep_their_ids():
    plan, _ = picked()
    assert [t.mbid for t in plan.tracks] == ["rec-rel-1", "rec-rel-2", None, "rec-rel-4", "rec-rel-5"]
    assert all(t.seat_recording is None for t in plan.tracks if t.mbid)


def test_an_album_cut_elsewhere_as_a_whole_keeps_every_id():
    """`Mono Inc. — Symphonic Live`: no file is the release's length, so none is an outlier."""
    plan, _ = picked([500, 500, 500, 500, 500])
    assert [t.mbid for t in plan.tracks] == [f"rec-rel-{n}" for n in range(1, 6)]
    assert "MusicBrainz: release rel — 0 of 5 lengths fit the files" in plan.seat_notes


def test_a_pinned_release_follows_the_same_rule():
    plan = the_playlist(EDIT)
    plan.mbid, plan.provenance["mbid"] = "rel", "user"
    assert enrich_release(plan, Disambiguated(RELEASE)) is True
    assert (plan.tracks[2].mbid, plan.tracks[2].seat_recording["id"]) == (None, "rec-rel-3")


def test_the_merge_takes_back_a_stored_id_that_is_the_withheld_one():
    fresh, _ = picked()
    existing = copy.deepcopy(fresh)
    existing.tracks[2].mbid, existing.tracks[2].mb_length, existing.tracks[2].seat_recording = "rec-rel-3", 233.0, None
    merged = merge_plans(existing, fresh)
    one = merged.tracks[2]
    assert (one.mbid, one.mb_length, one.seat_recording["id"]) == (None, None, "rec-rel-3")


def test_the_merge_keeps_mbid_none_across_another_update():
    fresh, _ = picked()
    again = merge_plans(merge_plans(copy.deepcopy(fresh), fresh), copy.deepcopy(fresh))
    assert (again.tracks[2].mbid, again.tracks[2].seat_recording["id"]) == (None, "rec-rel-3")


def test_a_refused_seat_recording_is_not_kept_to_offer():
    fresh, _ = picked()
    existing = copy.deepcopy(fresh)
    existing.tracks[2].refused_recordings = ["rec-rel-3"]
    assert merge_plans(existing, fresh).tracks[2].seat_recording is None


def withheld(album_dir, seat_s=212.0):
    plan, t = prinzessin(album_dir, seat_s)
    plan = load_plan(album_dir)
    t = next(x for x in plan.tracks if x.video_id == "Lkrs1eggmBg")
    t.mbid, t.mb_length, t.seat_recording = None, None, {"id": "rec-prinzessin", "length": seat_s}
    save_plan(plan, album_dir)
    return plan, t


def test_recording_finds_a_full_length_video_for_a_withheld_seat(album, opus_template):
    lib, album_dir = album
    plan, t = withheld(album_dir)
    yt, said = Searching(opus_template), []
    out = a_service(lib, yt, said).take_recordings(plan.source_id, only={f"{t.disc}-{t.number:02d}"})
    assert out.status == "dry" and yt.downloads == []
    assert [s for s in said if s.startswith(f"1-{t.number:02d} Prinzessin (recording 3:32, the seat's, withheld, "
                                            "file 210 s): “Schandmaul - Prinzessin” by condeorlok1921, 212 s")], said


def test_taking_the_seats_recording_gives_the_track_its_id(album, opus_template):
    lib, album_dir = album
    plan, t = withheld(album_dir)
    a_service(lib, Searching(opus_template), []).take_recordings(
        plan.source_id, only={f"{t.disc}-{t.number:02d}"}, dry_run=False)
    after = next(x for x in load_plan(album_dir).tracks if x.video_id == "Lkrs1eggmBg")
    assert (after.source_override, after.mbid, after.mb_length, after.seat_recording) == (
        "cpdBmSmmK0s", "rec-prinzessin", 212.0, None)


def test_reask_refuses_a_withheld_seat_recording_like_a_stored_id(album, opus_template):
    from test_reask_recording import ANSWERS, LIVE, Answering, sleeping_sun
    from test_reask_recording import a_service as reasking

    lib, album_dir = album
    plan, one = sleeping_sun(album_dir, opus_template)
    plan = load_plan(album_dir)
    plan.tracks[0].mbid, plan.tracks[0].mb_length = None, None
    plan.tracks[0].seat_recording = {"id": LIVE, "length": 295.2}
    save_plan(plan, album_dir)
    out = reasking(lib, opus_template, Answering(*ANSWERS), []).reask_recordings(
        plan.source_id, only={"1-01"}, dry_run=False)
    assert out.status == "ok"
    after = load_plan(album_dir).tracks[0]
    assert (after.mbid, after.refused_recordings, after.seat_recording) == ("f5b2b7f5-studio", [LIVE], None)
