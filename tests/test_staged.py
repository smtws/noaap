"""Taking in a collection that is somewhere else, a batch at a time (DESIGN §9, slice 103).

The user's collection is on a NAS, and 225 GB staged in one go would fill this machine and block
whatever else they are doing: *"think about a configurable batch size from the start (maybe default
to 1/10th of any given running-box's free disk space) (careful, dont count mounted nas shares into
it like many filemanagers do)"*.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from mutagen import File as MFile

from noaap import intake, precautions, staged
from noaap.config import Config
from noaap.service import Service


def _album(folder: Path, titles: list[str], tone: Path, artist: str, album: str) -> None:
    folder.mkdir(parents=True)
    for n, title in enumerate(titles, 1):
        path = folder / f"{n:02d} {title}.opus"
        shutil.copy(tone, path)
        audio = MFile(path)
        audio["title"] = [title]
        audio["artist"] = [artist]
        audio["album"] = [album]
        audio["comment"] = [f"{album} {title}"]
        audio.save()


@pytest.fixture
def elsewhere(tmp_path, one_second_of_sound):
    """A collection standing in for the share, with the owner's own cover in one album."""
    root = tmp_path / "share"
    _album(root / "aphelion" / "nocturnes (2003)", ["First", "Second"], one_second_of_sound,
           "Aphelion", "Nocturnes")
    _album(root / "bramblewood" / "hollow", ["Hollow", "Rime", "Vigil"], one_second_of_sound,
           "Bramblewood", "Hollow")
    (root / "bramblewood" / "hollow" / "cover.jpg").write_bytes(b"\xff\xd8 theirs")
    return root


# -- the batch size ---------------------------------------------------------------------------


def test_the_free_space_is_the_staging_filesystems_and_no_other(tmp_path):
    """The trap the user named: a file manager shows one number for "disk" and counts the share in.

    `statvfs` of the staging path alone. On this machine the two differ by an order of magnitude —
    the laptop's own disk against 5.9 TB on the NAS — so sizing a batch against the wrong one would
    stage hundreds of gigabytes onto a disk that has not got them.
    """
    free, where = staged.free_space(tmp_path)
    mine = os.statvfs(tmp_path)
    expected = mine.f_bavail * mine.f_frsize
    # within a hair of each other: two readings of a live filesystem, taken a moment apart
    assert abs(free - expected) < max(expected // 100, 1 << 20), (free, expected)
    assert where, "and it says which filesystem that was"

    # and the part that matters: a path on another filesystem gives another answer. `/proc` is not
    # the disk, and a share is not the disk either — that is the whole of the user's warning.
    elsewhere, named = staged.free_space(Path("/proc"))
    assert named != where, (named, where)


def test_the_free_space_of_a_folder_that_is_not_there_yet(tmp_path):
    """A staging folder is named before it is made, and still has to answer for its filesystem."""
    free, _ = staged.free_space(tmp_path / "not" / "yet" / "made")
    assert free == staged.free_space(tmp_path)[0]


def test_albums_are_grouped_into_batches_that_fit():
    made = staged.batches([(Path(f"a{i}"), 3) for i in range(7)], 10)
    assert [len(b.albums) for b in made] == [3, 3, 1]
    assert [b.bytes for b in made] == [9, 9, 3]


def test_an_album_bigger_than_a_batch_is_its_own_batch():
    """A twelve-gigabyte live set is still somebody's album: it is named, not refused."""
    made = staged.batches([(Path("big"), 12), (Path("small"), 1)], 10)
    assert [b.bytes for b in made] == [12, 1]
    assert made[0].alone and made[0].bytes > 10


def test_the_batch_order_is_the_listings_order_so_a_resume_means_something():
    albums = [(Path(f"a{i}"), 4) for i in range(5)]
    once = staged.batches(albums, 10)
    again = staged.batches(albums, 10)
    assert [[str(a) for a in b.albums] for b in once] == [[str(a) for a in b.albums] for b in again]


def test_a_record_of_another_root_is_not_this_ones(tmp_path):
    where = tmp_path / staged.STAGED
    staged.write_done(where, tmp_path / "somewhere-else", {"a"})
    assert staged.read_done(where, tmp_path / "somewhere-else") == {"a"}
    assert staged.read_done(where, tmp_path / "here") == set()


# -- the round trip ---------------------------------------------------------------------------


def _service(tmp_path, root):
    cfg = Config(library_root=root, musicbrainz=False, lyrics=False)
    return Service(cfg, root, log=lambda s: None)


QUIET = intake.Choices(names="scheme", musicbrainz=False, lyrics=False)


def test_a_dry_run_says_the_batches_and_writes_nothing_anywhere(elsewhere, tmp_path):
    before = {str(p.relative_to(elsewhere)): p.read_bytes()
              for p in sorted(elsewhere.rglob("*")) if p.is_file()}
    said = []

    done = staged.take_in_staged(_service(tmp_path, elsewhere), elsewhere, QUIET,
                                 staging=tmp_path / "staging", batch_size=10_000_000,
                                 dry_run=True, log=said.append)

    assert done.albums == 2 and done.batches >= 1 and done.tracks == 5
    assert any("GB free" in line for line in said), said
    assert any("would be renamed" in line for line in said), said
    assert {str(p.relative_to(elsewhere)): p.read_bytes()
            for p in sorted(elsewhere.rglob("*")) if p.is_file()} == before
    assert not (tmp_path / "staging").exists(), "not even the staging folder"


def test_the_round_trip_leaves_the_share_as_the_pass_would_have(elsewhere, tmp_path):
    """One batch per album, so the copy back is exercised twice, and nothing is lost either way."""
    staging = tmp_path / "staging"

    done = staged.take_in_staged(_service(tmp_path, elsewhere), elsewhere, QUIET,
                                 staging=staging, batch_size=1, dry_run=False, log=lambda s: None)

    assert done.batches == 2 and done.tracks == 5 and done.unverified == []
    assert done.copied_out > 0 and done.copied_back > 0
    assert not (staging / "batch").exists(), "the staging copy is gone"

    on_share = sorted(str(p.relative_to(elsewhere)) for p in elsewhere.rglob("*") if p.is_file())
    assert "Aphelion/Nocturnes/Aphelion - Nocturnes - 01 - First.opus" in on_share, on_share
    assert not any("nocturnes (2003)" in name for name in on_share), "the old folder is gone"
    assert "Bramblewood/Hollow/cover.jpg" in on_share, "their cover came along"
    assert all(not name.endswith(staged.PART) for name in on_share), "no half-copied file left"
    assert len([n for n in on_share if n.endswith(".opus")]) == 5, on_share


def test_nothing_on_the_share_is_removed_until_its_replacement_is_verified(elsewhere, tmp_path,
                                                                           monkeypatch):
    """The rule that makes the share its own way back: add and replace, and remove only after.

    A digest that does not match stands for any reason a copy back can go wrong — a dropped
    connection, a share that filled up — and in every one of them the original has to still be there.
    """
    before = {str(p.relative_to(elsewhere)): p.read_bytes()
              for p in sorted(elsewhere.rglob("*")) if p.is_file()}
    real = staged.bytes_sha

    def wrong(path: Path) -> str:
        """One file arrives on the share wrong — and only the share's copy of it reads wrong, since
        a fake that lies about both sides would have them agree, which is no test at all."""
        said = real(path)
        inside = str(path).startswith(str(elsewhere))
        return said + "-wrong" if inside and "Rime" in path.name else said

    monkeypatch.setattr(staged, "bytes_sha", wrong)
    said = []
    done = staged.take_in_staged(_service(tmp_path, elsewhere), elsewhere, QUIET,
                                 staging=tmp_path / "staging", batch_size=10_000_000,
                                 dry_run=False, log=said.append)

    assert done.unverified, "the mismatch was noticed"
    assert done.superseded == [], "and so nothing of the share's was removed"
    for name, was in before.items():
        assert (elsewhere / name).is_file(), f"{name} is still there"
        assert (elsewhere / name).read_bytes() == was, f"{name} is unchanged"
    assert any("nothing of the share's own was removed" in line for line in said), said


def test_a_batch_already_done_is_not_done_again(elsewhere, tmp_path):
    staging = tmp_path / "staging"
    staged.take_in_staged(_service(tmp_path, elsewhere), elsewhere, QUIET, staging=staging,
                          batch_size=1, dry_run=False, log=lambda s: None)
    again = staged.take_in_staged(_service(tmp_path, elsewhere), elsewhere, QUIET, staging=staging,
                                  batch_size=1, dry_run=False, log=lambda s: None)
    assert again.done == [], "both batches were finished by the first run"
    assert again.copied_out == 0 and again.copied_back == 0, "and nothing crossed again"


def test_a_restore_from_the_batchs_snapshot_reaches_the_share(elsewhere, tmp_path):
    """The snapshot is taken on the staging copy, and its paths are relative — so a restore can be
    pointed at the share, which is the root those paths were read from in the first place."""
    staging = tmp_path / "keeping"
    before = {str(p.relative_to(elsewhere)): p.read_bytes()
              for p in sorted(elsewhere.rglob("*")) if p.is_file()}

    # keep the batch's snapshot by taking it ourselves, the way `--restore` would be given one
    source = __import__("noaap.sources", fromlist=["get"]).get("folder",
                                                               Config(library_root=elsewhere))
    one = staged.batches(staged.album_sizes(elsewhere, source), 1)[0]
    copy = staging / "batch"
    for album in one.albums:
        (copy / album.relative_to(elsewhere)).parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(album, copy / album.relative_to(elsewhere))
    snapshot = precautions.take(copy, tmp_path / "batch.jsonl")

    staged.take_in_staged(_service(tmp_path, elsewhere), elsewhere, QUIET, staging=staging,
                          batch_size=1, dry_run=False, log=lambda s: None)
    assert (elsewhere / "Aphelion" / "Nocturnes").is_dir(), "the share has noaap's names now"

    done = precautions.restore(precautions.read(snapshot), elsewhere, apply=True, log=lambda s: None)

    assert done.missing == [] and done.changed == [] and done.lost == []
    for name in before:
        if name.startswith("aphelion/"):
            assert (elsewhere / name).is_file(), f"{name} is back under its own name"
    # names, tags and a proof of the recording — not the bytes, because a tag round-trip is not
    # byte-identical and there are no kept originals here. **Before the copy back, the share's own
    # untouched file is the byte-for-byte way back**; after it, this is.
    from noaap.tag import raw_tags
    recorded = {r.path: r.tags for r in precautions.read(snapshot).files}
    name = "aphelion/nocturnes (2003)/01 First.opus"
    assert raw_tags(elsewhere / name) == recorded[name], "every tag it had, as it had it"


def test_a_finished_run_leaves_a_way_back(elsewhere, tmp_path):
    """Which a first version of this did not, and that is the one thing the package is for.

    The share's own untouched file is the way back only until its replacement is verified and the
    superseded one removed. After that the batch's snapshot and the record of what the pass made are
    the only way back there is — and they were being deleted with the staging copy, so a finished run
    could not be undone at all. They cost half a megabyte a batch.
    """
    staging = tmp_path / "staging"

    done = staged.take_in_staged(_service(tmp_path, elsewhere), elsewhere, QUIET, staging=staging,
                                 batch_size=1, dry_run=False, log=lambda s: None)

    assert len(done.snapshots) == done.batches == 2
    for name in done.snapshots:
        snapshot = Path(name)
        assert snapshot.is_file(), name
        assert intake.made_path(snapshot).is_file(), "and what the pass created, to take away again"
        assert not intake.state_path(snapshot).exists(), "the resume file is spent and goes"

    # and it reaches the share: the first batch put back under the names its owner gave it
    snapshot = Path(done.snapshots[0])
    back = precautions.restore(precautions.read(snapshot), elsewhere, apply=True,
                               made=intake.read_made(intake.made_path(snapshot)),
                               folders=intake.read_made(intake.made_path(snapshot), folders=True),
                               log=lambda s: None)
    assert back.missing == [] and back.changed == []
    assert (elsewhere / "aphelion" / "nocturnes (2003)" / "01 First.opus").is_file()
    assert not (elsewhere / "Aphelion").exists(), "and noaap's folder is gone again"


def test_a_share_that_folds_case_does_not_lose_the_file_it_just_got(tmp_path, one_second_of_sound):
    """The worst thing this package has done, and comparing with a local run is what caught it.

    The share is case-insensitive and this machine is not. An album folder differing from the scheme
    only in its case — `iii` against `III` — comes back to the *same* directory on the share, and
    where the file names were already the scheme's, the superseded path **is** the path just written.
    On the real share that deleted 14 files after verifying every one of them.

    The copy back is called directly here, with a symlink standing in for the folding, because a
    symlink inside the tree would be copied into the staging folder and change the case the pass sees.
    """
    share = tmp_path / "share"
    folded = share / "Der W" / "iii"
    folded.mkdir(parents=True)
    staging = tmp_path / "batch"
    scheme = staging / "Der W" / "III"          # what the pass renamed it to, on this machine
    scheme.mkdir(parents=True)
    names = []
    for n, title in enumerate(["Operation", "Mordballaden"], 1):
        name = f"Der W - III - {n:02d} - {title}.opus"   # already the scheme's own names
        shutil.copy(one_second_of_sound, scheme / name)
        shutil.copy(one_second_of_sound, folded / name)
        names.append(name)
    (share / "Der W" / "III").symlink_to("iii")         # what a case-folding share does
    was = {r.path: r for r in precautions.read(precautions.take(staging, tmp_path / "s.jsonl")).files}
    was |= {f"Der W/iii/{name}": None for name in names}   # the share knows them by the folded name

    done = staged.Staged()
    staged._copy_back(share, staging, was, done, lambda s: None)

    assert done.unverified == []
    assert sorted(p.name for p in folded.iterdir()) == sorted(names), "every file still there"
    assert done.superseded == [], "and nothing was called superseded that we had just written"


def test_an_empty_folder_of_the_owners_is_not_tidied_away(elsewhere, tmp_path):
    """Sweeping the whole root for empty directories removed one from the real share."""
    (elsewhere / "aphelion" / "a folder they left empty").mkdir()

    staged.take_in_staged(_service(tmp_path, elsewhere), elsewhere, QUIET,
                          staging=tmp_path / "staging", batch_size=10_000_000, dry_run=False,
                          log=lambda s: None)

    assert (elsewhere / "aphelion" / "a folder they left empty").is_dir()


def _tags_and_picture(root: Path) -> dict[str, tuple]:
    from noaap.tag import embedded_cover, raw_tags
    return {str(p.relative_to(root)): (raw_tags(p), embedded_cover(p))
            for p in sorted(root.rglob("*")) if p.suffix.lower() == ".opus"}


def test_after_a_staged_run_and_its_restore_the_share_is_the_reference(elsewhere, tmp_path):
    """The reviewer's own check (R-354): every audio file equal to the reference in tags **and
    pictures**, and nothing of the pass's left behind.

    It failed twice. A restore put the text tags back and left the cover the pass had embedded in
    23 of 36 files; and the record of what the pass made was stamped with the staging copy's root, so
    a restore pointed at the share read nothing of it and left every plan in place.
    """
    reference = tmp_path / "reference"
    shutil.copytree(elsewhere, reference)
    was = _tags_and_picture(reference)
    staging = tmp_path / "staging"

    done = staged.take_in_staged(_service(tmp_path, elsewhere), elsewhere, QUIET, staging=staging,
                                 batch_size=1, dry_run=False, log=lambda s: None)
    assert done.unverified == []
    assert any(precautions.embedded_cover(p) for p in elsewhere.rglob("*.opus")), \
        "the pass did embed a cover, or this proves nothing"

    for name in done.snapshots:
        snapshot = Path(name)
        precautions.restore(precautions.read(snapshot), elsewhere, apply=True,
                            made=intake.read_made(intake.made_path(snapshot), elsewhere),
                            folders=intake.read_made(intake.made_path(snapshot), elsewhere,
                                                     folders=True),
                            pictures=precautions.pictures_for(snapshot), log=lambda s: None)

    assert _tags_and_picture(elsewhere) == was, "every tag and every picture as the reference has it"
    assert list(elsewhere.rglob(".ytalbum.json")) == [], "and no plan of the pass's left"
    assert list(elsewhere.rglob("cover.*")) == [elsewhere / "bramblewood" / "hollow" / "cover.jpg"], \
        "the cover that was theirs stays; one the pass fetched goes"


def test_an_empty_folder_of_the_owners_goes_only_when_the_setting_says_so(elsewhere, tmp_path):
    """R-355, the user: *"maybe we should make clear empty folders a setting?"* — `remove_empty_folders`."""
    (elsewhere / "aphelion" / "one they left empty").mkdir()
    cfg = Config(library_root=elsewhere, musicbrainz=False, lyrics=False)
    assert cfg.remove_empty_folders is False, "off by default"

    staged.take_in_staged(Service(cfg, elsewhere, log=lambda s: None), elsewhere, QUIET,
                          staging=tmp_path / "off", batch_size=10_000_000, dry_run=False,
                          log=lambda s: None)
    assert (elsewhere / "aphelion" / "one they left empty").is_dir(), "off: theirs is left alone"

    on = Config(library_root=elsewhere, musicbrainz=False, lyrics=False, remove_empty_folders=True)
    said = []
    intake.take_in(Service(on, elsewhere, log=lambda s: None), elsewhere,
                   intake.Choices(names="scheme", musicbrainz=False, lyrics=False),
                   dry_run=False, snapshot=tmp_path / "on.jsonl", log=said.append)
    assert not (elsewhere / "aphelion" / "one they left empty").exists(), "on: it goes"
    assert any("removed the empty folder" in line for line in said), said


def test_the_same_name_is_what_the_filesystem_says_it_is(tmp_path, monkeypatch,
                                                         one_second_of_sound):
    """The guard that was meant to stop the case-folding loss **did not work on the share**.

    It compared `Path.samefile`, and CIFS hands out a different inode for each spelling of one file
    — measured on the user's own share, 130595 against 130597 — so it answered "a different file"
    every time and fourteen files of an album were deleted after being verified. The old case passed
    because a symlink on ext4 really does share an inode. So the filesystem is **asked** whether it
    folds case, and here that answer is forced to `True` while the inodes stay honestly different.
    """
    share = tmp_path / "share"
    album = share / "Der W" / "iii"
    album.mkdir(parents=True)
    staging = tmp_path / "batch"
    scheme = staging / "Der W" / "III"
    scheme.mkdir(parents=True)
    names = []
    for n, title in enumerate(["Operation", "Mordballaden"], 1):
        name = f"Der W - III - {n:02d} - {title}.opus"
        shutil.copy(one_second_of_sound, scheme / name)
        shutil.copy(one_second_of_sound, album / name)      # the share's own copy, same name
        names.append(name)
    was = {f"Der W/iii/{name}": None for name in names}
    monkeypatch.setattr(staged.precautions, "folds_case", lambda where: True)

    done = staged.Staged()
    staged._copy_back(share, staging, was, done, lambda s: None)

    assert done.superseded == [], "nothing the pass had just written was called superseded"
    assert sorted(p.name for p in album.iterdir()) == sorted(names), "every file still there"


def test_a_restore_does_not_reach_a_folder_beside_the_one_the_pass_made(tmp_path,
                                                                       one_second_of_sound):
    """It cleared every empty directory under a husk, and counted the husk's parents as husks too.

    Which reached `Der W/Autonomie` — empty in the owner's collection, a sibling of the album the
    pass had moved, recorded by nobody. Found by round 1 of the gate.
    """
    root = tmp_path / "collection"
    album = root / "Aphelion" / "nocturnes (2003)"
    album.mkdir(parents=True)
    shutil.copy(one_second_of_sound, album / "01 First.opus")
    (root / "Aphelion" / "one they left empty").mkdir()
    (album / ".thumb").mkdir()
    (album / ".thumb" / "cover.jpg.jpg").write_bytes(b"\xff\xd8 theirs")
    snapshot = precautions.take(root, tmp_path / "snap.jsonl")
    snap = precautions.read(snapshot)
    moved = root / "Aphelion" / "Nocturnes"                  # what the pass would do
    album.rename(moved)

    precautions.restore(snap, root, apply=True, folders=["Aphelion/Nocturnes"], log=lambda s: None)

    assert (album / "01 First.opus").is_file(), "their album is back"
    assert (album / ".thumb" / "cover.jpg.jpg").is_file(), "with their thumbnail in it"
    assert (root / "Aphelion" / "one they left empty").is_dir(), "and the folder beside it is theirs"
    assert not moved.exists(), "while the folder the pass made is gone"


def test_the_original_is_moved_aside_so_a_restore_is_byte_for_byte(elsewhere, tmp_path):
    """R-364, from round 1: 40 files came back with the same tags, the same picture, other bytes.

    A tag round-trip through mutagen is not byte-identical, and in a staged run nothing else kept
    the original — the share's own copy was the way back only until it was deleted. So it is **moved**
    into a store beside the collection instead, which is a rename within the share and costs nothing
    over the network, and `restore(kept=…)` copies the bytes back from there.
    """
    was = {str(p.relative_to(elsewhere)): p.read_bytes()
           for p in sorted(elsewhere.rglob("*")) if p.is_file()}

    done = staged.take_in_staged(_service(tmp_path, elsewhere), elsewhere, QUIET,
                                 staging=tmp_path / "staging", batch_size=10_000_000,
                                 dry_run=False, log=lambda s: None)

    aside = staged.aside_for(elsewhere)
    assert aside.is_dir() and done.aside, "the store is there and the pass says what it holds"
    assert done.moved_aside > 0
    for name in done.aside:
        assert (aside / name).read_bytes() == was[name], f"{name} is kept byte for byte"
    assert aside.resolve() not in elsewhere.resolve().parents, "beside the collection, not inside it"

    for name in done.snapshots:
        snapshot = Path(name)
        precautions.restore(precautions.read(snapshot), elsewhere, apply=True, kept=aside,
                            made=intake.read_made(intake.made_path(snapshot), elsewhere),
                            folders=intake.read_made(intake.made_path(snapshot), elsewhere,
                                                     folders=True),
                            pictures=precautions.pictures_for(snapshot), log=lambda s: None)

    now = {str(p.relative_to(elsewhere)): p.read_bytes()
           for p in sorted(elsewhere.rglob("*")) if p.is_file()}
    assert now == was, "every file back, byte for byte"


def test_a_file_whose_name_does_not_change_is_moved_aside_before_it_is_written_over(tmp_path,
                                                                                    one_second_of_sound):
    """The superseded loop only ever sees names that changed; this one would have been overwritten."""
    share = tmp_path / "share"
    album = share / "Aphelion" / "Nocturnes"      # already the scheme's folder *and* file names
    album.mkdir(parents=True)
    path = album / "Aphelion - Nocturnes - 01 - First.opus"
    shutil.copy(one_second_of_sound, path)
    audio = MFile(path)
    audio["title"] = ["First"]
    audio["artist"] = ["Aphelion"]
    audio["album"] = ["Nocturnes"]
    audio.save()
    was = path.read_bytes()

    done = staged.take_in_staged(_service(tmp_path, share), share, QUIET,
                                 staging=tmp_path / "staging", batch_size=10_000_000,
                                 dry_run=False, log=lambda s: None)

    assert done.superseded == [], "nothing was renamed, so nothing is superseded"
    assert done.aside == ["Aphelion/Nocturnes/Aphelion - Nocturnes - 01 - First.opus"], done.aside
    assert (staged.aside_for(share) / done.aside[0]).read_bytes() == was, "kept before it was written"
    assert path.read_bytes() != was, "and the file itself was rewritten"
