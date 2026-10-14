"""A done track's audio from the video that is its recording (§9, slice 207; q30, R-698).

The search answers are YouTube's own for `Schandmaul - Prinzessin`, saved on 2026-10-15
(`design-fixtures/yt_search_prinzessin.json`): the official 210-s video the album already plays,
three uploads of 212 to 214 s, a live take of 284 s, and other songs of the band.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_incremental import FakeYouTube, opus_template
from test_same_slot import album

from noaap.config import Config
from noaap.download import load_plan, save_plan
from noaap.service import Service

SAVED = json.loads((Path(__file__).parent.parent / "design-fixtures" / "yt_search_prinzessin.json").read_text())


class Searching(FakeYouTube):
    def __init__(self, template):
        super().__init__(template)
        self.asked: list[str] = []

    def capabilities(self):
        return super().capabilities() | {"tracks"}

    def find_tracks(self, query, limit=8):
        self.asked.append(query)
        return SAVED.get(query, [])


def prinzessin(album_dir, recording_s):
    plan = load_plan(album_dir)
    t = next(t for t in plan.tracks if t.video_id == "Lkrs1eggmBg")
    t.artist, t.title, t.mbid, t.mb_length, t.file_length = "Schandmaul", "Prinzessin", "rec-prinzessin", recording_s, 210.0
    save_plan(plan, album_dir)
    return plan, t


def a_service(lib, yt, said):
    return Service(Config(musicbrainz=False, lyrics=False), lib, log=said.append, yt=yt)


def test_the_dry_run_names_the_hit_its_channel_its_length_and_the_distance(album, opus_template):
    lib, album_dir = album
    plan, t = prinzessin(album_dir, 212.0)
    yt, said = Searching(opus_template), []
    out = a_service(lib, yt, said).take_recordings(plan.source_id, only={f"{t.disc}-{t.number:02d}"})
    assert out.status == "dry" and yt.downloads == []
    assert [s for s in said if s.startswith(f"1-{t.number:02d} Prinzessin (recording 3:32, file 210 s): "
                                            "“Schandmaul - Prinzessin” by condeorlok1921, 212 s, +0.0 s")], said
    assert yt.asked[0] == "Schandmaul - Prinzessin", "and the album's own track is not offered back to it"
    again = load_plan(album_dir)
    assert next(x for x in again.tracks if x.video_id == t.video_id).source_override is None


def test_the_apply_switches_to_the_hit_and_fetches_it_and_keeps_the_rest_as_candidates(album, opus_template):
    lib, album_dir = album
    plan, t = prinzessin(album_dir, 212.0)
    yt = Searching(opus_template)
    a_service(lib, yt, []).take_recordings(plan.source_id, only={f"{t.disc}-{t.number:02d}"}, dry_run=False)
    after = next(x for x in load_plan(album_dir).tracks if x.video_id == "Lkrs1eggmBg")
    assert (after.source_override, after.state, after.mbid) == ("cpdBmSmmK0s", "done", "rec-prinzessin")
    assert yt.downloads == ["cpdBmSmmK0s"]
    assert {c.ref for c in after.candidates} >= {"Lkrs1eggmBg", "cpdBmSmmK0s", "dCQontnB6Ms"}
    assert "ZDJoHHwpI6Q" not in {c.ref for c in after.candidates}, "the live take is never a candidate"


def test_without_only_the_tracks_whose_recording_is_longer_than_the_file_are_asked(album, opus_template):
    """The share's case: a 301-s recording on record, a 210-s file — and nothing of that length on
    YouTube under this title, so nothing is taken."""
    lib, album_dir = album
    plan, t = prinzessin(album_dir, 301.0)
    yt, said = Searching(opus_template), []
    out = a_service(lib, yt, said).take_recordings(plan.source_id, dry_run=False)
    assert [s for s in said if "Prinzessin (recording 5:01, file 210 s): nothing close enough was found" in s], said
    assert out.status == "ok" and yt.downloads == []
    assert yt.asked == ["Schandmaul - Prinzessin"], "MusicBrainz off: no release to name, and `Vol. 1` names nothing"


def test_a_track_not_on_the_album_is_refused(album, opus_template):
    lib, album_dir = album
    plan, _ = prinzessin(album_dir, 212.0)
    out = a_service(lib, Searching(opus_template), []).take_recordings(plan.source_id, only={"9-99"})
    assert out.status == "failed" and out.message.startswith("this album has no track 9-99")


def test_the_second_query_names_the_recording_s_release(album, opus_template):
    class MB:
        def search_recordings(self, artist, title):
            return [{"id": "rec-prinzessin", "title": "Prinzessin", "length": 212_000,
                     "releases": [{"title": "Wie Pech & Schwefel", "status": "Official",
                                   "release-group": {"primary-type": "Album"}}]}]

        def __getattr__(self, name):
            return lambda *a, **k: []

    lib, album_dir = album
    plan, t = prinzessin(album_dir, 212.0)
    yt = Searching(opus_template)
    Service(Config(musicbrainz=True, lyrics=False), lib, log=lambda s: None, yt=yt, mb=MB()).take_recordings(
        plan.source_id, only={f"{t.disc}-{t.number:02d}"})
    assert yt.asked == ["Schandmaul - Prinzessin", "Schandmaul Prinzessin Wie Pech & Schwefel"]
