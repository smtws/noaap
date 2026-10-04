"""Windows Media's own library ids (DESIGN §9, slice 121; R-455, item 7).

`PRIV` frames whose owner begins `WM/` — `WMCollectionID`, `WMContentID`, `MediaClassPrimaryID` and
kin — are a media player's library ids, written into the file by something that is not this program
and meaning nothing outside it. One of them is also what stopped the collection's pass: its
description is raw binary and held a `\\x85`, which cut a snapshot record in two (slice 114).

The user, asked: *"i would, but not necessarily other users"* — so a setting, off by default, on the
same terms as `drop_comments`: only where the file is written at all, counted by the dry run first,
and recorded so a restore puts them back.
"""

from __future__ import annotations

import shutil

import pytest
from mutagen.id3 import ID3, PRIV, TALB, TIT2, TPE1, TPE2, TRCK
from test_collisions import a_service
from test_intake import QUIET

from noaap import intake, sources
from noaap.config import Config
from noaap.download import wm_frames_to_drop, would_do
from noaap.service import Service
from noaap.tag import raw_tags, restore_tags, tag_file, wm_frames_in
from noaap.treatment import Treatment, drops_wm_frames

THEIRS = [("WM/WMCollectionID", b"\xeb\x78\x44\x7b\x83"),
          ("WM/MediaClassPrimaryID", b"\xbc\x7d\x60\xd1"),
          ("PeakValue", b"\x01\x02")]        # not Windows Media's: somebody else's, and it stays


def an_mp3(folder, number, title, one_second_of_mp3, *, privs=THEIRS):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{number:02d} {title}.mp3"
    shutil.copy(one_second_of_mp3, path)
    tags = ID3()
    for frame in (TIT2(encoding=3, text=[title]), TPE1(encoding=3, text=["A Band"]),
                  TPE2(encoding=3, text=["A Band"]), TALB(encoding=3, text=["An Album"]),
                  TRCK(encoding=3, text=[str(number)])):
        tags.add(frame)
    for owner, data in privs:
        tags.add(PRIV(owner=owner, data=data))
    tags.save(path, v1=0)
    return path


@pytest.fixture
def album(tmp_path, one_second_of_mp3):
    root = tmp_path / "collection"
    where = root / "A Band" / "An Album"
    an_mp3(where, 1, "One", one_second_of_mp3)
    return root, where


def a_plan(root, where):
    source = sources.get("folder", Config(library_root=root))
    source.digests = False
    return intake._adopted(where, root, source, log=lambda s: None)


def a_library(root, **settings):
    cfg = Config(library_root=root, musicbrainz=False, lyrics=False, **settings)
    return Service(cfg, root, log=lambda s: None)


# -- counted by their owner, never by their description -----------------------------------------


def test_only_windows_medias_own_frames_are_counted(album):
    root, where = album
    one = where / "01 One.mp3"

    assert wm_frames_in(one) == 2, "two of the three are WM/, the third is somebody else's"


def test_a_file_of_another_format_has_none(tmp_path, one_second_of_sound):
    opus = tmp_path / "a.opus"
    shutil.copy(one_second_of_sound, opus)
    assert wm_frames_in(opus) == 0
    assert wm_frames_in(tmp_path / "not there.mp3") == 0


# -- with the setting on they go, and nothing else does -----------------------------------------


def test_the_setting_takes_them_out_and_leaves_the_others(album):
    root, where = album
    plan = a_plan(root, where)
    one = where / plan.tracks[0].filename

    tag_file(one, plan, plan.tracks[0], None, None, keep_unknown=True, drop_wm_frames=True)

    assert wm_frames_in(one) == 0
    kept = [(f.owner, f.data) for f in ID3(one, load_v1=False).getall("PRIV")]
    assert kept == [("PeakValue", b"\x01\x02")], "a `PRIV` of somebody else's is a tag like any"


def test_with_the_setting_off_they_stay(album):
    root, where = album
    plan = a_plan(root, where)
    one = where / plan.tracks[0].filename

    tag_file(one, plan, plan.tracks[0], None, None, keep_unknown=True)

    assert wm_frames_in(one) == 2


# -- it reaches the passes --------------------------------------------------------------------


def test_a_take_in_with_the_setting_on_leaves_none(album):
    root, where = album

    intake.take_in(a_library(root, drop_wm_frames=True), root, QUIET, dry_run=False,
                   log=lambda s: None)

    assert [wm_frames_in(p) for p in sorted(where.glob("*.mp3"))] == [0]


def test_repair_reaches_an_album_that_is_otherwise_tidy(album):
    root, where = album
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    assert sum(wm_frames_in(p) for p in where.glob("*.mp3")) == 2

    service = a_library(root, drop_wm_frames=True, retag_adopted=True)
    said = []
    service.log = said.append
    service.repair(dry_run=True)

    assert [line for line in said if "Windows Media frame(s) would be dropped" in line], said
    assert sum(wm_frames_in(p) for p in where.glob("*.mp3")) == 2, "a dry run writes nothing"

    service.repair()

    assert sum(wm_frames_in(p) for p in where.glob("*.mp3")) == 0


def test_an_album_whose_tags_are_not_ours_to_write_is_left_alone(album):
    root, where = album
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    plan = a_plan(root, where)
    want = Treatment.from_settings(Config(library_root=root, drop_wm_frames=True))

    assert drops_wm_frames(plan, want) is False
    assert wm_frames_to_drop(plan, where, want) == 0
    assert not [line for line in would_do(plan, where, None, root, want) if "Windows Media" in line]


def test_the_dry_run_counts_them(album):
    root, where = album
    plan = a_plan(root, where)
    want = Treatment.from_settings(Config(library_root=root, drop_wm_frames=True,
                                          retag_adopted=True))

    said = would_do(plan, where, None, root, want)

    counted = [line for line in said if "Windows Media frame(s) would be dropped" in line]
    assert len(counted) == 1 and "2 Windows Media frame(s)" in counted[0], said
    assert wm_frames_to_drop(plan, where, want) == 2


# -- and the record puts them back --------------------------------------------------------------


def test_the_record_keeps_every_frame_and_the_restore_puts_them_all_back(album):
    """A setting that removes them makes them this program's to record — owner and bytes, per frame.

    Their *descriptions* never reach a key: one of those is raw binary holding a `\\x85`, and that is
    what cut a snapshot record in two.
    """
    root, where = album
    one = where / "01 One.mp3"
    recorded = raw_tags(one)
    assert sorted(owner for owner, _ in recorded["PRIV"]) == sorted(o for o, _ in THEIRS)
    plan = a_plan(root, where)

    tag_file(one, plan, plan.tracks[0], None, None, keep_unknown=True, drop_wm_frames=True)
    assert wm_frames_in(one) == 0

    assert restore_tags(one, recorded) is True

    back = sorted((f.owner, f.data) for f in ID3(one, load_v1=False).getall("PRIV"))
    assert back == sorted(THEIRS)


def test_a_record_taken_before_this_leaves_them_where_they_are(album):
    """Every record in the library is silent about `PRIV`, and silence is not "there were none"."""
    root, where = album
    one = where / "01 One.mp3"
    old_record = {k: v for k, v in raw_tags(one).items() if k != "PRIV"}

    assert restore_tags(one, old_record) is False
    assert wm_frames_in(one) == 2

    assert restore_tags(one, {**old_record, "PRIV": []}) is True
    assert wm_frames_in(one) == 0
