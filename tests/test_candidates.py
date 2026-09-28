"""A track holds candidates, not a video id (DESIGN §9, slice 50).

The shape only: no ranking and no second provider yet. What matters here is that the new fields are
**derived from the old ones and never ahead of them** — two ytalbums share a library, and the older
one writes `source_override` knowing nothing about candidates.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_incremental import FakeYouTube, opus_template, vol1

from noaap.config import Config
from noaap.download import load_plan, run, save_plan
from noaap.models import AlbumPlan, PlanTrack
from noaap.plan import build_plan
from noaap.service import Service, apply_user_edits, switch_source


def track(**kw) -> PlanTrack:
    base = dict(video_id="playlistvid", number=1, filename="f.opus", provenance={},
                artist="A", title="T")
    return PlanTrack(**{**base, **kw})


@pytest.fixture
def library(tmp_path, opus_template):
    yt = FakeYouTube(opus_template)
    plan = build_plan(vol1())
    run(plan, tmp_path / plan.folder, yt)
    return tmp_path, load_plan(tmp_path / plan.folder), yt


# -- synthesis ------------------------------------------------------------------------------------


def test_a_track_with_no_candidates_gets_one_for_its_video():
    t = track()
    assert [c.ref for c in t.candidates] == ["playlistvid"]
    assert t.candidates[0].provider == "youtube"
    assert t.candidates[0].added_by == "source"
    assert t.chosen == "playlistvid"


def test_an_override_becomes_a_second_candidate_and_the_chosen_one():
    t = track(source_override="otherone")
    assert [c.ref for c in t.candidates] == ["playlistvid", "otherone"]
    assert t.candidate("otherone").added_by == "user"
    assert t.chosen == "otherone"
    assert t.effective_id == "otherone", "the audio still comes from the override"


def test_the_old_fields_win_over_a_stale_candidate_list():
    """An older ytalbum can write `source_override` and leave `chosen` behind."""
    t = PlanTrack.from_dict({**track().to_dict(), "source_override": "newone",
                             "chosen": "playlistvid"})
    assert t.chosen == "newone"
    assert "newone" in [c.ref for c in t.candidates]


def test_the_shape_survives_a_round_trip():
    t = track(source_override="otherone")
    t.refuse("badone")
    again = PlanTrack.from_dict(json.loads(json.dumps(t.to_dict())))
    assert again == t


# -- switching, and refusing ----------------------------------------------------------------------


def test_switching_adds_the_candidate_and_moves_chosen():
    t = track()
    assert switch_source(t, "otherone")
    assert t.chosen == "otherone"
    assert [c.ref for c in t.candidates] == ["playlistvid", "otherone"]
    assert t.candidate("otherone").why


def test_switching_back_keeps_the_candidate_but_chooses_the_playlists_video():
    t = track(source_override="otherone")
    assert switch_source(t, None)
    assert t.chosen == "playlistvid"
    assert [c.ref for c in t.candidates] == ["playlistvid", "otherone"], "it is still known about"


def test_a_refused_candidate_is_never_chosen_again():
    t = track()
    t.refuse("badone")
    assert not switch_source(t, "badone"), "refusing means refusing"
    assert t.chosen == "playlistvid"


def test_refusing_the_one_in_use_goes_back_to_the_playlists_video():
    """Refusing what you are listening to has to mean something."""
    plan = AlbumPlan.from_dict(build_plan(vol1()).to_dict())
    t = plan.tracks[0]
    switch_source(t, "otherone")
    assert t.effective_id == "otherone"

    apply_user_edits(plan, {"tracks": [{"video_id": t.video_id, "refuse": "otherone"}]})

    assert t.source_override is None
    assert t.effective_id == t.video_id
    assert "otherone" in t.refused_candidates


def test_refusing_is_remembered_across_a_round_trip():
    plan = AlbumPlan.from_dict(build_plan(vol1()).to_dict())
    apply_user_edits(plan, {"tracks": [{"video_id": plan.tracks[0].video_id, "refuse": "badone"}]})
    again = AlbumPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert again.tracks[0].refused_candidates == ["badone"]


# -- the one track in the reference library that already has an override --------------------------


def test_an_existing_override_behaves_exactly_as_before(library):
    """The library has one `source_override` today; it must look and work unchanged."""
    tmp_path, plan, yt = library
    t = plan.tracks[0]
    before = t.effective_id
    switch_source(t, "overridevid")
    save_plan(plan, tmp_path / plan.folder)

    back = load_plan(tmp_path / plan.folder).tracks[0]
    assert back.source_override == "overridevid"
    assert back.effective_id == "overridevid" != before
    assert back.chosen == "overridevid"
    # and an older ytalbum, which reads only the old field, still sees the same thing
    assert json.loads(json.dumps(back.to_dict()))["source_override"] == "overridevid"


def test_the_quality_of_the_file_we_have_is_measured(library):
    tmp_path, plan, yt = library
    t = plan.tracks[0]
    chosen = t.candidate(t.effective_id)
    assert chosen is not None
    assert chosen.codec == "opus"
    assert chosen.channels
    assert chosen.length == pytest.approx(t.file_length, abs=0.1)
    # `sample_rate` stays empty for Opus on purpose: the format is always 48 kHz and mutagen reports
    # no per-file rate, so there is nothing measured to record. An absent number is not a zero.
    assert chosen.sample_rate is None


# -- the bin ---------------------------------------------------------------------------------------


def test_restoring_a_displaced_file_refuses_whatever_displaced_it(library):
    """Spike §4: putting a file back means saying no to the thing that replaced it."""
    from noaap.recycle import bin_track, entries
    from noaap.service import _inside

    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    t = plan.tracks[0]
    s = Service(Config(musicbrainz=False), tmp_path, yt=yt, log=lambda _: None)

    bin_track(tmp_path, album_dir, plan, t, "replaced by a better candidate",
              audio=_inside(album_dir, t.filename),
              ranking={"chosen": {"ref": "shinynewone", "bitrate": 320000},
                       "kept": {"ref": t.video_id, "bitrate": 160000}})
    assert s.restore(entries(tmp_path)[0].id).status == "ok"

    back = load_plan(album_dir).tracks[0]
    assert "shinynewone" in back.refused_candidates
