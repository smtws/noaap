"""The comment a library may ask to be rid of (DESIGN §9, slice 113; R-438).

Measured on the first batch staged out of the user's collection — 3,972 files, read-only: 2,993 of
them carry a comment, and 4,873 of the 4,937 comment values are the one string `ripped by
Sir_Mc_Tod`, with `www.NewAlbumReleases.net` 48 times and `Encoded by EasyTAG` 12. The user, shown
that: *"source descriptions I don't want to carry on"*.

A comment is otherwise one of the fields this program does not model and therefore never touches, so
this is the one thing a write takes away rather than leaves, and it happens only where the library's
`drop_comments` says so. Ruling 2 is separate and unconditional: the `COMM:ID3v1 Comment` frame
mutagen makes out of a trailing block on load is never written into an ID3v2 tag.
"""

from __future__ import annotations

import shutil

import pytest
from mutagen import File as MFile
from mutagen.id3 import COMM, ID3, TALB, TIT2, TPE1, TPE2, TRCK
from mutagen.mp4 import MP4
from test_collisions import a_service
from test_formats import encode
from test_id3v1_tail import with_a_tail
from test_intake import QUIET

from noaap import intake, sources
from noaap.config import Config
from noaap.download import comments_to_drop, would_do
from noaap.service import Service
from noaap.tag import comments_in, tag_file
from noaap.treatment import Treatment, drops_comments


def an_mp3(folder, number, title, one_second_of_mp3, *, comments=(), tail=None):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{number:02d} {title}.mp3"
    shutil.copy(one_second_of_mp3, path)
    tags = ID3()
    for frame in (TIT2(encoding=3, text=[title]), TPE1(encoding=3, text=["A Band"]),
                  TPE2(encoding=3, text=["A Band"]), TALB(encoding=3, text=["An Album"]),
                  TRCK(encoding=3, text=[str(number)])):
        tags.add(frame)
    for desc, text in comments:
        tags.add(COMM(encoding=3, lang="eng", desc=desc, text=[text]))
    tags.save(path, v1=0)
    return with_a_tail(path, title, comment=tail) if tail is not None else path


@pytest.fixture
def album(tmp_path, one_second_of_mp3):
    """Two mp3s: one with the ripper's note in its v2 tag, one with it only in the tail."""
    root = tmp_path / "collection"
    where = root / "A Band" / "An Album"
    an_mp3(where, 1, "One", one_second_of_mp3, comments=[("", "ripped by Sir_Mc_Tod")])
    an_mp3(where, 2, "Two", one_second_of_mp3, tail="ripped by Sir_Mc_Tod")
    return root, where


def a_plan(root, where):
    source = sources.get("folder", Config(library_root=root))
    source.digests = False
    return intake._adopted(where, root, source, log=lambda s: None)


def a_library(root, **settings):
    cfg = Config(library_root=root, musicbrainz=False, lyrics=False, **settings)
    return Service(cfg, root, log=lambda s: None)


# -- ruling 2: nothing of the tail is moved into the ID3v2 tag ---------------------------------


def test_a_tails_comment_is_not_written_into_the_v2_tag(album):
    """`ID3(path)` makes a `COMM:ID3v1 Comment` frame out of the block on load, and `save` writes
    it. That is how 175 files on the share came to hold a comment frame their owner never put there.
    Loading without v1 is how it stops, and it stops whatever the setting says.
    """
    root, where = album
    plan = a_plan(root, where)
    two = where / plan.tracks[1].filename
    assert comments_in(two) == 0, "it is in the tail, not in the tag"
    assert [str(f) for f in ID3(two).getall("COMM")] == ["ripped by Sir_Mc_Tod"], \
        "mutagen offers it on load, which is what used to get written"

    tag_file(two, plan, plan.tracks[1], None, None, keep_unknown=True)

    assert ID3(two, load_v1=False).getall("COMM") == []
    assert comments_in(two) == 0


def test_a_comment_frame_of_their_own_still_survives_with_the_setting_off(album):
    root, where = album
    plan = a_plan(root, where)
    one = where / plan.tracks[0].filename

    tag_file(one, plan, plan.tracks[0], None, None, keep_unknown=True)

    assert [str(f) for f in ID3(one, load_v1=False).getall("COMM")] == ["ripped by Sir_Mc_Tod"]


# -- ruling 1: with the setting on, the comment goes, in all three containers -------------------


def test_the_setting_takes_every_description_out_of_an_mp3(tmp_path, one_second_of_mp3):
    root = tmp_path / "collection"
    where = root / "A Band" / "An Album"
    one = an_mp3(where, 1, "One", one_second_of_mp3,
                 comments=[("", "ripped by Sir_Mc_Tod"), ("iTunNORM", "0001 0002")])
    plan = a_plan(root, where)
    assert comments_in(one) == 2

    tag_file(one, plan, plan.tracks[0], None, None, keep_unknown=True, drop_comments=True)

    assert comments_in(one) == 0


def test_the_setting_takes_comment_and_description_out_of_a_vorbis_file(tmp_path,
                                                                        one_second_of_sound):
    root = tmp_path / "collection"
    where = root / "A Band" / "An Album"
    where.mkdir(parents=True)
    path = where / "01 One.opus"
    shutil.copy(one_second_of_sound, path)
    audio = MFile(path)
    for key, value in (("title", "One"), ("artist", "A Band"), ("albumartist", "A Band"),
                       ("album", "An Album"), ("tracknumber", "1"),
                       ("comment", "ripped by Sir_Mc_Tod"), ("DESCRIPTION", "www.example.net"),
                       ("replaygain_track_gain", "-3.21 dB")):
        audio[key] = [value]
    audio.save()
    plan = a_plan(root, where)
    assert comments_in(path) == 2

    tag_file(path, plan, plan.tracks[0], None, None, keep_unknown=True, drop_comments=True)

    assert comments_in(path) == 0
    assert MFile(path)["replaygain_track_gain"] == ["-3.21 dB"], "only the comment goes"


def test_the_setting_takes_the_comment_atom_out_of_an_m4a(tmp_path):
    root = tmp_path / "collection"
    where = root / "A Band" / "An Album"
    where.mkdir(parents=True)
    path = encode(where / "01 One.m4a", "-c:a", "aac", "-b:a", "128k")
    audio = MP4(path)
    audio["\xa9nam"], audio["\xa9ART"], audio["aART"] = [["One"], ["A Band"], ["A Band"]]
    audio["\xa9alb"], audio["\xa9cmt"] = [["An Album"], ["ripped by Sir_Mc_Tod"]]
    audio["trkn"] = [(1, 1)]
    audio.save()
    plan = a_plan(root, where)
    assert comments_in(path) == 1

    tag_file(path, plan, plan.tracks[0], None, None, keep_unknown=True, drop_comments=True)

    assert comments_in(path) == 0


def test_an_m4a_still_gets_noaaps_own_source_in_that_atom(tmp_path):
    """`©cmt` is where noaap keeps an m4a's `source`, so the drop happens before the plan's keys
    go in — otherwise a fetched album would lose the one value this program does put there.
    """
    root = tmp_path / "collection"
    where = root / "A Band" / "An Album"
    where.mkdir(parents=True)
    path = encode(where / "01 One.m4a", "-c:a", "aac", "-b:a", "128k")
    audio = MP4(path)
    audio["\xa9nam"], audio["\xa9cmt"] = [["One"], ["ripped by somebody"]]
    audio.save()
    plan = a_plan(root, where)
    plan.source_url = "https://example.com/playlist?list=abc"

    tag_file(path, plan, plan.tracks[0], None, None, keep_unknown=False, drop_comments=True)

    assert MP4(path)["\xa9cmt"] == ["https://example.com/playlist?list=abc"]


# -- it reaches the passes, and only where a file is written ------------------------------------


def test_a_take_in_with_the_setting_on_leaves_no_comment_behind(album):
    root, where = album

    done = intake.take_in(a_library(root, drop_comments=True), root, QUIET,
                          dry_run=False, log=lambda s: None)

    assert done.adopted == 1
    assert [comments_in(p) for p in sorted(where.glob("*.mp3"))] == [0, 0]


def test_a_take_in_with_the_setting_off_keeps_the_frame_they_wrote(album):
    root, where = album

    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)

    assert sum(comments_in(p) for p in where.glob("*.mp3")) == 1


def test_repair_reaches_an_album_that_is_otherwise_tidy(album):
    """The album is taken in, so every file says what the plan says and nothing else would look at
    it again. Switching the setting on has to reach it from the next repair.
    """
    root, where = album
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    assert sum(comments_in(p) for p in where.glob("*.mp3")) == 1

    service = a_library(root, drop_comments=True, retag_adopted=True)
    said = []
    service.log = said.append
    service.repair(dry_run=True)

    assert [line for line in said if "comment(s) would be dropped" in line], said
    assert sum(comments_in(p) for p in where.glob("*.mp3")) == 1, "a dry run writes nothing"

    service.repair()

    assert sum(comments_in(p) for p in where.glob("*.mp3")) == 0


def test_an_album_whose_tags_are_not_ours_to_write_is_not_opened_for_it(album):
    """`drop_comments` says what the files this library writes hold. An adopted album the library
    does not retag is not written to at all, so its comment stays and the dry run promises nothing.
    """
    root, where = album
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    plan = a_plan(root, where)
    want = Treatment.from_settings(Config(library_root=root, drop_comments=True))

    assert drops_comments(plan, want) is False
    assert comments_to_drop(plan, where, want) == 0
    assert not [line for line in would_do(plan, where, None, root, want) if "comment" in line]


def test_the_dry_run_counts_them_and_the_apply_writes_exactly_that(album):
    root, where = album
    plan = a_plan(root, where)
    want = Treatment.from_settings(Config(library_root=root, drop_comments=True,
                                          retag_adopted=True))

    said = would_do(plan, where, None, root, want)
    counted = [line for line in said if "comment(s) would be dropped" in line]

    assert len(counted) == 1, said
    assert "1 comment(s) would be dropped" in counted[0]
    assert comments_to_drop(plan, where, want) == 1


# -- and a restore gives it back, because a setting can take it away ---------------------------


def test_the_record_keeps_every_description_and_the_restore_puts_them_all_back(
        tmp_path, one_second_of_mp3):
    """`drop_comments` makes `COMM` a key this program writes, so it belongs in the record.

    Per description, not the first one: a file may hold `COMM::XXX` beside `COMM:iTunNORM:eng`, and
    a record of one of them is a restore that silently drops the other.
    """
    from noaap.tag import raw_tags, restore_tags

    root = tmp_path / "collection"
    where = root / "A Band" / "An Album"
    one = an_mp3(where, 1, "One", one_second_of_mp3,
                 comments=[("", "ripped by Sir_Mc_Tod"), ("iTunNORM", "0001 0002")])
    recorded = raw_tags(one)
    assert sorted(row[0] for row in recorded["COMM"]) == ["", "iTunNORM"]
    plan = a_plan(root, where)

    tag_file(one, plan, plan.tracks[0], None, None, keep_unknown=True, drop_comments=True)
    assert comments_in(one) == 0

    assert restore_tags(one, recorded) is True

    back = {f.desc: str(f) for f in ID3(one, load_v1=False).getall("COMM")}
    assert back == {"": "ripped by Sir_Mc_Tod", "iTunNORM": "0001 0002"}


def test_a_tails_comment_is_not_recorded_as_if_it_were_in_the_tag(album):
    """Ruling 2 reaches the record too: what mutagen makes out of the block is not in the file."""
    from noaap.tag import raw_tags

    root, where = album
    plan = a_plan(root, where)
    two = where / plan.tracks[1].filename

    assert raw_tags(two).get("COMM") in (None, [])


def test_a_record_taken_before_this_leaves_the_comment_where_it_is(tmp_path, one_second_of_mp3):
    """Every record in the library is silent about `COMM`, and silence is not "there was none".

    The undo's rule is that what the record does not have is removed, which is right for a key this
    program writes and wrong for the thousands of records taken while the comment was nobody's but
    the owner's. An empty list is how a record says it really saw none.
    """
    from noaap.tag import raw_tags, restore_tags

    root = tmp_path / "collection"
    where = root / "A Band" / "An Album"
    one = an_mp3(where, 1, "One", one_second_of_mp3, comments=[("", "ripped by Sir_Mc_Tod")])
    old_record = {k: v for k, v in raw_tags(one).items() if k != "COMM"}   # a record of 1.31.2

    assert restore_tags(one, old_record) is False, "nothing else about the file differs either"
    assert comments_in(one) == 1

    assert restore_tags(one, {**old_record, "COMM": []}) is True
    assert comments_in(one) == 0
