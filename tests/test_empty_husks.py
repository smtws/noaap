"""An empty folder a rename left behind (DESIGN §9, slice 115; R-455, item 1).

After the user's collection came in, **91 empty album folders** were on the share — every one an
album whose folder the scheme renamed and whose `.thumb` the pass took away. `Subway to Sally/
bastard` beside `BASTARD` with its 15 files, `mcmxcv` beside `MCMXCV`; the backup shows 14 files
under `bastard` before the pass, so the pass emptied it and left the directory.

The cause is one line of `empty_under`: it asked every candidate whether it was empty **before
removing any of them**, so deepest-first order decided nothing. `bastard` was asked while its
`.thumb` was still inside, answered "not empty", and stayed once `.thumb` went.
"""

from __future__ import annotations

import shutil

import pytest
from test_collisions import a_service
from test_intake import QUIET

from noaap import intake, precautions
from noaap.config import Config
from noaap.service import Service


def an_album_with_a_thumb(root, artist, album, titles, one_second_of_mp3):
    from mutagen.id3 import ID3, TALB, TIT2, TPE1, TPE2, TRCK

    folder = root / artist / album
    (folder / ".thumb").mkdir(parents=True)
    (folder / ".thumb" / "cover.jpg.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    for number, title in enumerate(titles, 1):
        path = folder / f"{number:02d} {title}.mp3"
        shutil.copy(one_second_of_mp3, path)
        tags = ID3()
        for frame in (TIT2(encoding=3, text=[title]), TPE1(encoding=3, text=[artist]),
                      TPE2(encoding=3, text=[artist]), TALB(encoding=3, text=[album.upper()]),
                      TRCK(encoding=3, text=[str(number)])):
            tags.add(frame)
        tags.save(path, v1=0)
    return folder


# -- the one line ---------------------------------------------------------------------------------


def test_a_folder_whose_only_child_also_goes_is_empty_too(tmp_path):
    """`bastard` holds nothing but `.thumb`, and `.thumb` is on the list. Both go, deepest first —
    and so does the artist folder above them, which is then holding nothing of anybody's."""
    root = tmp_path / "collection"
    husk = root / "A Band" / "bastard"
    (husk / ".thumb").mkdir(parents=True)

    out = precautions.empty_under(root, [husk, husk / ".thumb"])

    assert out == [husk / ".thumb", husk, root / "A Band"], "deepest first, up to the root"


def test_an_artist_folder_with_another_album_in_it_stays(tmp_path):
    """The chain stops where something of the owner's is: the artist still has an album."""
    root = tmp_path / "collection"
    husk = root / "A Band" / "bastard"
    (husk / ".thumb").mkdir(parents=True)
    (root / "A Band" / "Another Album").mkdir(parents=True)
    (root / "A Band" / "Another Album" / "01 One.mp3").write_bytes(b"audio")

    out = precautions.empty_under(root, [husk, husk / ".thumb"])

    assert out == [husk / ".thumb", husk]


def test_a_folder_that_still_holds_a_file_is_not_empty(tmp_path):
    root = tmp_path / "collection"
    folder = root / "A Band" / "An Album"
    (folder / ".thumb").mkdir(parents=True)
    (folder / "01 One.mp3").write_bytes(b"not empty")

    out = precautions.empty_under(root, [folder, folder / ".thumb"])

    assert out == [folder / ".thumb"], "the file keeps its folder, and the list says so"


def test_three_deep_collapse_in_one_pass(tmp_path):
    root = tmp_path / "collection"
    deep = root / "A Band" / "An Album" / "CD1" / ".thumb"
    deep.mkdir(parents=True)

    out = precautions.empty_under(root, [deep])

    assert out == [deep, deep.parent, deep.parent.parent, root / "A Band"], \
        "a chain of empties goes in one pass, deepest first"


def test_the_root_itself_is_never_a_candidate(tmp_path):
    root = tmp_path / "collection"
    root.mkdir()
    assert precautions.empty_under(root, [root]) == []
    assert precautions.empty_under(root, everything=True) == []


def test_a_folder_outside_the_root_is_never_a_candidate(tmp_path):
    root = tmp_path / "collection"
    root.mkdir()
    elsewhere = tmp_path / "somewhere else"
    elsewhere.mkdir()

    assert precautions.empty_under(root, [elsewhere]) == []


# -- and the pass that leaves them --------------------------------------------------------------


def test_a_staged_pass_leaves_no_husk_behind(tmp_path, one_second_of_mp3):
    """The real shape, and it needs the staged pass to reproduce.

    A plain take-in renames the folder in place and there is nothing left to be empty. Staged, the
    rename happens on the copy and the share gets the new path written beside the old one — whose
    files the superseded loop then moves aside. That is where `Subway to Sally/bastard` was left
    standing beside `BASTARD` with its `.thumb` gone.
    """
    from noaap import staged

    share = tmp_path / "share"
    an_album_with_a_thumb(share, "Subway to Sally", "bastard", ["One", "Two"], one_second_of_mp3)
    cfg = Config(library_root=share, musicbrainz=False, lyrics=False)
    service = Service(cfg, share, log=lambda s: None)

    done = staged.take_in_staged(service, share, QUIET, staging=tmp_path / "staging",
                                 batch_size=10_000_000, dry_run=False, log=lambda s: None)

    assert done.tracks == 2 and done.unverified == []
    left = sorted(str(p.relative_to(share)) for p in share.rglob("*")
                  if p.is_dir() and not any(p.iterdir()))
    assert left == [], f"empty folders left behind: {left}"
    assert (share / "Subway to Sally" / "BASTARD").is_dir(), "and the album is under its new name"


def test_repair_names_the_husks_when_the_setting_is_off(tmp_path, one_second_of_mp3):
    """Removing them is the setting; saying they are there is not — the 91 on the share are this."""
    root = tmp_path / "collection"
    an_album_with_a_thumb(root, "A Band", "An Album", ["One"], one_second_of_mp3)
    intake.take_in(a_service(root), root, QUIET, dry_run=False, log=lambda s: None)
    husk = root / "A Band" / "husk"
    (husk / ".thumb").mkdir(parents=True)

    said = []
    service = a_service(root)
    service.log = said.append
    service.repair(dry_run=True)

    named = [line for line in said if "empty folder(s) are under the library" in line]
    assert named, said
    assert "husk" in named[0]
    assert husk.is_dir(), "a dry run removes nothing, and the setting is off anyway"

    service.repair()

    assert husk.is_dir(), "`remove_empty_folders` is off: named, not removed"


def test_repair_removes_them_when_the_library_asks(tmp_path, one_second_of_mp3):
    root = tmp_path / "collection"
    an_album_with_a_thumb(root, "A Band", "An Album", ["One"], one_second_of_mp3)
    intake.take_in(a_service(root), root, QUIET, dry_run=False, log=lambda s: None)
    husk = root / "A Band" / "husk"
    (husk / ".thumb").mkdir(parents=True)

    cfg = Config(library_root=root, musicbrainz=False, lyrics=False, remove_empty_folders=True)
    said = []
    Service(cfg, root, log=said.append).repair()

    assert not husk.exists(), "and its `.thumb` with it, in one pass"
    assert [line for line in said if "removed the empty folder" in line]
