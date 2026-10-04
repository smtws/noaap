"""What a repair skips, and the one question that decides it (DESIGN §9, slice 124; R-465).

The repair over the user's collection reported "25 albums would be tidied up" and only **four** held
a real change. Three of those four had never been applied by any repair: `Mono Inc. — Head Under
Water` (13 files to rename by slice 116), `Nightwish — Human. :II: Nature.` (17 files and a cover)
and `Schandmaul — Sinnfonie`, whose 66 files said three discs where the album has four. They
surfaced by accident, because the disc lookup of slice 123 happened to un-skip their albums.

The skip asked eleven questions — misplaced, borrowed, measured, stale, refound, strays, moved
sources, recuts, tails, comments, Windows Media frames — and none of them asked whether a **file**
would be renamed or retagged. So slice 116's renames reached only the albums that failed the skip
for some other reason: `Mono Inc. — Temple Of The Torn` was renamed because it still carried ID3v1
tails, and `Head Under Water`, which had none, was not.
"""

from __future__ import annotations

import shutil

import pytest
from mutagen.id3 import ID3, TALB, TIT2, TPE1, TPE2, TRCK
from test_duplicate_numbers import TwoDiscs
from test_intake import QUIET

from noaap import intake
from noaap.config import Config
from noaap.download import load_plan, save_plan
from noaap.service import Service


def an_mp3(folder, number, title, one_second_of_mp3, *, artist="A Band", album="An Album"):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{number:02d} {title}.mp3"
    shutil.copy(one_second_of_mp3, path)
    tags = ID3()
    for frame in (TIT2(encoding=3, text=[title]), TPE1(encoding=3, text=[artist]),
                  TPE2(encoding=3, text=[artist]), TALB(encoding=3, text=[album]),
                  TRCK(encoding=3, text=[str(number)])):
        tags.add(frame)
    tags.save(path, v1=0)
    return path


def a_library(root, **settings):
    cfg = Config(library_root=root, musicbrainz=False, lyrics=False, **settings)
    return Service(cfg, root, log=lambda s: None)


def taken_in(root):
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)


def headers(said):
    return [line for line in said if line.startswith("=== ")]


# -- an album whose only pending change is a rename --------------------------------------------


def test_an_album_whose_only_change_is_a_scheme_rename_is_not_skipped(tmp_path, one_second_of_mp3):
    """`Mono Inc. — Head Under Water`, in miniature: the band's name ends in a dot, the artist
    folder has never had one, and slice 116 makes the files match it. Nothing else about the album
    is out of place — so every earlier repair walked past it."""
    root = tmp_path / "collection"
    an_mp3(root / "Mono Inc." / "Head Under Water", 1, "Burn Me", one_second_of_mp3,
           artist="Mono Inc.", album="Head Under Water")
    taken_in(root)
    album = root / "Mono Inc" / "Head Under Water"
    assert album.is_dir(), "the artist folder has no dot"
    renamed = album / "Mono Inc. - Head Under Water - 01 - Burn Me.mp3"
    plan = load_plan(album)
    plan.tracks[0].filename = renamed.name
    next(album.glob("*.mp3")).rename(renamed)
    save_plan(plan, album)

    said = []
    service = a_library(root, retag_adopted=True, rename_adopted=True)
    service.log = said.append
    service.repair(dry_run=True)

    assert headers(said), "the album is reached"
    assert [line for line in said if "would be renamed" in line and "Mono Inc -" in line], said


def test_and_the_apply_does_it(tmp_path, one_second_of_mp3):
    root = tmp_path / "collection"
    an_mp3(root / "Mono Inc." / "Head Under Water", 1, "Burn Me", one_second_of_mp3,
           artist="Mono Inc.", album="Head Under Water")
    taken_in(root)
    album = root / "Mono Inc" / "Head Under Water"
    renamed = album / "Mono Inc. - Head Under Water - 01 - Burn Me.mp3"
    plan = load_plan(album)
    plan.tracks[0].filename = renamed.name
    next(album.glob("*.mp3")).rename(renamed)
    save_plan(plan, album)

    a_library(root, retag_adopted=True, rename_adopted=True).repair()

    assert sorted(p.name for p in album.glob("*.mp3")) == \
        ["Mono Inc - Head Under Water - 01 - Burn Me.mp3"]


def test_an_album_with_nothing_pending_is_still_skipped(tmp_path, one_second_of_mp3):
    """The skip is what keeps a repair over 1311 albums cheap: it must still skip."""
    root = tmp_path / "collection"
    an_mp3(root / "A Band" / "An Album", 1, "One", one_second_of_mp3)
    taken_in(root)

    said = []
    service = a_library(root, retag_adopted=True, rename_adopted=True)
    service.log = said.append
    service.repair(dry_run=True)

    assert headers(said) == [], said
    assert [line for line in said if "0 album(s) would be tidied up" in line], said


# -- and a partial disc match is not a reason to stay -------------------------------------------


def test_an_album_the_disc_lookup_could_not_settle_is_skipped(tmp_path, one_second_of_sound):
    """21 of the collection's 25 duplicate-number albums are these: MusicBrainz found nothing or
    only part of a release, nothing was assigned, and nothing else about them is out of place. A
    full pass over each of them on every repair, for ever, is the cost of getting this wrong."""
    from mutagen import File as MFile

    root = tmp_path / "collection"
    album = root / "A Band" / "Twice"
    album.mkdir(parents=True)
    for number, title in ((1, "One"), (2, "Two"), (1, "One (mix)"), (2, "Two (mix)")):
        path = album / f"{number:02d} {title}.opus"
        shutil.copy(one_second_of_sound, path)
        audio = MFile(path)
        audio["title"], audio["artist"] = [title], ["A Band"]
        audio["albumartist"], audio["album"] = ["A Band"], ["Twice"]
        audio["tracknumber"] = [str(number)]
        audio.save()
    taken_in(root)
    partial = TwoDiscs([["One", "Two"], ["Nothing like it", "Nor this"]], title="Twice")

    said = []
    cfg = Config(library_root=root, musicbrainz=True, lyrics=False, retag_adopted=True)
    service = Service(cfg, root, log=said.append, mb=partial)
    service.repair(dry_run=True)

    assert [line for line in said if "did not settle it" in line], said
    assert headers(said) == [], "reported, and then skipped like any other tidy album"


def test_an_assignment_does_keep_the_album(tmp_path, one_second_of_sound):
    from mutagen import File as MFile

    root = tmp_path / "collection"
    album = root / "A Band" / "Twice"
    album.mkdir(parents=True)
    for number, title in ((1, "One"), (2, "Two"), (1, "One (mix)"), (2, "Two (mix)")):
        path = album / f"{number:02d} {title}.opus"
        shutil.copy(one_second_of_sound, path)
        audio = MFile(path)
        audio["title"], audio["artist"] = [title], ["A Band"]
        audio["albumartist"], audio["album"] = ["A Band"], ["Twice"]
        audio["tracknumber"] = [str(number)]
        audio.save()
    taken_in(root)
    whole = TwoDiscs([["One", "Two"], ["One (mix)", "Two (mix)"]], title="Twice")

    said = []
    cfg = Config(library_root=root, musicbrainz=True, lyrics=False, retag_adopted=True)
    Service(cfg, root, log=said.append, mb=whole).repair(dry_run=True)

    assert [line for line in said if "would be" in line and "assigned from MusicBrainz" in line]
    assert headers(said), "assigned, so the album stays for the pass that writes it"


def test_the_one_line_that_promises_nothing_does_not_keep_an_album(tmp_path, one_second_of_mp3):
    """`would_do` ends with "a cover would be saved beside the album if a picture can be found in
    its files" where the only place to look is the album's own files. It promises nothing, and an
    album with no cover beside it would otherwise never be skipped again.

    A cover that really can be had says where from, and that does keep the album.
    """
    from noaap.download import MAYBE_COVER, would_do

    root = tmp_path / "collection"
    an_mp3(root / "A Band" / "An Album", 1, "One", one_second_of_mp3)
    taken_in(root)
    album = root / "A Band" / "An Album"
    plan = load_plan(album)
    lines = would_do(plan, album, None, root, intake.Choices().as_treatment())

    assert [line for line in lines if MAYBE_COVER in line], "the line is there"
    assert not [line for line in lines if MAYBE_COVER not in line], "and it is the only one"

    said = []
    service = a_library(root, retag_adopted=True, rename_adopted=True)
    service.log = said.append
    service.repair(dry_run=True)

    assert headers(said) == [], said
