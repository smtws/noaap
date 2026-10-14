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
        return [{"id": "fits-the-video", "title": "Lullaby", "length": 268_000, "score": 100,
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
    one.title, one.mbid, one.mb_length, one.duration = "Lullaby", "e5332cbe", 315.5, 270.0   # vol1's video
    one.provenance["title"] = Provenance.MB
    save_plan(plan, album_dir)
    mb = Answering()
    said: list[str] = []
    Service(Config(musicbrainz=True, lyrics=False), lib, log=said.append, yt=FakeYouTube(opus_template),
            mb=mb).update_all(report_only=True, deep=True)
    assert "Lullaby" in mb.asked, mb.asked
    assert [s for s in said if "e5332cbe" in s and "the lookup gives fits-the-video (268.0 s)" in s], said


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


# -- a re-ask attaches only what fits (q29c, R-706) -------------------------------------------------


def a_reasked_track(video_s=316.0, title="Ruins"):
    t = build_plan(vol1()).tracks[0]
    t.artist, t.title, t.duration, t.mbid, t.mb_length = "Lord of the Lost", title, video_s, None, None
    t.ask_again = True
    return t


class Ruins:
    """Thornstar 13, as MusicBrainz answered on 2026-10-14: the take of 274 s, and others."""

    def __init__(self, *answers):
        self.answers = answers

    def search_recordings(self, artist, title):
        return [{"id": rid, "title": rtitle, "length": length, "score": 100,
                 "artist-credit": [{"name": artist, "joinphrase": "", "artist": {"id": "a", "name": artist}}],
                 "releases": [{"title": "Thornstar", "status": "Official", "release-group": {"primary-type": "Album"}}]}
                for rid, rtitle, length, *_ in self.answers]


def test_a_shorter_take_is_no_answer_and_changes_no_title():
    t = a_reasked_track()
    from noaap.enrich import enrich_track
    assert not enrich_track(t, Ruins(("7e54b224", "Ruins (Alternative Version)", 274_000)))
    assert (t.mbid, t.mb_length, t.title, t.artist) == (None, None, "Ruins", "Lord of the Lost")


def test_a_recording_of_no_known_length_is_no_answer_on_a_re_ask():
    t = a_reasked_track(242.0, "Die Hörner hoch")
    from noaap.enrich import enrich_track
    assert not enrich_track(t, Ruins(("e1c99981", "Die Hörner Hoch", None)))
    assert (t.mbid, t.title) == (None, "Die Hörner hoch")


def test_a_fitting_recording_is_attached_either_side_and_the_title_kept():
    from noaap.enrich import enrich_track
    for length in (312_000, 320_000):
        t = a_reasked_track()
        assert enrich_track(t, Ruins(("7e54b224", "Ruins (Alternative Version)", 274_000), ("fits", "Ruins", length)))
        assert (t.mbid, t.mb_length, t.title) == ("fits", length / 1000, "Ruins")


# -- and not another version of the song (q29d, R-709) ----------------------------------------------


class Versions(Ruins):
    def search_recordings(self, artist, title):
        out = super().search_recordings(artist, title)
        for r, (_, _, _, dis) in zip(out, self.answers, strict=True):
            r["disambiguation"] = dis
        return out


def test_a_live_bootleg_of_the_right_length_is_refused():
    """Evanescence 02: e408cf50, `live, 2006-10-10: The Electric Factory, Philadelphia`, 252 s for 254."""
    from noaap.enrich import enrich_track
    t = a_reasked_track(254.0, "Bring Me to Life")
    answer = ("e408cf50", "Bring Me to Life", 252_000, "live, 2006-10-10: The Electric Factory, Philadelphia")
    assert not enrich_track(t, Versions(answer), album="Fallen (Deluxe Edition / Remastered 2023)")
    assert t.mbid is None


def test_live_on_a_live_album_passes():
    """Powerwolf — Wildlive (Live at Olympiahalle) 11: f31146f0, the Olympiahalle recording."""
    from noaap.enrich import enrich_track
    t = a_reasked_track(244.0, "Stossgebet")
    answer = ("f31146f0", "Stossgebet", 245_176, "live, 2024-10-25: Olympiahalle, Munich, Germany")
    assert enrich_track(t, Versions(answer), album="Wildlive (Live at Olympiahalle)")
    assert t.mbid == "f31146f0"


def test_live_in_the_recording_s_own_title_is_refused_too():
    from noaap.enrich import enrich_track
    t = a_reasked_track(242.0, "Die Hörner hoch")
    assert not enrich_track(t, Versions(("ca7b0922", "Die Hörner hoch (live)", 237_000, "")), album="Best Of")


def test_a_studio_take_on_a_live_album_is_refused():
    """R-710: `Wildlive (Live at Olympiahalle)` 09 `1589` (246 s) was offered 857ecab7, the studio take
    of `Wake Up the Wicked` (244.8 s, no disambiguation) — the rule is symmetric."""
    from noaap.enrich import enrich_track
    t = a_reasked_track(246.0, "1589")
    assert not enrich_track(t, Versions(("857ecab7", "1589", 244_781, "")), album="Wildlive (Live at Olympiahalle)")
    assert t.mbid is None
