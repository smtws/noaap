"""A track may take its audio from another video, without changing what it is (DESIGN.md §9.34).

The playlist's video stays the identity — the order, `in_source`, the merge, the MusicBrainz
match. Only the audio moves, and everything the old file carried moves out of the way with it.
"""

import json
import shutil
from pathlib import Path

import pytest
from test_incremental import FakeYouTube, opus_template, vol1

from ytalbum.config import Config
from ytalbum.download import load_plan, run, save_plan
from ytalbum.lyrics import timings_stale, write_sidecar
from ytalbum.models import AlbumPlan, Entry, PlanTrack, Provenance
from ytalbum.plan import build_plan, merge_plans
from ytalbum.service import Service, apply_user_edits, switch_source
from ytalbum.trim import ORIGINALS, original_path
from ytalbum.youtube import one_video

SHARED = Path(__file__).parent / "shared"
OTHER = "Z2UO4FsFGFM"  # the audio upload of a song whose playlist entry is the film cut


class Probing(FakeYouTube):
    """A FakeYouTube that also answers the one question an override raises: whose upload is it?"""

    def __init__(self, template, channel="Kupfergold Records", duration=184.0):
        super().__init__(template)
        self.channel, self.duration, self.probed = channel, duration, []

    def probe(self, video_id):
        self.probed.append(video_id)
        return Entry(video_id=video_id, position=1, title="the song itself",
                     channel=self.channel, duration=self.duration)


def album(tmp_path, yt):
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    return album_dir, load_plan(album_dir)


# -- what a source is ----------------------------------------------------------------------


@pytest.mark.parametrize("case", json.loads((SHARED / "video_ids.json").read_text())["cases"],
                         ids=lambda c: c["why"])
def test_one_video_is_what_the_shared_table_says(case):
    """The page refuses a bad paste with the same rule (logic.mjs's `oneVideo`)."""
    assert one_video(case["text"]) == case["id"]


def test_a_source_that_is_not_one_video_is_refused_with_the_title_in_the_message():
    plan = build_plan(vol1())
    track = plan.tracks[0]
    with pytest.raises(ValueError, match="is not a single YouTube video"):
        apply_user_edits(plan, {"tracks": [{"video_id": track.video_id, "source": "https://www.youtube.com/@someone"}]})
    assert track.source_override is None and track.state == "pending"


def test_plans_written_before_this_existed_still_load():
    """Old plans have none of the new keys; the dataclass defaults are the answer."""
    raw = build_plan(vol1()).to_dict()
    for t in raw["tracks"]:
        for key in ("source_override", "lyrics_for_source", "lyrics_for_length"):
            del t[key]
    plan = AlbumPlan.from_dict(raw)
    assert all(t.source_override is None and t.effective_id == t.video_id for t in plan.tracks)


# -- the switch ----------------------------------------------------------------------------


def track_that_is_done() -> PlanTrack:
    plan = build_plan(vol1())
    t = plan.tracks[0]
    t.state, t.tagged, t.trimmed = "done", "sig", "1.60-"
    t.trim_start, t.trim_end = 1.6, 200.0
    t.file_length, t.duration, t.channel = 207.0, 210.0, "Someone Else"
    t.lyrics, t.lyrics_id = "synced", 4242
    return t


def test_choosing_another_video_puts_the_track_back_in_the_queue():
    t = track_that_is_done()
    assert switch_source(t, OTHER) is True

    assert t.effective_id == OTHER and t.video_id != OTHER  # the identity did not move
    assert t.state == "pending" and t.tagged is None and t.trimmed is None
    assert t.trim_start is None and t.trim_end is None  # they describe seconds of the old recording
    assert t.file_length is None and t.duration is None and t.channel is None
    assert t.lyrics is None and t.lyrics_id == 4242  # looked up again; what was found is not forgotten
    assert t.provenance["source"] == Provenance.USER and t.auto["source"] == t.video_id


def test_choosing_the_same_video_again_changes_nothing():
    t = track_that_is_done()
    switch_source(t, OTHER)
    t.state, t.trim_start = "done", 1.6
    assert switch_source(t, OTHER) is False
    assert t.state == "done" and t.trim_start == 1.6


def test_the_playlists_own_video_is_not_an_override():
    plan = build_plan(vol1())
    t = plan.tracks[0]
    apply_user_edits(plan, {"tracks": [{"video_id": t.video_id, "source": f"https://youtu.be/{t.video_id}"}]})
    assert t.source_override is None and "source" not in t.provenance


def test_the_way_back_is_the_reset_badge():
    t = track_that_is_done()
    switch_source(t, OTHER)
    t.state, t.trim_start, t.file_length = "done", 0.5, 184.0

    plan = build_plan(vol1())
    plan.tracks[0] = t
    apply_user_edits(plan, {"tracks": [{"video_id": t.video_id, "reset": ["source"]}]})

    assert t.source_override is None and t.effective_id == t.video_id
    assert "source" not in t.provenance and "source" not in t.auto
    assert t.state == "pending" and t.trim_start is None and t.file_length is None  # the same cost


# -- the download --------------------------------------------------------------------------


def test_the_download_takes_the_effective_id_and_the_uploader_follows(tmp_path, opus_template):
    yt = Probing(opus_template)
    album_dir, plan = album(tmp_path, yt)
    track = plan.tracks[0]
    switch_source(track, OTHER)
    save_plan(plan, album_dir)

    run(plan, album_dir, yt)

    saved = load_plan(album_dir).tracks[0]
    assert yt.downloads[-1] == OTHER  # not the playlist's video
    assert yt.probed == [OTHER]
    assert saved.channel == "Kupfergold Records"  # or a channel-wide trim would cut the wrong file
    assert saved.duration == 184.0
    assert saved.state == "done" and saved.in_source is True
    assert (album_dir / saved.filename).exists()


def test_originals_are_kept_per_recording(tmp_path, opus_template):
    """Two recordings must never be cut from each other's original."""
    t = track_that_is_done()
    assert original_path(tmp_path, t).name == f"{t.video_id}.opus"
    switch_source(t, OTHER)
    assert original_path(tmp_path, t).name == f"{OTHER}.opus"


def test_switching_removes_the_previous_recordings_original(tmp_path, opus_template):
    yt = Probing(opus_template)
    album_dir, plan = album(tmp_path, yt)
    track = plan.tracks[0]
    kept = album_dir / ORIGINALS / f"{track.video_id}.opus"
    kept.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(album_dir / track.filename, kept)

    service = Service(Config(musicbrainz=False), tmp_path, yt=yt)
    service.apply_edits(plan.source_id, {"tracks": [{"video_id": track.video_id, "source": OTHER}]})

    assert not kept.exists()  # it is another recording, and could only ever shadow the new one
    assert load_plan(album_dir).tracks[0].source_override == OTHER


# -- the merge -----------------------------------------------------------------------------


def test_a_fresh_playlist_keeps_the_override_and_its_uploader():
    existing = build_plan(vol1())
    t = existing.tracks[0]
    t.state, t.channel, t.duration = "done", "Kupfergold Records", 184.0
    switch_source(t, OTHER)
    t.channel, t.duration = "Kupfergold Records", 184.0  # as the download left them

    merged = merge_plans(existing, build_plan(vol1()))

    kept = merged.tracks[0]
    assert kept.video_id == t.video_id and kept.source_override == OTHER
    assert kept.in_source is True  # the *playlist's* video is still in the playlist
    assert kept.channel == "Kupfergold Records" and kept.duration == 184.0
    assert kept.provenance["source"] == Provenance.USER


def test_a_track_without_an_override_still_follows_the_playlists_video():
    existing = build_plan(vol1())
    existing.tracks[0].channel, existing.tracks[0].duration = "stale", 1.0
    merged = merge_plans(existing, build_plan(vol1()))
    fresh = build_plan(vol1()).tracks[0]
    assert merged.tracks[0].channel == fresh.channel and merged.tracks[0].duration == fresh.duration


# -- the words beside it --------------------------------------------------------------------


def test_the_panel_says_when_the_timings_were_written_for_another_file(tmp_path):
    plan = build_plan(vol1())
    t = plan.tracks[0]
    t.filename, t.state, t.file_length, t.lyrics = "01 a - b.opus", "done", 207.0, "synced"
    write_sidecar(tmp_path, t, "[00:12.30] words")

    assert t.lyrics_for_source == t.video_id and t.lyrics_for_length == 207.0
    assert timings_stale(t) is None

    switch_source(t, OTHER)
    t.lyrics, t.file_length = "synced", 184.0  # the new recording, measured after the download

    stale = timings_stale(t)
    assert stale == {"source": t.video_id, "was": 207.0, "now": 184.0}

    write_sidecar(tmp_path, t, "[00:10.10] words")  # saving them says they are for this one
    assert timings_stale(t) is None


def test_plain_words_and_old_sidecars_raise_nothing(tmp_path):
    plan = build_plan(vol1())
    t = plan.tracks[0]
    t.filename, t.file_length = "01 a - b.opus", 207.0
    t.lyrics = "plain"
    write_sidecar(tmp_path, t, "words with no timestamps")
    switch_source(t, OTHER)
    t.lyrics, t.file_length = "plain", 184.0
    assert timings_stale(t) is None  # nothing in them can point at a wrong second

    t.lyrics, t.lyrics_for_source, t.lyrics_for_length = "synced", None, None  # written before we recorded it
    assert timings_stale(t) is None


def test_a_file_measured_twice_is_not_another_file(tmp_path):
    plan = build_plan(vol1())
    t = plan.tracks[0]
    t.filename, t.file_length, t.lyrics = "01 a - b.opus", 207.0, "synced"
    write_sidecar(tmp_path, t, "[00:12.30] words")
    t.file_length = 207.4
    assert timings_stale(t) is None
