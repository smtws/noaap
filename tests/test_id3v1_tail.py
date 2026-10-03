"""An mp3 this program writes ends without an ID3v1 tail (DESIGN §9, slice 112; R-434, R-435).

The whole-collection pass stopped an hour in, in a traceback, on
`Earth, Wind & Fire - … - 04 - Fan The Fire.mp3`: the careful write refused because what it had
written was not the same recording. It was right by its own measure and wrong about the audio. That
file ends in a 128-byte ID3v1 `TAG` block, and mutagen's `save` rewrites the block from the v2
frames by default — NUL-padding what was space-padded — while ffmpeg's demuxer hands the block to
the **decoder**. `tests/test_identity.py` holds the digests' half of the answer; this file holds the
writer's: the block goes whole, nothing of it is moved into the ID3v2 tag, and the files already
written with one rewritten are reached. `tests/test_drop_comments.py` is the comment's own half.
"""

from __future__ import annotations

import shutil

import pytest
from mutagen.id3 import COMM, ID3, TALB, TIT2, TPE1, TPE2, TRCK
from test_collisions import a_service
from test_intake import QUIET

from noaap import intake, sources
from noaap.config import Config
from noaap.tag import _id3v1_tail, ends_with_an_id3v1_tail, tag_file

THEIRS = "ripped by Sir_Mc_Tod"      # what 153 of the 187 Crematory originals really say


def a_tail(title: str, pad: bytes = b" ", comment: str = THEIRS) -> bytes:
    """A real 128-byte ID3v1.1 block, space-padded the way the collection's files are."""
    def field(text: str, width: int) -> bytes:
        return text.encode("latin-1")[:width].ljust(width, pad)
    return (b"TAG" + field(title, 30) + field("A Band", 30) + field("An Album", 30)
            + b"2003" + field(comment, 28) + b"\x00\x01" + b"\x0c")


def with_a_tail(path, title, **how):
    with path.open("ab") as out:
        out.write(a_tail(title, **how))
    return path


def their_mp3(folder, number, title, one_second_of_mp3, *, tail=True, comm=None):
    """One file as its owner left it: v2 frames, perhaps their own comment, perhaps a v1 tail."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{number:02d} {title}.mp3"
    shutil.copy(one_second_of_mp3, path)
    tags = ID3()
    for frame in (TIT2(encoding=3, text=[title]), TPE1(encoding=3, text=["A Band"]),
                  TPE2(encoding=3, text=["A Band"]), TALB(encoding=3, text=["An Album"]),
                  TRCK(encoding=3, text=[str(number)])):
        tags.add(frame)
    if comm is not None:
        tags.add(COMM(encoding=3, lang="eng", desc="", text=[comm]))
    tags.save(path, v1=0)
    return with_a_tail(path, title) if tail else path


def their_album(root, titles, one_second_of_mp3, **how):
    where = root / "A Band" / "An Album"
    for number, title in enumerate(titles, 1):
        their_mp3(where, number, title, one_second_of_mp3, **how)
    return where


@pytest.fixture
def album(tmp_path, one_second_of_mp3):
    """Somebody's mp3 album, every file ending in a space-padded tail with their comment in it."""
    root = tmp_path / "collection"
    return root, their_album(root, ["One", "Two"], one_second_of_mp3)


def a_plan(root, where):
    source = sources.get("folder", Config(library_root=root))
    source.digests = False
    return intake._adopted(where, root, source, log=lambda s: None)


# -- the tail goes whole, and nothing of it is lost (R-434, R-435) -----------------------------


def test_a_written_mp3_ends_without_a_tail(album):
    root, where = album
    plan = a_plan(root, where)
    one = where / plan.tracks[0].filename
    assert _id3v1_tail(one) is not None, "the fixture's own premise"

    tag_file(one, plan, plan.tracks[0], None, None, keep_unknown=True)

    assert _id3v1_tail(one) is None
    assert ends_with_an_id3v1_tail(one) is False


def test_a_file_that_never_had_one_is_left_without_one(tmp_path, one_second_of_mp3):
    root = tmp_path / "collection"
    where = their_album(root, ["One"], one_second_of_mp3, tail=False)
    plan = a_plan(root, where)

    tag_file(where / plan.tracks[0].filename, plan, plan.tracks[0], None, None, keep_unknown=True)

    assert _id3v1_tail(where / plan.tracks[0].filename) is None


def test_nothing_of_the_tail_is_moved_into_the_v2_tag(album):
    """The tail goes and nothing of it goes inwards (R-438, ruling 2).

    `ID3(path)` makes a `COMM:ID3v1 Comment` frame out of the block on load and `save` writes it, so
    the first version of this dropped the tail and left its 30 characters behind in ID3v2 — which is
    how 175 files on the share came to hold a comment frame their owner never put there. The user's
    answer to the measurement was that those source descriptions are not to be carried on, so the
    read is `load_v1=False` and the block is simply gone. `tests/test_drop_comments.py` holds the
    other half.
    """
    root, where = album
    plan = a_plan(root, where)
    one = where / plan.tracks[0].filename
    assert [str(f) for f in ID3(one).getall("COMM")] == [THEIRS], "mutagen offers it on load"

    tag_file(one, plan, plan.tracks[0], None, None, keep_unknown=True)

    assert _id3v1_tail(one) is None
    assert ID3(one, load_v1=False).getall("COMM") == []


def test_a_comment_frame_of_their_own_is_not_touched(tmp_path, one_second_of_mp3):
    """What the tail's going does not reach: a `COMM` frame really in the ID3v2 tag. That is a tag,
    and an adopted album's tags are kept unless the library asks otherwise (`drop_comments`).
    """
    root = tmp_path / "collection"
    where = their_album(root, ["One"], one_second_of_mp3, comm="from my own vinyl")
    plan = a_plan(root, where)
    one = where / plan.tracks[0].filename

    tag_file(one, plan, plan.tracks[0], None, None, keep_unknown=True)

    assert [str(f) for f in ID3(one, load_v1=False).getall("COMM")] == ["from my own vinyl"]
    assert _id3v1_tail(one) is None


def test_a_tail_is_what_tells_an_already_treated_file_apart(album):
    """Crematory's shape: taken in by 1.31.2, which rewrote the tail instead of dropping it.

    Such a file's plan says the track is done and its tags are exactly what the plan asked for, so
    the signature agrees and nothing else in the program can see that the file is in a state no
    version writes any more. This one question can (R-434).
    """
    root, where = album
    plan = a_plan(root, where)
    one = where / plan.tracks[0].filename
    tag_file(one, plan, plan.tracks[0], None, None, keep_unknown=True)
    assert ends_with_an_id3v1_tail(one) is False

    with_a_tail(one, "One", pad=b"\x00", comment="")   # as 1.31.2 left 175 files on the share

    assert ends_with_an_id3v1_tail(one) is True


def test_only_an_mp3_is_asked_the_question(tmp_path, one_second_of_sound):
    """It costs an open per file, so it is asked of the one format that can answer it."""
    opus = tmp_path / "a.opus"
    shutil.copy(one_second_of_sound, opus)

    assert ends_with_an_id3v1_tail(opus) is False
    assert ends_with_an_id3v1_tail(tmp_path / "not there.mp3") is False


def test_repair_rewrites_a_treated_file_whose_tail_is_still_there(album):
    """And the pass that brings a whole library to one state is the one that has to notice.

    `repair` skips an album nothing is wrong with, before it ever reaches the writer, and the dry
    run says what the real run would do. Without the question asked in all three places, those 175
    files would keep their tails for ever.
    """
    root, where = album
    service = a_service(root)
    intake.take_in(service, root, QUIET, dry_run=False, log=lambda s: None)
    files = sorted(where.glob("*.mp3"))
    assert files and all(_id3v1_tail(p) is None for p in files)
    for path in files:                                  # put 1.31.2's state back
        with_a_tail(path, path.stem, pad=b"\x00", comment="")

    said = []
    service.log = said.append
    service.repair(dry_run=True)

    assert [line for line in said if "without its ID3v1 tail" in line], said
    assert all(_id3v1_tail(p) is not None for p in files), "a dry run writes nothing"

    service.repair()

    assert all(_id3v1_tail(p) is None for p in sorted(where.glob("*.mp3")))


def test_a_file_this_program_never_wrote_keeps_its_tail(album):
    """The condition on the whole of the above: the block goes where *this* program put it.

    An album adopted with the library's tags setting off has never been written to — `tagged` is
    unset — so its tail is its owner's, 128 bytes they have had since 2003, and no pass of a program
    that was told not to touch their tags is going to take it away.
    """
    root, where = album
    service = a_service(root)
    intake.take_in(service, root, intake.Choices(musicbrainz=False, lyrics=False, tags=False),
                   dry_run=False, log=lambda s: None)
    files = sorted(where.glob("*.mp3"))
    assert files and all(_id3v1_tail(p) is not None for p in files), "nothing was written into them"

    said = []
    service.log = said.append
    service.repair()

    assert all(_id3v1_tail(p) is not None for p in sorted(where.glob("*.mp3")))
    assert not [line for line in said if "ID3v1" in line], said
