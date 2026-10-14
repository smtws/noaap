"""A stored per-track recording longer than its video is cleared at the next read (§9, slice 206; q29,
R-698).

`Feuerschwanz — Sex Is Muss` 1-01 `Ketzerei`, a 100-s video, held `e5332cbe` (215.5 s) from a lookup
before slice 204; the gated lookup now gives `b259ce05` of no known length, and the merge kept the
stored id against it.
"""

from __future__ import annotations

from test_dry_recording_retag import recorded_as
from test_incremental import FakeYouTube, opus_template, vol1
from test_same_slot import album

from noaap.download import load_plan, run, save_plan, would_do
from noaap.plan import build_plan, changes_from, merge_plans
from noaap.tag import tag_file, tags_in


def stored_and_fresh(fresh_mbid=None, fresh_len=None, stored_len=215.5, album_mbid=None, override=None,
                     asked=False):
    stored = build_plan(vol1())
    for t in stored.tracks:
        t.state = "done"
    stored.mbid = album_mbid
    one = stored.tracks[0]
    one.mbid, one.mb_length, one.duration, one.source_override = "e5332cbe", stored_len, 100.0, override
    fresh = build_plan(vol1())
    fresh.mbid = album_mbid
    f = next(t for t in fresh.tracks if t.video_id == one.video_id)
    f.mbid, f.mb_length, f.duration = fresh_mbid, fresh_len, 100.0
    if asked:
        f.recording_asked = True
    return stored, merge_plans(stored, fresh), one.video_id


def track(plan, vid):
    return next(t for t in plan.tracks if t.video_id == vid)


def test_the_too_long_id_goes_and_the_gated_lookup_s_answer_is_taken():
    stored, merged, vid = stored_and_fresh(fresh_mbid="b259ce05")
    assert (track(merged, vid).mbid, track(merged, vid).mb_length) == ("b259ce05", None)
    lines = [s for s in changes_from(stored, merged) if "e5332cbe" in s]
    assert lines == [f"01 {track(merged, vid).title}: the recording id e5332cbe (215.5 s) is longer than "
                     "the video (100 s) and goes — the lookup gives b259ce05 (of no known length)"], lines


def test_where_the_lookup_gives_none_the_track_has_none():
    stored, merged, vid = stored_and_fresh(asked=True)
    assert (track(merged, vid).mbid, track(merged, vid).mb_length) == (None, None)
    assert any("e5332cbe (215.5 s) is longer than the video (100 s) and goes — the lookup gives none" in s
               for s in changes_from(stored, merged))


def test_a_track_nobody_asked_about_is_said_to_be_not_asked():
    """R-703: "the lookup gives none" of a lookup that never ran was round H's first dry, 16 times."""
    stored, merged, vid = stored_and_fresh()
    lines = [s for s in changes_from(stored, merged) if "e5332cbe" in s]
    assert lines and lines[0].endswith("and goes — not asked"), lines


def test_a_stored_id_within_the_slack_stays():
    stored, merged, vid = stored_and_fresh(stored_len=104.0)
    assert track(merged, vid).mbid == "e5332cbe"


def test_an_album_seated_on_a_release_is_not_asked():
    stored, merged, vid = stored_and_fresh(album_mbid="a-release")
    assert track(merged, vid).mbid == "e5332cbe"


def test_a_track_whose_audio_is_another_video_s_is_not_asked():
    stored, merged, vid = stored_and_fresh(override="ANOTHERVID1")
    assert track(merged, vid).mbid == "e5332cbe"


# -- the files (R-699, R-700) ------------------------------------------------------------------------


def test_the_check_says_the_id_the_apply_drops_from_noaap_s_own_album(album, opus_template):
    """R-700: the writer deletes every key and writes the plan's, so a plan with no recording id
    drops the file's — and the check said nothing about it."""
    _, album_dir = album
    plan = recorded_as(album_dir, opus_template, "e5332cbe")
    one = plan.tracks[0]
    one.mbid = None
    save_plan(plan, album_dir)

    said = would_do(plan, album_dir)

    assert [s for s in said if s.startswith(f"{one.number:02d} would be retagged:")
            and "musicbrainz_trackid would be dropped" in s], said
    run(plan, album_dir, FakeYouTube(opus_template), download=False)
    assert "musicbrainz_trackid" not in tags_in(album_dir / one.filename), "what the apply then does"
    assert not would_do(load_plan(album_dir), album_dir)


def test_an_adopted_album_drops_a_refused_id_and_keeps_another(album, opus_template):
    _, album_dir = album
    plan = recorded_as(album_dir, opus_template, "e5332cbe")
    plan.adopted = True
    one, two = plan.tracks[0], plan.tracks[1]
    one.mbid, one.refused_recordings = None, ["e5332cbe"]
    two.mbid = None                                    # not refused: the file's id is the file's
    save_plan(plan, album_dir)
    tag_file(album_dir / two.filename, plan, two, keep_unknown=True)  # give 02 an id of its own first
    said = would_do(plan, album_dir)
    assert [s for s in said if s.startswith(f"{one.number:02d} would be retagged:")
            and "musicbrainz_trackid would be dropped" in s], said
    assert not [s for s in said if s.startswith(f"{two.number:02d} ") and "musicbrainz_trackid" in s], said
    run(plan, album_dir, FakeYouTube(opus_template), download=False)
    assert "musicbrainz_trackid" not in tags_in(album_dir / one.filename)


def test_a_refused_id_is_never_attached_again():
    """Methämmer 1-03: the search answers the same id with no length, which the gate lets through."""
    stored, merged, vid = stored_and_fresh(fresh_mbid="e5332cbe")
    t = track(merged, vid)
    assert (t.mbid, t.mb_length, t.refused_recordings) == (None, None, ["e5332cbe"])
    fresh_again = build_plan(vol1())
    f = next(x for x in fresh_again.tracks if x.video_id == vid)
    f.mbid, f.duration = "e5332cbe", 100.0
    assert track(merge_plans(merged, fresh_again), vid).mbid is None


# -- the lookup is really asked (q29b, R-703) --------------------------------------------------------


class Answering:
    """MusicBrainz as far as one track's search: a recording of the video's length for `Lullaby`."""

    def __init__(self):
        self.asked = []

    def search_recordings(self, artist, title):
        self.asked.append(title)
        if title != "Lullaby":
            return []
        return [{"id": "fits-the-video", "title": "Lullaby", "length": 99_000, "score": 100,
                 "artist-credit": [{"name": artist, "joinphrase": "", "artist": {"id": "a", "name": artist}}],
                 "releases": []}]

    def __getattr__(self, name):
        return lambda *a, **k: []          # no release search answers


def test_a_taken_back_recording_is_asked_again_and_the_answer_attached(album, opus_template):
    """Sabaton's shape: the stored id was handed to the fresh read as the recording it already is, and
    a title MusicBrainz gave is not looked up — so 01 was never asked."""
    from noaap.config import Config
    from noaap.models import Provenance
    from noaap.service import Service

    lib, album_dir = album
    plan = load_plan(album_dir)
    one = plan.tracks[0]
    one.title, one.mbid, one.mb_length, one.duration = "Lullaby", "e5332cbe", 215.5, 100.0
    one.provenance["title"] = Provenance.MB
    save_plan(plan, album_dir)
    mb = Answering()
    said: list[str] = []
    Service(Config(musicbrainz=True, lyrics=False), lib, log=said.append, yt=FakeYouTube(opus_template),
            mb=mb).update_all(report_only=True, deep=True)
    assert "Lullaby" in mb.asked, mb.asked
    assert [s for s in said if "e5332cbe" in s and "the lookup gives fits-the-video (99.0 s)" in s], said


def test_a_taken_back_track_with_a_music_brainz_title_is_worth_asking():
    from noaap.enrich import _worth_looking_up
    from noaap.models import Kind, Provenance
    plan = build_plan(vol1())
    plan.kind = Kind.OFFICIAL_ALBUM
    t = plan.tracks[0]
    t.provenance = {"title": Provenance.MB, "artist": Provenance.MB}
    assert not _worth_looking_up(plan, t)
    t.ask_again = True
    assert _worth_looking_up(plan, t)
