"""A check names every track its apply would download (§9, slice 195; q20, R-656).

Round A's check of `Crematory/Crematory` (2026-10-13) said nothing about tracks: its plan held 07
`Flammen` and 08 `Die Suche` as failed, with no file, and the apply that followed said `downloading 2
of 10 tracks` and fetched both. A download is the one change a check must never leave out.
"""

from __future__ import annotations

from test_incremental import FakeYouTube, opus_template
from test_same_slot import album

from noaap.config import Config
from noaap.download import load_plan, save_plan
from noaap.service import Service


def crematory(album_dir, which=(1, 2)):
    """Two tracks failed, their files gone: the shape the round-A apply met."""
    plan = load_plan(album_dir)
    for t in plan.tracks:
        if t.number in which:
            (album_dir / t.filename).unlink()
            t.state, t.error = "failed", "HTTP Error 403"
    save_plan(plan, album_dir)
    return [t for t in plan.tracks if t.number in which]


def a_check(lib, yt):
    said: list[str] = []
    Service(Config(musicbrainz=False, lyrics=False), lib, yt=yt, log=said.append).update_all(
        report_only=True, deep=True)
    return said


def test_the_check_names_the_failed_tracks_it_would_download(album, opus_template):
    lib, album_dir = album
    failed = crematory(album_dir)
    yt = FakeYouTube(opus_template)

    said = a_check(lib, yt)

    line = "  2 track(s) would be downloaded: " + ", ".join(f"{t.number:02d} {t.title}" for t in failed)
    assert line in said, said
    assert [s for s in said if s.endswith(", and 2 track(s) would be downloaded")], said
    assert yt.downloads == [], "a check downloads nothing"


def test_the_apply_says_the_same_before_it_fetches_and_then_nothing_is_left(album, opus_template):
    lib, album_dir = album
    crematory(album_dir)
    yt = FakeYouTube(opus_template)
    checked = [s for s in a_check(lib, yt) if "would be downloaded:" in s]

    said: list[str] = []
    Service(Config(musicbrainz=False, lyrics=False), lib, yt=yt, log=said.append).update_all(deep=True)

    assert checked and checked[0] in said, said
    assert said.index(checked[0]) < next(i for i, s in enumerate(said) if s.startswith("downloading 2 of"))
    assert len(yt.downloads) == 2
    assert not [s for s in a_check(lib, FakeYouTube(opus_template)) if "would be downloaded" in s]


def test_a_finished_track_whose_file_is_gone_is_one_of_them(album, opus_template):
    lib, album_dir = album
    plan = load_plan(album_dir)
    gone = plan.tracks[2]
    (album_dir / gone.filename).unlink()

    said = a_check(lib, FakeYouTube(opus_template))

    assert f"  1 track(s) would be downloaded: {gone.number:02d} {gone.title}" in said, said


def test_an_album_with_every_file_says_no_download(album, opus_template):
    lib, _ = album
    assert not [s for s in a_check(lib, FakeYouTube(opus_template)) if "would be downloaded" in s]
