"""A cut file's header says how long the file is (DESIGN §9, slice 190; q14, corrected in I-477).

`Apocalyptica/Apocalyptica/04 Bittersweet` was cut to 205.90 s by a version that stream-copied FLAC:
its audio is 205.92 s and its STREAMINFO still says the original's 208.18 s. Every reader believed
the header — the plan's `file_length` too, and I-476, which called the cut missing.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from mutagen.flac import FLAC
from test_incremental import vol1

from noaap.download import needs_a_recut, run, would_do
from noaap.plan import build_plan
from noaap.trim import apply, signature


def decoded(path) -> float:
    rate = FLAC(path).info.sample_rate
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a", "-ac", "1", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return len(raw) / 2 / rate


def header(path) -> float:
    return FLAC(path).info.length


@pytest.fixture
def flac(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    path = tmp_path / "tone.flac"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=10:sample_rate=48000",
                    "-ac", "2", "-c:a", "flac", str(path)], check=True)
    return path


def cut_the_old_way(tmp_path, flac):
    """What a version before slice 166 wrote: a stream copy, which keeps the original's header."""
    plan = build_plan(vol1())
    track = plan.tracks[0]
    track.ext, track.filename, track.state = "flac", "t.flac", "done"
    for other in plan.tracks[1:]:
        other.state = "pending"
    here = tmp_path / "album" / track.filename
    here.parent.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(flac), "-ss", "0", "-to", "7", "-c", "copy",
                    "-avoid_negative_ts", "make_zero", str(here)], check=True)
    track.trim_end = 7.0
    track.trimmed = signature(track)
    assert header(here) > 9.9 and decoded(here) < 7.1, "the fixture is the shape this is about"
    return plan, track, here


def test_a_flac_cut_today_has_a_true_header(tmp_path, flac):
    """The cutter itself (slice 166 already encodes a FLAC cut again): pinned so it stays so."""
    plan = build_plan(vol1())
    track = plan.tracks[0]
    track.ext, track.filename = "flac", "t.flac"
    path = tmp_path / "t.flac"
    shutil.copy(flac, path)
    track.trim_start, track.trim_end = 2.0, 7.0

    assert apply(tmp_path, track, path) is True

    assert abs(header(path) - decoded(path)) < 0.01


def test_the_check_says_the_file_and_its_header_disagree(tmp_path, flac):
    plan, track, here = cut_the_old_way(tmp_path, flac)

    said = would_do(plan, here.parent)

    assert "01 the file holds 7.0 s, its header says 10.0 s — the header will be rewritten" in said, said


def test_the_repair_skip_does_not_pass_it_by(tmp_path, flac):
    plan, track, here = cut_the_old_way(tmp_path, flac)

    assert needs_a_recut(plan, here.parent) == [track]


def test_the_apply_rewrites_the_header_and_not_a_sample(tmp_path, flac):
    from noaap.trim import pcm_digest

    plan, track, here = cut_the_old_way(tmp_path, flac)
    sound = pcm_digest(here)

    run(plan, here.parent, source=None, download=False)
    here = here.parent / track.filename   # the pass also names it as the library does

    assert abs(header(here) - decoded(here)) < 0.01, (header(here), decoded(here))
    assert pcm_digest(here) == sound, "the same samples, only the header told the truth"
    assert abs(track.file_length - 7.0) < 0.05, "and the plan's length follows"
    assert track.trimmed == "0.00-7.00" and not track.error
    assert not [line for line in would_do(plan, here.parent) if "header" in line]


def test_a_rewrite_that_would_change_the_sound_is_not_kept(tmp_path, flac, monkeypatch, caplog):
    from noaap import trim

    plan, track, here = cut_the_old_way(tmp_path, flac)
    before = here.read_bytes()
    monkeypatch.setattr(trim, "pcm_digest", lambda path: str(path))   # every file sounds different

    with caplog.at_level("WARNING"):
        run(plan, here.parent, source=None, download=False)
    here = here.parent / track.filename

    assert here.read_bytes()[:4] == before[:4] and header(here) > 9.9, "the old file stays"
    assert "header" in caplog.text
