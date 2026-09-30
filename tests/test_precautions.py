"""What is prepared before noaap writes to somebody's own collection (DESIGN §9, slice 99).

The user, of twenty years of music on a NAS with no backup: *"i wont let it to my collection of 20
years without precautions prepared"*. These cases are that sentence, held: a snapshot that records
what every file was, a write that cannot leave a half-written file, and a restore that puts names and
tags back and proves the recording is the one that was written down.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from mutagen import File as MFile

from noaap import precautions
from noaap.precautions import Unsafe


@pytest.fixture
def collection(tmp_path, one_second_of_sound):
    """Somebody's own folder: two albums, their own names, their own tags, no plan anywhere."""
    root = tmp_path / "collection"
    for album, titles in (("Aphelion/Nocturnes", ["01 First", "02 Second"]),
                          ("Aphelion/Vigil", ["01 Only"])):
        folder = root / album
        folder.mkdir(parents=True)
        for n, title in enumerate(titles, 1):
            path = folder / f"{title}.opus"
            shutil.copy(one_second_of_sound, path)
            audio = MFile(path)
            audio["title"] = [title.split(" ", 1)[1]]
            audio["artist"] = ["Aphelion"]
            audio["album"] = [album.split("/")[1]]
            audio["comment"] = [f"ripped by me in 2006, track {n}"]
            audio.save()
    return root


def files_under(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in precautions.audio_under(root)}


# -- the snapshot ----------------------------------------------------------------------------------


def test_a_snapshot_records_every_file_and_what_it_said(collection, tmp_path):
    out = precautions.take(collection, tmp_path / "snap.jsonl")

    lines = out.read_text().splitlines()
    head = json.loads(lines[0])
    assert head["noaap_snapshot"] == 1 and head["root"] == str(collection)
    assert head["digest_by"] and head["at"]
    assert len(lines) == 4, "a header and one line per audio file"

    snap = precautions.read(out)
    assert [r.path for r in snap.files] == ["Aphelion/Nocturnes/01 First.opus",
                                            "Aphelion/Nocturnes/02 Second.opus",
                                            "Aphelion/Vigil/01 Only.opus"]
    one = snap.files[0]
    assert one.size > 0 and one.mtime_ns > 0 and one.packets
    assert one.tags["title"] == ["First"], "the keys a pass here could overwrite, verbatim"
    assert "comment" in one.others, "and a fingerprint of every key of theirs that it could not"
    assert one.others["comment"] not in ("", None)
    assert "comment" not in one.tags, "their own field is not ours to hold a copy of"


def test_a_snapshot_never_overwrites_one_that_is_there(collection, tmp_path):
    out = precautions.take(collection, tmp_path / "snap.jsonl")
    with pytest.raises(Unsafe, match="already there"):
        precautions.take(collection, out)


def test_a_file_that_is_not_a_snapshot_is_refused(tmp_path):
    other = tmp_path / "notes.jsonl"
    other.write_text('{"something": "else"}\n')
    with pytest.raises(Unsafe, match="not a noaap snapshot"):
        precautions.read(other)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    with pytest.raises(Unsafe, match="empty"):
        precautions.read(empty)


# -- the safe write --------------------------------------------------------------------------------


def test_a_write_that_fails_leaves_the_file_as_it_was(collection):
    path = next(precautions.audio_under(collection))
    before = path.read_bytes()

    def write(tmp: Path) -> None:
        tmp.write_bytes(b"half a file")
        raise RuntimeError("the machine stopped here")

    with pytest.raises(RuntimeError):
        precautions.safely(path, write)

    assert path.read_bytes() == before
    assert not list(path.parent.glob(".*noaap-new")), "and the copy it was working on is gone"


def test_a_write_that_changes_the_recording_is_refused(collection, one_second_of_sound):
    path = next(precautions.audio_under(collection))
    before = path.read_bytes()

    with pytest.raises(Unsafe, match="not the same recording"):
        precautions.safely(path, lambda tmp: tmp.write_bytes(b"\x00" * 4096))

    assert path.read_bytes() == before


def test_a_write_that_keeps_the_recording_goes_through(collection):
    path = next(precautions.audio_under(collection))
    before = path.read_bytes()

    def retag(tmp: Path) -> str:
        audio = MFile(tmp)
        audio["title"] = ["Something Else"]
        audio.save()
        return "done"

    assert precautions.safely(path, retag) == "done"
    assert path.read_bytes() != before
    assert MFile(path)["title"] == ["Something Else"]
    assert precautions.same_audio(path, path), "and it is still the same recording"


def test_the_original_is_kept_once_and_whole(collection, tmp_path):
    keep = tmp_path / "originals"
    path = next(precautions.audio_under(collection))
    before = path.read_bytes()

    for title in ("First pass", "Second pass"):
        precautions.safely(path, lambda tmp, t=title: _retitle(tmp, t), keep=keep, root=collection)

    kept = keep / path.relative_to(collection)
    assert kept.read_bytes() == before, "the first pass put the original aside, the second left it"
    assert MFile(path)["title"] == ["Second pass"]


def _retitle(path: Path, title: str) -> None:
    audio = MFile(path)
    audio["title"] = [title]
    audio.save()


# -- the restore -----------------------------------------------------------------------------------


def test_a_restore_puts_names_and_tags_back(collection, tmp_path):
    snap = precautions.read(precautions.take(collection, tmp_path / "snap.jsonl"))
    # what a pass does: rename, and write our own tags
    for path in list(precautions.audio_under(collection)):
        precautions.safely(path, lambda tmp: _retitle(tmp, "noaap's title"))
        path.rename(path.with_name(f"Aphelion - {path.name}"))

    dry = precautions.restore(snap, collection)
    assert dry.files == 3 and dry.renamed == 3
    assert [p.name for p in precautions.audio_under(collection)][0].startswith("Aphelion - "), \
        "a dry restore changes nothing"

    done = precautions.restore(snap, collection, apply=True)
    assert done.missing == [] and done.changed == []
    assert [str(p.relative_to(collection)) for p in precautions.audio_under(collection)] == \
        [r.path for r in snap.files]
    assert done.lost == [], "and nothing of theirs was lost on the way"
    for was in snap.files:
        audio = MFile(collection / was.path)
        assert list(audio["title"]) == was.tags["title"], "their title, not ours"
        assert list(audio["comment"]) == ["ripped by me in 2006, track 1"] or was.path != snap.files[0].path
        assert (collection / was.path).stat().st_mtime_ns == was.mtime_ns


def test_a_restore_from_the_kept_originals_is_byte_for_byte(collection, tmp_path):
    """The only layer that can promise the file back exactly: a tag round-trip cannot.

    Measured while building this: writing the same tag values back through mutagen gives a file of the
    same size and different bytes, so "byte-identical" is a promise only the kept copy can make.
    """
    keep = tmp_path / "originals"
    before = files_under(collection)
    snap = precautions.read(precautions.take(collection, tmp_path / "snap.jsonl"))

    for path in list(precautions.audio_under(collection)):
        precautions.safely(path, lambda tmp: _retitle(tmp, "noaap's title"), keep=keep, root=collection)
        path.rename(path.with_name(f"Aphelion - {path.name}"))
    assert files_under(collection) != before

    precautions.restore(snap, collection, apply=True, kept=keep)
    assert files_under(collection) == before, "byte for byte, names included"


def test_a_file_that_is_not_there_is_named_and_not_guessed_at(collection, tmp_path):
    snap = precautions.read(precautions.take(collection, tmp_path / "snap.jsonl"))
    gone = collection / snap.files[0].path
    gone.unlink()

    done = precautions.restore(snap, collection, apply=True)
    assert done.missing == [snap.files[0].path]
    assert done.files == 3


def test_the_room_it_would_take_is_said_before_it_is_taken(collection, tmp_path):
    need, free = precautions.room_for(collection, tmp_path / "originals")
    assert need == sum(p.stat().st_size for p in precautions.audio_under(collection))
    assert free > 0
    said = precautions.says_room(collection, tmp_path / "originals")
    assert "GB" in said and str(tmp_path / "originals") in said


def test_a_field_of_theirs_that_a_pass_dropped_is_named(collection, tmp_path):
    """The snapshot cannot put their own `comment` back — nothing here writes it, so nothing here
    keeps a copy — but it holds a fingerprint, so a pass that dropped one is found out."""
    snap = precautions.read(precautions.take(collection, tmp_path / "snap.jsonl"))
    path = collection / snap.files[0].path

    def wipe(tmp: Path) -> None:      # what a writer with `keep_unknown=False` would do to it
        audio = MFile(tmp)
        del audio["comment"]
        audio.save()

    precautions.safely(path, wipe)
    done = precautions.restore(snap, collection, apply=True)
    assert done.lost == [f"{snap.files[0].path}: comment"]
    assert done.changed == [], "the recording itself is untouched, which is the other question"
