"""A kept original belongs to its track wherever the track's id goes (§9, slice 168; R-578).

The P120 walk re-pointed a folder track from its old file name to its new one, the original stayed
under a key made of the old whole path, and the cut file could never be put back (finding 10)."""

import shutil
import subprocess

import pytest
from test_incremental import vol1
from test_trim import a_tone

from noaap.download import point_at
from noaap.plan import build_plan
from noaap.trim import ORIGINALS, apply, key, original_path, re_own, ref_key


def a_folder_track(album, tone, name="01 - Song.mp3"):
    plan = build_plan(vol1())
    plan.tracks = plan.tracks[:1]
    plan.provider = "folder"
    track = plan.tracks[0]
    shutil.copy(tone, album / name)
    track.video_id, track.filename, track.ext, track.state = str(album / name), name, "mp3", "done"
    track.candidates = []
    track.sync_candidates()
    return plan, track


def test_a_folder_ref_is_keyed_inside_its_album(tmp_path):
    album = tmp_path / "A" / "B"
    assert ref_key(str(album / "CD1" / "01 x.mp3"), album) == ref_key("./CD1/01 x.mp3", album)
    assert ref_key(str(album / "CD1" / "01 x.mp3"), album) != ref_key(str(album / "CD2" / "01 x.mp3"), album)
    assert ref_key("dQw4w9WgXcQ", album) == "dQw4w9WgXcQ", "a YouTube id is keyed as it always was"
    moved = tmp_path / "elsewhere" / "B"
    assert ref_key(str(album / "01 x.mp3"), album) == ref_key(str(moved / "01 x.mp3"), moved), \
        "a library moved or remounted keeps every original"


def test_a_re_pointed_track_takes_its_original_along(tmp_path):
    tone = a_tone(tmp_path, "mp3")
    album = tmp_path / "lib" / "A" / "B"
    album.mkdir(parents=True)
    plan, track = a_folder_track(album, tone, "01 - Song Title.mp3")
    before = (album / track.filename).read_bytes()
    track.trim_start = 2.0
    apply(album, track, album / track.filename)

    # the file is renamed on disk (a rename pass), and the plan is re-pointed at it
    (album / track.filename).rename(album / "01 - Song title.mp3")
    track.filename = "01 - Song title.mp3"
    assert point_at(plan, album)
    assert original_path(album, track).exists(), "the original follows the id"

    track.trim_start = None
    assert apply(album, track, album / track.filename) is True
    assert (album / track.filename).read_bytes() == before


def test_an_original_kept_under_the_whole_path_is_adopted(tmp_path):
    tone = a_tone(tmp_path, "mp3")
    album = tmp_path / "A" / "B"
    album.mkdir(parents=True)
    _, track = a_folder_track(album, tone)
    before = (album / track.filename).read_bytes()
    (album / ORIGINALS).mkdir()
    legacy = album / ORIGINALS / f"{key(track.video_id)}.mp3"     # how 1.51 named it
    shutil.copy(tone, legacy)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tone), "-ss", "2", "-c", "copy",
                    str(album / track.filename)], check=True)
    track.trimmed = "2.00-"

    assert apply(album, track, album / track.filename) is True     # the reset
    assert (album / track.filename).read_bytes() == before
    assert not legacy.exists()


def cut_with_an_orphan(tmp_path, ext="mp3"):
    tone = a_tone(tmp_path, ext)
    album = tmp_path / "A" / "B"
    album.mkdir(parents=True)
    plan, track = a_folder_track(album, tone, f"01 - Song.{ext}")
    track.ext = ext
    track.trim_start = 2.0
    apply(album, track, album / track.filename)
    orphan = original_path(album, track).rename(album / ORIGINALS / f"ref-0000000000000000.{ext}")
    return plan, track, album, orphan, tone


@pytest.mark.parametrize("ext", ["mp3", "flac"])
def test_repair_takes_an_orphan_back_where_one_cut_is_its_own(tmp_path, ext):
    plan, track, album, orphan, tone = cut_with_an_orphan(tmp_path, ext)

    said = re_own(album, plan.tracks, apply=False)
    assert said == [f"01 would take back its kept original {orphan.name} — the file is that original cut at 2.00-"]
    assert orphan.exists(), "the check moves nothing"

    re_own(album, plan.tracks, apply=True)
    assert original_path(album, track).exists() and not orphan.exists()
    track.trim_start = None
    apply(album, track, album / track.filename)
    assert (album / track.filename).read_bytes() == tone.read_bytes()


def test_an_orphan_nothing_was_cut_from_is_named_and_left(tmp_path):
    plan, track, album, orphan, _ = cut_with_an_orphan(tmp_path)
    stranger = album / ORIGINALS / "ref-1111111111111111.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=10",
                    "-c:a", "libmp3lame", str(stranger)], check=True)

    said = re_own(album, plan.tracks, apply=True)
    assert "kept original without a track: ref-1111111111111111.mp3" in said
    assert stranger.exists()
    assert original_path(album, track).exists(), "the one that is its cut was still taken back"
