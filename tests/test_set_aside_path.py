"""Words timed for a taken-in file are set aside under a name that is one (§9, slice 215; q38a, R-763).

`Disturbed — Immortalized` 11 (F18, 1.94.0): a taken-in NAS file, its id
`./Disturbed - Immortalized - 11 - The Sound of Silence.opus`, switched in the UI to `u9Dg-g7t2l4`.
The save named the old sidecar `….lrc./mnt/…opus` and raised — after the new audio was in place, so
the plan stayed `pending` with no length, and every later save hit the same error.
"""

from __future__ import annotations

import pytest
from test_incremental import FakeYouTube, opus_template  # fixture
from test_same_slot import album  # fixture
from test_stale_sidecar import WORDS, with_a_sidecar

import noaap.download as download
from noaap.download import load_plan, run, save_plan
from noaap.lyrics import Lyrics, set_aside, sidecar_path
from noaap.service import switch_source

TAKEN_IN = "./Disturbed - Immortalized - 11 - The Sound of Silence.opus"


@pytest.mark.parametrize("was", [TAKEN_IN, "/mnt/speicherzwerg-nfs/c/Musik/Disturbed/Immortalized/11.opus"])
def test_a_path_is_set_aside_as_taken_in(album, was):
    _, album_dir = album
    _, t = with_a_sidecar(album_dir)
    side = sidecar_path(album_dir, t.filename)
    moved = set_aside(album_dir, t, was)
    assert moved == side.with_name(f"{side.name}.taken-in") and moved.read_text(encoding="utf-8") == WORDS
    assert not side.exists()


def test_a_second_taken_in_sidecar_does_not_overwrite_the_first(album):
    _, album_dir = album
    _, t = with_a_sidecar(album_dir)
    side = sidecar_path(album_dir, t.filename)
    side.with_name(f"{side.name}.taken-in").write_text("earlier\n", encoding="utf-8")
    assert set_aside(album_dir, t, TAKEN_IN).name == f"{side.name}.taken-in.2"
    assert side.with_name(f"{side.name}.taken-in").read_text(encoding="utf-8") == "earlier\n"


def fetched(album_dir, opus_template, said):
    plan = load_plan(album_dir)
    t = plan.tracks[0]
    t.source_override, t.sidecar_timed_for, t.state = "u9Dg-g7t2l4", TAKEN_IN, "pending"
    save_plan(plan, album_dir)
    run(plan, album_dir, FakeYouTube(opus_template), say=said.append)
    return next(x for x in load_plan(album_dir).tracks if x.video_id == t.video_id)


def test_the_save_of_a_switched_taken_in_track_reaches_done(album, opus_template):
    _, album_dir = album
    with_a_sidecar(album_dir)
    said = []
    after = fetched(album_dir, opus_template, said)
    assert (after.state, after.error) == ("done", None) and after.file_length
    side = sidecar_path(album_dir, after.filename)
    assert not side.exists() and side.with_name(f"{side.name}.taken-in").read_text(encoding="utf-8") == WORDS
    assert (after.sidecar_timed_for, after.lyrics) == (None, None)
    assert [s for s in said if s.startswith("01 lyrics set aside — timed for ./Disturbed")], said


def test_a_sidecar_that_cannot_be_moved_does_not_leave_the_track_pending(album, opus_template, monkeypatch):
    def refused(*_):
        raise OSError("Read-only file system")

    monkeypatch.setattr(download, "set_aside", refused)
    monkeypatch.setattr(download, "RETRY_DELAY", 0)
    _, album_dir = album
    with_a_sidecar(album_dir)
    said = []
    after = fetched(album_dir, opus_template, said)
    assert (after.state, after.error) == ("done", None) and after.file_length and after.tagged
    assert after.sidecar_timed_for.endswith(TAKEN_IN[2:]), "still known as stale, for the next pass"
    assert sidecar_path(album_dir, after.filename).read_text(encoding="utf-8") == WORDS
    assert [s for s in said if "could not be set aside: Read-only file system" in s], said


NEW_WORDS = "[00:12.0] Hello darkness\n[00:15.5] my old friend\n"


class Lrclib:
    def get(self, artist, title, album, length, skip=()):
        return Lyrics(synced=NEW_WORDS, lrclib_id=11)

    def by_id(self, lrclib_id):
        return Lyrics(synced=NEW_WORDS, lrclib_id=lrclib_id)


def test_words_written_for_the_new_audio_are_not_set_aside(album, opus_template):
    """R-763a: the user's second save — the re-download wrote the new audio's words a second before
    the audio was in place, and the same pass took them for the old file's."""
    _, album_dir = album
    plan, t = with_a_sidecar(album_dir)
    switch_source(t, "u9Dg-g7t2l4")
    t.sidecar_timed_for, t.state = TAKEN_IN, "pending"
    save_plan(plan, album_dir)
    said = []
    run(plan, album_dir, FakeYouTube(opus_template), lyrics=Lrclib(), say=said.append)
    after = next(x for x in load_plan(album_dir).tracks if x.video_id == t.video_id)
    assert (after.state, after.error, after.lyrics) == ("done", None, "synced")
    assert sidecar_path(album_dir, after.filename).read_text(encoding="utf-8") == NEW_WORDS
    assert after.sidecar_timed_for == "u9Dg-g7t2l4"
    side = sidecar_path(album_dir, after.filename)
    assert [p.name for p in album_dir.glob(f"{side.name}.*")] == [f"{side.name}.taken-in"], "the new words stay"


def test_the_old_words_are_kept_beside_the_new_ones(album, opus_template):
    """q38b, R-765: the fetch's lookup wrote the new words over the old ones before the stale check
    ran, so the old file's words were lost instead of set aside as `<name>.lrc.<id>`."""
    _, album_dir = album
    plan, t = with_a_sidecar(album_dir)
    old = t.video_id
    switch_source(t, "ALBUMVERSN1")
    save_plan(plan, album_dir)
    said = []
    run(plan, album_dir, FakeYouTube(opus_template), lyrics=Lrclib(), say=said.append)
    after = next(x for x in load_plan(album_dir).tracks if x.video_id == old)
    side = sidecar_path(album_dir, after.filename)
    assert side.with_name(f"{side.name}.{old}").read_text(encoding="utf-8") == WORDS
    assert side.read_text(encoding="utf-8") == NEW_WORDS
    assert (after.state, after.lyrics, after.sidecar_timed_for) == ("done", "synced", "ALBUMVERSN1")
    assert [s for s in said if s.startswith(f"01 lyrics set aside — timed for {old}")], said
