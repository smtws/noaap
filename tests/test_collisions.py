"""Two albums, or two tracks, that would be given one name (DESIGN §9, slice 103; R-410, ruling 1).

The user's own collection holds a box set as three sibling folders — `… - Gestern`, `… - Heute`,
`… - Morgen` — whose files all state one album. The scheme gives the three folders one name, so the
first to move would own it and the other two would stay, half the box filed under noaap's name and
half under its owner's, with three plans aimed at one directory. The same is true inside a folder of
two files that want one filename: `old.rename(new)` does not overwrite, so one file silently keeps
its name while the plan records the name it never got.

Both are refused before anything moves, and both say so in the dry run and in the apply.
"""

from __future__ import annotations

import shutil

import pytest
from mutagen import File as MFile
from test_intake import QUIET, collection, service

from noaap import intake, sources
from noaap.config import Config
from noaap.download import PLAN_FILE, run, would_do
from noaap.plan import clashing_names
from noaap.service import Service


def an_album(folder, titles, *, album, artist, numbers=None, one_second_of_sound):
    """Somebody's own album folder: audio, tags, no plan."""
    folder.mkdir(parents=True)
    for n, title in enumerate(titles, 1):
        path = folder / f"{numbers[n - 1] if numbers else n:02d} {title.replace('?', '-q')}.opus"
        shutil.copy(one_second_of_sound, path)
        audio = MFile(path)
        audio["title"] = [title]
        audio["artist"] = [artist]
        audio["albumartist"] = [artist]
        audio["album"] = [album]
        audio["tracknumber"] = [str(numbers[n - 1] if numbers else n)]
        audio.save()
    return folder


@pytest.fixture
def box(tmp_path, one_second_of_sound):
    """Three folders of one box set, each stating the same album in its tags."""
    root = tmp_path / "collection"
    for part, titles in (("Gestern", ["Dunkler Ort", "Kirche"]),
                         ("Heute", ["Terpentin", "So sind wir"]),
                         ("Morgen", ["Ich bin in Dir", "Dunkel"])):
        an_album(root / "Böhse Onkelz" / f"Gestern war Heute noch Morgen - {part}", titles,
                 album="Gestern war Heute noch Morgen", artist="Böhse Onkelz",
                 one_second_of_sound=one_second_of_sound)
    return root


def a_service(root):
    cfg = Config(library_root=root, musicbrainz=False, lyrics=False)
    return Service(cfg, root, log=lambda s: None)


def test_three_folders_that_would_become_one_are_named_and_left_alone(box):
    said = []
    done = intake.take_in(a_service(box), box, QUIET, dry_run=True, log=said.append)

    warned = [line for line in said if line.startswith("⚠")]
    assert len(warned) == 1, said
    for part in ("Gestern", "Heute", "Morgen"):
        assert f"Gestern war Heute noch Morgen - {part}" in warned[0]
    assert "would be filed under one name, Böhse Onkelz/Gestern war Heute noch Morgen" in warned[0]
    # **and it does not call them the same album** (R-418): two folders may be two halves of one
    assert "noaap does not merge folders" in warned[0]
    assert "left as they are" in warned[0]
    # and not one of them is planned for: three refused, nothing adopted
    assert done.adopted == 0
    assert len(done.refused) == 3


def test_the_apply_leaves_all_three_where_they_are(box):
    intake.take_in(a_service(box), box, QUIET, dry_run=False, log=lambda s: None)

    folders = sorted(p.name for p in (box / "Böhse Onkelz").iterdir() if p.is_dir())
    assert folders == ["Gestern war Heute noch Morgen - Gestern",
                       "Gestern war Heute noch Morgen - Heute",
                       "Gestern war Heute noch Morgen - Morgen"]
    # no plan was written into any of them, so no later pass takes one for a noaap album
    assert list(box.rglob(PLAN_FILE)) == []
    assert not (box / "Böhse Onkelz" / "Gestern war Heute noch Morgen").exists()


def test_a_collection_without_a_collision_is_taken_in_as_before(collection, service):
    """The check refuses what collides and nothing else."""
    done = intake.take_in(service, collection, QUIET, dry_run=True, log=lambda s: None)
    assert done.adopted == 3
    assert done.refused == []


@pytest.fixture
def one_name_twice(tmp_path, one_second_of_sound):
    """An album whose plan gives two tracks one name — the state the editor can be typed into.

    Two files cannot reach it by their tags alone: two that state one title are one track with two
    copies, which is a different thing and is handled as one. Two *tracks* reach it when somebody
    types a number or a title that another track already has, which the page allows, and then the
    rename is a file silently keeping its name while the plan records the one it did not get.
    """
    root = tmp_path / "collection"
    album = an_album(root / "Eisregen" / "Farbenfinsternis", ["Vorboten", "Angst"],
                     album="Farbenfinsternis", artist="Eisregen",
                     one_second_of_sound=one_second_of_sound)
    source = sources.get("folder", Config(library_root=root))
    source.digests = False
    plan = intake._adopted(album, root, source, log=lambda s: None)
    plan.tracks[1].title = plan.tracks[0].title       # as somebody could type it
    plan.tracks[1].number = plan.tracks[0].number
    return root, album, plan


def test_two_tracks_that_want_one_name_leave_the_album_s_names_alone(one_name_twice):
    root, album, plan = one_name_twice
    assert clashing_names(plan), "the two tracks really do want one name"

    said = would_do(plan, album, library=root, want=QUIET.as_treatment())
    warned = [line for line in said if line.startswith("⚠")]
    assert len(warned) == 1, said
    assert "would all be called" in warned[0]
    assert "the album keeps its own names" in warned[0]
    assert not [line for line in said if "would be renamed" in line]


def test_the_run_renames_neither_of_them(one_name_twice):
    root, album, plan = one_name_twice
    before = sorted(p.name for p in album.glob("*.opus"))

    run(plan, album, sources.get("folder", Config(library_root=root)),
        want=QUIET.as_treatment(), download=False)

    after = sorted(p.name for p in album.glob("*.opus"))
    assert after == before, "both files keep the names their owner gave them"
    # and the plan does not claim a name that is not on disk
    assert sorted(t.filename for t in plan.tracks) == before
