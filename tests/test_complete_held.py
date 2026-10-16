"""A track `complete` fetched stays the album's while the album is pinned to that release (§9,
slice 211; q34, R-745), and an extra on the release's seat goes behind the release's tracks (F14).

The live case: `Limp Bizkit — Results May Vary`, pinned to `bc4de5d9`. The playlist has 17 of the
release's 18 and `Sam Rivers Forever`, which the release does not; `complete` fetched `Drown` for 18.
The next update would have called `Drown` "no longer in the source" and moved it to 20.
"""

from test_complete import FakeMB, Finder, album, service_for
from test_incremental import FakeYouTube, opus_template

from noaap.config import Config
from noaap.download import load_plan, save_plan
from noaap.models import Provenance
from noaap.service import Service


class PinnedMB(FakeMB):
    """MusicBrainz as far as the pinned release; every other question has no answer."""

    def __getattr__(self, name):
        return lambda *a, **k: []


def release_without_the_last(plan, gone="Drown"):
    """The album's own tracks but its last, which the release does not have; `gone` on that seat."""
    *ours, extra = plan.tracks
    tracks = [{"position": t.number, "title": t.title, "length": 200_000,
               "recording": {"id": f"rec-{t.number}", "title": t.title}} for t in ours]
    tracks.append({"position": extra.number, "title": gone, "length": 231_000,
                   "recording": {"id": "rec-drown", "title": gone}})
    return {"id": "rel-1", "title": plan.album, "media": [{"position": 1, "format": "CD", "tracks": tracks}]}, extra


def drown_hit(plan):
    return {"Drown": [{"ref": "drown", "title": f"{plan.albumartist} - Drown", "channel": "a fan", "length": 231}]}


def completed(lib, album_dir, plan, template):
    release, extra = release_without_the_last(plan)
    out = service_for(lib, Finder(template, drown_hit(plan)), release).complete(plan.source_id, dry_run=False)
    assert out.status == "ok", out.message
    return release, extra


def update_dry(lib, template, release):
    said: list[str] = []
    Service(Config(musicbrainz=True, lyrics=False), lib, log=said.append, yt=FakeYouTube(template),
            mb=PinnedMB(release)).update_all(report_only=True, deep=True)
    return said


def test_the_dry_says_the_extra_goes_behind_the_release(album, opus_template):
    lib, album_dir, plan = album
    release, extra = release_without_the_last(plan)
    n = extra.number
    out = service_for(lib, Finder(opus_template, drown_hit(plan)), release).complete(plan.source_id)
    assert out.status == "dry"
    assert (f"extra: 1-{n:02d} {extra.title}: the release's seat of Drown — goes behind its tracks, "
            f"to 1-{n + 1:02d}") in out.changes, out.changes
    assert not [c for c in out.changes if "left as it is" in c], out.changes


def test_the_apply_seats_the_completed_track_and_moves_the_extra(album, opus_template):
    lib, album_dir, plan = album
    release, extra = completed(lib, album_dir, plan, opus_template)
    after = load_plan(album_dir)
    drown = next(t for t in after.tracks if t.video_id == "drown")
    moved = next(t for t in after.tracks if t.video_id == extra.video_id)
    assert (drown.number, drown.completed_from, drown.state) == (extra.number, "rel-1", "done")
    assert moved.number == extra.number + 1
    assert (album_dir / moved.filename).exists() and not (album_dir / extra.filename).exists()
    assert len({(t.disc, t.number) for t in after.tracks}) == len(after.tracks), "two files on one number"


def test_the_next_update_keeps_the_completed_track_where_it_is(album, opus_template):
    lib, album_dir, plan = album
    release, extra = completed(lib, album_dir, plan, opus_template)
    said = update_dry(lib, opus_template, release)
    assert [s for s in said if "0 new, 0 no longer in the source" in s], said
    assert not [s for s in said if "Drown" in s or "would be renamed" in s], said


def test_prune_does_not_offer_the_completed_track(album, opus_template):
    lib, album_dir, plan = album
    release, extra = completed(lib, album_dir, plan, opus_template)
    Service(Config(musicbrainz=True, lyrics=False), lib, log=lambda s: None, yt=FakeYouTube(opus_template),
            mb=PinnedMB(release)).update_all(deep=True)
    after = load_plan(album_dir)
    drown = next(t for t in after.tracks if t.video_id == "drown")
    assert (drown.in_source, drown.number) == (True, extra.number)
    out = Service(Config(musicbrainz=False, lyrics=False), lib, log=lambda s: None).prune(album_dir)
    assert out.status == "ok" and (album_dir / drown.filename).exists()


def test_without_the_pin_the_old_rule_holds(album, opus_template):
    lib, album_dir, plan = album
    release, extra = completed(lib, album_dir, plan, opus_template)
    unpinned = load_plan(album_dir)
    unpinned.provenance["mbid"] = Provenance.MB
    save_plan(unpinned, album_dir)
    said = update_dry(lib, opus_template, release)
    assert [s for s in said if s.strip().endswith("Drown: no longer in the source")], said
