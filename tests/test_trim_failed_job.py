"""A save whose trim could not be applied is a failed job, not a done one (§9, slice 167; R-576 item 2).

The P120 walk reset a trim on an mp3, the trim was refused — "trim failed" on the track, a warning in
the log — and the job said done, the toast said ✓, and the file stayed cut."""

from __future__ import annotations

from test_identify import collection, settled, the_album

from noaap.config import Config
from noaap.download import load_plan, save_plan
from noaap.web import App


def an_app(root):
    cfg = Config(library_root=root, musicbrainz=False, lyrics=False)
    return App(cfg, root, port=0)        # the default service: the one that reports each track's step


def a_cut_without_its_original(app):
    """A track whose file is cut and whose untouched original is not kept: nothing can be cut again."""
    source_id = the_album(app)
    album_dir, _ = app.album(source_id)
    plan = load_plan(album_dir)
    track = plan.tracks[0]
    track.trim_start, track.trimmed = 0.1, "0.10-"
    save_plan(plan, album_dir)
    return source_id, track


def test_a_refused_trim_fails_the_job_and_says_so(collection):
    app = an_app(collection)
    source_id, track = a_cut_without_its_original(app)

    job = settled(app, app.submit("edit", {"id": source_id, "edits": {"tracks": [
        {"video_id": track.video_id, "trim_start": "0.3"}]}}))

    assert job.state == "failed", job.log
    assert any(line.startswith("trim failed:") for line in job.log)
    assert job.log[-1] == "1 trim(s) could not be applied — each track says why"


def test_a_save_without_a_trim_is_still_done(collection):
    app = an_app(collection)
    source_id = the_album(app)
    plan = load_plan(app.album(source_id)[0])

    job = settled(app, app.submit("edit", {"id": source_id, "edits": {"tracks": [
        {"video_id": plan.tracks[0].video_id, "title": "Renamed"}]}}))

    assert job.state == "done", job.log
    assert job.trims_failed == 0
