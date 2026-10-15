"""Words timed for audio a track no longer has are set aside, never embedded (§9, slice 208; q32, R-721).

Round I (2026-10-15) switched `My Dark Lullabies — Ghost Tears` from the curator's 243-s edit to the
254-s album version. The next check offered `lyrics nothing → "[01:07.3] So long in fear…"` — the
edit's `.lrc`, embedded into the other audio with the edit's times.
"""

from __future__ import annotations

from test_incremental import FakeYouTube, opus_template
from test_same_slot import album

from noaap.download import load_plan, run, save_plan, would_do
from noaap.lyrics import sidecar_path
from noaap.service import switch_source
from noaap.tag import tags_in

WORDS = "[01:07.3] So long in fear\n[01:10.1] I have gazed\n"


def with_a_sidecar(album_dir):
    plan = load_plan(album_dir)
    t = plan.tracks[0]
    sidecar_path(album_dir, t.filename).write_text(WORDS, encoding="utf-8")
    t.lyrics = "synced"
    save_plan(plan, album_dir)
    return plan, t


def lines_for(said, t):
    return [s for s in said if s.startswith(f"{t.number:02d} ")]


def test_a_switched_track_s_sidecar_is_set_aside_and_never_embedded(album, opus_template):
    _, album_dir = album
    plan, t = with_a_sidecar(album_dir)
    old = t.video_id
    switch_source(t, "ALBUMVERSN1")
    save_plan(plan, album_dir)
    run(plan, album_dir, FakeYouTube(opus_template))          # the fetch of the new audio
    side = sidecar_path(album_dir, t.filename)
    assert not side.exists() and side.with_name(f"{side.name}.{old}").read_text(encoding="utf-8") == WORDS
    assert "lyrics" not in tags_in(album_dir / t.filename)
    after = next(x for x in load_plan(album_dir).tracks if x.video_id == old)
    assert (after.sidecar_timed_for, after.lyrics) == (None, None), "and a lyrics pass asks again"


def test_the_check_says_set_aside_and_never_lyrics_nothing(album, opus_template):
    """Round I's 8 tracks: switched before `sidecar_timed_for` existed, fetched with no lyrics — the
    status reset and the sidecar still there."""
    _, album_dir = album
    plan, t = with_a_sidecar(album_dir)
    t.source_override, t.lyrics, t.sidecar_timed_for = "ALBUMVERSN1", None, None
    save_plan(plan, album_dir)
    said = lines_for(would_do(plan, album_dir), t)
    assert f"{t.number:02d} lyrics set aside — timed for {t.video_id}" in said, said
    assert not [s for s in said if "lyrics nothing" in s or "So long in fear" in s], said


def test_a_sidecar_for_the_audio_the_track_has_is_left_alone(album, opus_template):
    _, album_dir = album
    plan, t = with_a_sidecar(album_dir)
    assert not [s for s in would_do(plan, album_dir) if "set aside" in s]
    run(plan, album_dir, FakeYouTube(opus_template), download=False)
    assert sidecar_path(album_dir, t.filename).exists()


def test_switching_twice_remembers_the_audio_the_words_were_timed_for(album):
    _, album_dir = album
    plan, t = with_a_sidecar(album_dir)
    old = t.video_id
    switch_source(t, "FIRSTOTHER1")
    switch_source(t, "SECONDOTHR2")
    assert t.sidecar_timed_for == old
