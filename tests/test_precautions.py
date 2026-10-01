"""What is prepared before noaap writes to somebody's own collection (DESIGN §9, slice 99).

The user, of twenty years of music on a NAS with no backup: *"i wont let it to my collection of 20
years without precautions prepared"*. These cases are that sentence, held: a snapshot that records
what every file was, a write that cannot leave a half-written file, and a restore that puts names and
tags back and proves the recording is the one that was written down.
"""

from __future__ import annotations

import json
import shutil
import subprocess
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
    assert one.size > 0 and one.mtime_ns > 0 and one.audio and one.how == "decoded"
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
    assert next(precautions.audio_under(collection)).name.startswith("Aphelion - "), \
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


def test_the_copy_it_writes_to_is_the_same_kind_of_file(tmp_path, one_second_of_sound):
    """`tag.kind` decides the format by the suffix, so the temporary copy has to keep it.

    Found on the user's own collection: with the copy named `.x.mp3.noaap-new`, every mp3 was opened
    as an Opus and **1662 of 2000 files were not tagged** — *"read b'ID3', expected b'OggS'"*. Nothing
    was damaged, because the write fails before the replace; nothing was written either.
    """
    from noaap.tag import kind

    seen = []
    for suffix in (".opus", ".mp3", ".flac", ".m4a"):
        path = tmp_path / f"track{suffix}"
        shutil.copy(one_second_of_sound, path)          # the bytes do not matter here, the name does
        precautions.safely(path, lambda tmp: seen.append((kind(path), kind(tmp))))
    assert seen == [("opus", "opus"), ("mp3", "mp3"), ("flac", "flac"), ("mp4", "mp4")]


def test_two_files_of_one_recording_are_not_taken_for_each_other(tmp_path, one_second_of_sound):
    """The same track on an album and on a best-of holds the same audio.

    The digest search looks for a renamed file by what it holds; if it may claim a file the snapshot
    records under its own name, it renames somebody else's file away — which is how the first version
    of this left a `FileNotFoundError` behind on a fixture where both files were the same tone.
    """
    root = tmp_path / "collection"
    (root / "Album").mkdir(parents=True)
    for name in ("01 One.opus", "02 Two.opus"):
        shutil.copy(one_second_of_sound, root / "Album" / name)   # the same recording twice
    snap = precautions.read(precautions.take(root, tmp_path / "snap.jsonl"))

    (root / "Album" / "01 One.opus").unlink()                     # one of them is gone
    done = precautions.restore(snap, root, apply=True)

    assert done.missing == ["Album/01 One.opus"], "named, not filled in with the other one"
    assert (root / "Album" / "02 Two.opus").is_file(), "and the other one is where it was"


def test_a_renamed_collection_is_found_without_digesting_all_of_it(tmp_path, one_second_of_mp3):
    """Roughly one digest per file, not one per file per file.

    Measured on the user's own collection: a pass that renamed *and* retagged all 2000 files left the
    restore asking ffmpeg about every candidate for every file — 8 GB read in four minutes and nowhere
    near done, hours for that copy and days for the collection. The candidates are ranked now, and
    only the digest decides, so the ranking costs nothing and may be as rough as it likes.

    **In mp3, because this could not happen in Opus.** Every file here holds a different recording (a
    candidate that is not the one wanted has to be digested to find out), the retag is big enough to
    change the size (mutagen's padding swallows a small one, which is why the fixtures never showed
    this), and the new names reverse the order the tree is walked in. Unranked: 54 digests for these
    twelve files. Ranked: 24, which is one to find each file and one to prove its audio survived.
    """
    root = tmp_path / "collection"
    made = 0
    for artist in ("Aphelion", "Bramblewood"):
        folder = root / artist / "Album"
        folder.mkdir(parents=True)
        for n, title in enumerate(["First", "Second", "Third", "Fourth", "Fifth", "Sixth"], 1):
            path = folder / f"{n:02d} {title}.mp3"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                            "-i", f"sine=frequency={300 + 70 * made}:duration=1",
                            "-c:a", "libmp3lame", str(path)], check=True)
            audio = MFile(path, easy=True)
            audio["title"] = [title]
            audio["artist"] = ["x" * (n * 40)]
            audio.save()
            made += 1
    snap = precautions.read(precautions.take(root, tmp_path / "snap.jsonl"))

    for was in snap.files:                        # the pass: renamed, retagged, and the folder moved
        old = root / was.path
        number = int(Path(was.path).name[:2])
        new = (root / was.path.split("/")[0] / "Album Renamed"
               / f"noaap - {99 - number:02d} - {Path(was.path).stem[3:]}.mp3")
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
        audio = MFile(new, easy=True)
        audio["artist"] = ["noaap wrote this " * 4000]
        audio.save()

    asked = []
    real = precautions.stream_sha
    precautions.stream_sha = lambda path: (asked.append(path), real(path))[1]
    try:
        done = precautions.restore(snap, root, apply=True)
    finally:
        precautions.stream_sha = real

    assert done.missing == [] and done.renamed == 12
    assert len(asked) <= 2 * done.files + 2, f"{len(asked)} digests for {done.files} files"
    assert sorted(files_under(root)) == [r.path for r in snap.files]


def test_a_careful_tag_write_to_a_real_mp3_goes_through(tmp_path, one_second_of_mp3):
    """The whole careful write, on the format the collection is actually in.

    The suffix case above proves the copy is named right; this one proves the write that follows
    works — the tags land, the recording is the one that was there, and the original is kept. On the
    user's collection this failed for 1662 files and the suite could not see it, because every
    fixture here was Opus.
    """
    from noaap.tag import raw_tags

    path = tmp_path / "collection" / "Artist" / "Album" / "01 Track.mp3"
    path.parent.mkdir(parents=True)
    shutil.copy(one_second_of_mp3, path)
    before = path.read_bytes()
    kept = tmp_path / "originals"

    def write(tmp: Path) -> str:
        audio = MFile(tmp, easy=True)
        audio["title"] = ["What noaap says it is"]
        audio["artist"] = ["Artist"]
        audio.save()
        return "written"

    answer = precautions.safely(path, write, keep=kept, root=tmp_path / "collection")

    assert answer == "written"
    assert raw_tags(path).get("TIT2") == ["What noaap says it is"]   # ID3 frames, by their own names
    assert not list(path.parent.glob(".*noaap-new*")), "and nothing left beside it"
    assert (kept / "Artist/Album/01 Track.mp3").read_bytes() == before, "the original, byte for byte"


def test_a_cover_the_pass_embedded_does_not_hide_the_file(tmp_path, one_second_of_mp3):
    """The digest a restore searches by must be of the recording, not of the file.

    Found on the user's own collection: the pass embedded the album's cover, which moved the packet
    digest of every mp3 it touched — ffmpeg's demuxer hands the trailing tag block over as audio —
    and the restore then reported one file *missing* while its audio sat there decoding identically.
    """
    from mutagen.id3 import APIC

    root = tmp_path / "collection"
    (root / "Artist" / "Album").mkdir(parents=True)
    path = root / "Artist" / "Album" / "01 Track.mp3"
    shutil.copy(one_second_of_mp3, path)
    snap = precautions.read(precautions.take(root, tmp_path / "snap.jsonl"))

    moved = path.with_name("Artist - Album - 01 - Track.mp3")      # what the pass does
    path.rename(moved)
    audio = MFile(moved)
    audio.add_tags() if audio.tags is None else None
    audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=b"\xff\xd8" + b"x" * 4000))
    audio.save()
    assert precautions.stream_sha(moved) != snap.files[0].audio, "the file digest did move"

    done = precautions.restore(snap, root, apply=True)

    assert done.missing == [] and done.renamed == 1
    assert path.is_file() and not moved.exists(), "found by what it holds, and put back"


def test_a_file_whose_audio_really_changed_is_named(tmp_path, one_second_of_mp3):
    """And the check that says so has to be a check.

    Where the packet digest had moved, this used to ask only whether ffmpeg could read the file at
    all — so `0 files whose audio is not what it was` meant nothing for any file a pass had retagged.
    """
    root = tmp_path / "collection"
    (root / "Artist" / "Album").mkdir(parents=True)
    path = root / "Artist" / "Album" / "01 Track.mp3"
    shutil.copy(one_second_of_mp3, path)
    snap = precautions.read(precautions.take(root, tmp_path / "snap.jsonl"))

    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
                    "-c:a", "libmp3lame", str(path)], check=True)   # another recording, same name

    done = precautions.restore(snap, root, apply=True)

    assert done.changed == ["Artist/Album/01 Track.mp3"], done.changed


def test_the_owners_own_files_come_back_too(tmp_path, one_second_of_sound):
    """A cover, a `.url`, a thumbnail cache — the pass moves them with the folder, so they are in it.

    Measured on the user's own collection: the snapshot recorded audio only, the pass moved each
    album folder into the scheme — folder and all — and the restore put the audio back under its
    recorded paths and left **8 of their own files** in a folder they never made. The album looked
    restored and its cover was somewhere else.
    """
    root = tmp_path / "collection"
    album = root / "aphelion" / "nocturnes (2003)"
    (album / ".thumb").mkdir(parents=True)
    shutil.copy(one_second_of_sound, album / "01 First.opus")
    (album / "cover.jpg").write_bytes(b"\xff\xd8" + b"a picture of theirs" * 50)
    (album / "New Album Releases.url").write_text("[InternetShortcut]\nURL=https://example.invalid\n")
    (album / ".thumb" / "cover.jpg.jpg").write_bytes(b"\xff\xd8" + b"a thumbnail" * 20)
    before = {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    assert len(before) == 4, before.keys()
    snap = precautions.read(precautions.take(root, tmp_path / "snap.jsonl"))
    assert len(snap.files) == 4, [r.path for r in snap.files]
    assert [r.how for r in snap.files].count("bytes") == 3, "three of them are not audio"

    moved = root / "Aphelion" / "Nocturnes"                 # what the pass does to the folder
    moved.parent.mkdir(parents=True)
    album.rename(moved)
    (moved / "01 First.opus").rename(moved / "Aphelion - Nocturnes - 01 - First.opus")

    done = precautions.restore(snap, root, apply=True)

    assert done.missing == [] and done.changed == []
    assert {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()} == before, "all four, where they were"


def test_a_careful_write_reads_one_file_when_the_digest_is_already_known(tmp_path, one_second_of_mp3):
    """The snapshot measured this file minutes ago; the write does not measure it again.

    R-342 ruling 4. Without `expect` the proof is `same_audio(path, tmp)` — both files read. With it
    only the copy is read, against a digest this same pass wrote down. Over a share that is the
    difference between one remote read of the collection and two.
    """
    path = tmp_path / "collection" / "01 Track.mp3"
    path.parent.mkdir(parents=True)
    shutil.copy(one_second_of_mp3, path)
    snap = precautions.read(precautions.take(tmp_path / "collection", tmp_path / "snap.jsonl"))
    known = snap.files[0].audio

    def write(tmp: Path) -> str:
        audio = MFile(tmp, easy=True)
        audio["title"] = ["What noaap says it is"]
        audio.save()
        return "written"

    read: list[Path] = []
    real_stream, real_decoded = precautions.stream_sha, precautions.decoded_sha
    precautions.stream_sha = lambda p: (read.append(p), real_stream(p))[1]
    precautions.decoded_sha = lambda p: (read.append(p), real_decoded(p))[1]
    try:
        assert precautions.safely(path, write, expect=known) == "written"
        with_expect = list(read)
        read.clear()
        assert precautions.safely(path, write) == "written"
        without = list(read)
    finally:
        precautions.stream_sha, precautions.decoded_sha = real_stream, real_decoded

    assert len(with_expect) == 1 and with_expect[0] != path, "only the copy, and only once"
    assert len(without) == 2 and path in without, "the old way reads the original too"


def test_a_digest_that_disagrees_costs_time_and_not_correctness(tmp_path, one_second_of_mp3):
    """A stale expectation must never be the reason a write is refused — or accepted."""
    path = tmp_path / "01 Track.mp3"
    shutil.copy(one_second_of_mp3, path)
    before = path.read_bytes()

    def retag(tmp: Path) -> str:
        audio = MFile(tmp, easy=True)
        audio["title"] = ["fine"]
        audio.save()
        return "written"

    # a digest of some other recording: the full comparison runs after all and lets this through
    assert precautions.safely(path, retag, expect="not this file's digest at all") == "written"
    from noaap.tag import raw_tags
    assert raw_tags(path).get("TIT2") == ["fine"]

    def ruin(tmp: Path) -> None:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "sine=frequency=880:duration=1", "-c:a", "libmp3lame", str(tmp)],
                       check=True)

    path.write_bytes(before)
    with pytest.raises(Unsafe):
        precautions.safely(path, ruin, expect="not this file's digest at all")
    assert path.read_bytes() == before, "and the file that was there is still there"


def test_a_picture_the_pass_embedded_is_taken_back_out(tmp_path, one_second_of_mp3):
    """A restore puts the text tags back; the picture was in no key list, so it stayed.

    Measured by the reviewer on a staged run: **23 of 36 files** still carried the cover the pass had
    embedded, with every text tag correctly restored. The picture is written by the same writers, so
    it is recorded and put back like anything else they touch.
    """
    root = tmp_path / "collection"
    root.mkdir()
    path = root / "01 Track.mp3"
    shutil.copy(one_second_of_mp3, path)
    assert precautions.embedded_cover(path) is None, "it starts with none"
    snap = precautions.read(precautions.take(root, tmp_path / "snap.jsonl"))
    assert snap.files[0].picture is None, "and the snapshot says so"

    from noaap.tag import set_picture
    set_picture(path, b"\xff\xd8" + b"the cover the pass embedded" * 100)
    assert precautions.embedded_cover(path) is not None

    precautions.restore(snap, root, apply=True, log=lambda s: None)

    assert precautions.embedded_cover(path) is None, "and it is gone again"


def test_a_picture_that_was_there_is_put_back(tmp_path, one_second_of_mp3):
    """And the other half: a pass that *replaced* a cover has to give the original back.

    The bytes live in a store beside the snapshot, one file per distinct picture, because a cover is
    a hundred kilobytes and eleven thousand of them are not a snapshot.
    """
    root = tmp_path / "collection"
    root.mkdir()
    path = root / "01 Track.mp3"
    shutil.copy(one_second_of_mp3, path)
    theirs = b"\xff\xd8" + b"the picture its owner put there" * 100
    from noaap.tag import set_picture
    set_picture(path, theirs)

    where = precautions.take(root, tmp_path / "snap.jsonl")
    snap = precautions.read(where)
    assert snap.files[0].picture, "the snapshot records which picture it was"
    store = precautions.pictures_for(where)
    assert (store / snap.files[0].picture).read_bytes() == theirs, "and keeps the bytes, once"

    set_picture(path, b"\xff\xd8" + b"what the pass put there instead" * 100)
    done = precautions.restore(snap, root, apply=True, pictures=store, log=lambda s: None)

    assert precautions.embedded_cover(path) == theirs, "theirs, byte for byte"
    assert done.lost == []


def test_without_the_store_a_replaced_picture_is_named_not_guessed(tmp_path, one_second_of_mp3):
    root = tmp_path / "collection"
    root.mkdir()
    path = root / "01 Track.mp3"
    shutil.copy(one_second_of_mp3, path)
    from noaap.tag import set_picture
    set_picture(path, b"\xff\xd8" + b"theirs" * 200)
    snap = precautions.read(precautions.take(root, tmp_path / "snap.jsonl"))
    set_picture(path, b"\xff\xd8" + b"ours" * 200)

    done = precautions.restore(snap, root, apply=True, pictures=None, log=lambda s: None)

    assert done.lost and "the picture it carried" in done.lost[0]


def test_which_empty_folders_may_go_is_a_setting(tmp_path):
    """The user: *"maybe we should make clear empty folders a setting?"* — and it is off by default."""
    root = tmp_path / "collection"
    (root / "theirs").mkdir(parents=True)
    (root / "ours").mkdir()
    (root / "holds something").mkdir()
    (root / "holds something" / "a file").write_text("x")

    mine = precautions.empty_under(root, only=[root / "ours"])
    assert mine == [root / "ours"], "off: only what this pass emptied"

    everything = precautions.empty_under(root, everything=True)
    assert set(everything) == {root / "theirs", root / "ours"}, everything
    assert root not in everything, "never the root"
    assert not any("holds something" in str(p) for p in everything), "never one that holds anything"
