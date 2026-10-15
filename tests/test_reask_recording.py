"""A stored recording id taken back and asked again by the file's length (§9, slice 210; q33, R-728).

Round I's shape: `Nightwish — Sleeping Sun` on a curator's volume, a 242-s file of the Oceanborn
studio take, stored as the 295-s live take of *End of an Era*. The volume is a compilation, so the
update's take-back (slice 206) leaves it alone (slice 208); `noaap recording --reask` is the door.
"""

from __future__ import annotations

from test_dry_recording_retag import tags_in
from test_incremental import FakeYouTube, opus_template
from test_same_slot import album

from noaap.config import Config
from noaap.download import load_plan, run, save_plan
from noaap.models import Kind
from noaap.service import Service

LIVE = "e9a9720c-live"


def rec(rid, title, seconds, disambiguation="", release="Oceanborn"):
    return {"id": rid, "title": title, "length": int(seconds * 1000), "score": 100,
            "disambiguation": disambiguation,
            "artist-credit": [{"name": "Nightwish", "joinphrase": "", "artist": {"id": "a", "name": "Nightwish"}}],
            "releases": [{"title": release, "status": "Official", "release-group": {"primary-type": "Album"}}]}


class Answering:
    """MusicBrainz as far as one recording search for `Sleeping Sun`."""

    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def search_recordings(self, artist, title):
        self.asked.append(title)
        return self.answers if title == "Sleeping Sun" else []

    def __getattr__(self, name):
        return lambda *a, **k: []


def sleeping_sun(album_dir, template, stored=LIVE, stored_s=295.2):
    plan = load_plan(album_dir)
    assert plan.kind == Kind.COMPILATION
    one = plan.tracks[0]
    one.artist, one.title, one.mbid, one.mb_length = "Nightwish", "Sleeping Sun", stored, stored_s
    run(plan, album_dir, FakeYouTube(template), download=False)   # the file says the stored id
    plan = load_plan(album_dir)
    plan.tracks[0].file_length = 242.0
    save_plan(plan, album_dir)
    return plan, plan.tracks[0]


def a_service(lib, template, mb, said):
    return Service(Config(musicbrainz=True, lyrics=False), lib, log=said.append, yt=FakeYouTube(template), mb=mb)


ANSWERS = (rec(LIVE, "Sleeping Sun", 295.2, "live, 2005-10-21: Hartwall Areena", "End of an Era"),
           rec("live-fits", "Sleeping Sun", 243.0, "live, 2000", "From Wishes to Eternity"),
           rec("f5b2b7f5-studio", "Sleeping Sun", 243.9))


def test_on_a_compilation_the_dry_names_the_old_and_the_new_recording_and_writes_nothing(album, opus_template):
    lib, album_dir = album
    plan, one = sleeping_sun(album_dir, opus_template)
    said: list[str] = []
    out = a_service(lib, opus_template, Answering(*ANSWERS), said).reask_recordings(plan.source_id, only={"1-01"})
    assert out.status == "dry"
    assert ("1-01 Sleeping Sun (file 242 s): e9a9720c (Sleeping Sun [live, 2005-10-21: Hartwall Areena], 295 s, "
            "End of an Era) → f5b2b7f5 (Sleeping Sun, 244 s, Oceanborn)") in said, said
    after = load_plan(album_dir).tracks[0]
    assert (after.mbid, after.refused_recordings) == (LIVE, [])
    assert tags_in(album_dir / after.filename)["musicbrainz_trackid"] == LIVE


def test_the_apply_refuses_the_stored_id_sets_the_fitting_one_and_retags(album, opus_template):
    lib, album_dir = album
    plan, one = sleeping_sun(album_dir, opus_template)
    out = a_service(lib, opus_template, Answering(*ANSWERS), []).reask_recordings(
        plan.source_id, only={"1-01"}, dry_run=False)
    assert out.status == "ok"
    after = load_plan(album_dir).tracks[0]
    assert (after.mbid, after.mb_length, after.refused_recordings) == ("f5b2b7f5-studio", 243.9, [LIVE])
    assert tags_in(album_dir / after.filename)["musicbrainz_trackid"] == "f5b2b7f5-studio"


def test_without_only_the_tracks_whose_recording_is_longer_than_the_file_are_asked(album, opus_template):
    lib, album_dir = album
    plan, one = sleeping_sun(album_dir, opus_template)
    mb = Answering(*ANSWERS)
    a_service(lib, opus_template, mb, []).reask_recordings(plan.source_id)
    assert mb.asked == ["Sleeping Sun"], "the other tracks have no recording longer than their file"


def test_a_stored_recording_that_fits_the_file_is_left_and_says_so(album, opus_template):
    lib, album_dir = album
    plan, one = sleeping_sun(album_dir, opus_template, stored="f5b2b7f5-studio", stored_s=243.9)
    said: list[str] = []
    mb = Answering(*ANSWERS)
    a_service(lib, opus_template, mb, said).reask_recordings(plan.source_id, only={"1-01"}, dry_run=False)
    assert "1-01 Sleeping Sun: f5b2b7f5 (244 s) already fits the file (242 s) — left" in said, said
    assert mb.asked == []
    after = load_plan(album_dir).tracks[0]
    assert (after.mbid, after.refused_recordings) == ("f5b2b7f5-studio", [])


def test_where_nothing_fits_the_track_keeps_no_id_and_the_file_drops_it(album, opus_template):
    lib, album_dir = album
    plan, one = sleeping_sun(album_dir, opus_template)
    said: list[str] = []
    out = a_service(lib, opus_template, Answering(*ANSWERS[:2]), said).reask_recordings(
        plan.source_id, only={"1-01"}, dry_run=False)
    assert out.status == "ok"
    assert [s for s in said if s.startswith("1-01 Sleeping Sun (file 242 s): e9a9720c (") and s.endswith(
        "→ — nothing fits")], said
    after = load_plan(album_dir).tracks[0]
    assert (after.mbid, after.mb_length, after.refused_recordings) == (None, None, [LIVE])
    assert "musicbrainz_trackid" not in tags_in(album_dir / after.filename)


def test_a_track_the_album_does_not_have_changes_nothing(album, opus_template):
    lib, album_dir = album
    plan, one = sleeping_sun(album_dir, opus_template)
    out = a_service(lib, opus_template, Answering(*ANSWERS), []).reask_recordings(
        plan.source_id, only={"1-01", "9-99"}, dry_run=False)
    assert out.status == "failed" and "9-99" in out.message
    assert load_plan(album_dir).tracks[0].mbid == LIVE
