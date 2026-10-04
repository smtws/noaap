"""The lookup that settles an album's discs is not the take-in's alone (DESIGN §9, slice 123; R-462).

`Crematory/Early Years` on the user's share: 18 files numbered 1 to 9 twice, every title of the
second run saying "mix". A take-in asks MusicBrainz which disc each file is on — and a take-in skips
an album that already holds a plan, so once the album was in, nothing could ask again. The answer
existed at MusicBrainz and no pass could fetch it.

So `repair` and `update` ask too, before the skip that would pass a tidy album by.
"""

from __future__ import annotations

import shutil

import pytest
from mutagen import File as MFile
from test_duplicate_numbers import TwoDiscs

from noaap import intake
from noaap.config import Config
from noaap.download import load_plan
from noaap.service import Service

QUIET = intake.Choices(musicbrainz=False, lyrics=False)


@pytest.fixture
def twice(tmp_path, one_second_of_sound):
    """One folder, two runs of 1 to 3 — the shape `Early Years` has, in miniature."""
    root = tmp_path / "collection"
    album = root / "A Band" / "Twice"
    album.mkdir(parents=True)
    for number, title in ((1, "One"), (2, "Two"), (3, "Three"),
                          (1, "One (mix)"), (2, "Two (mix)"), (3, "Three (mix)")):
        path = album / f"{number:02d} {title}.opus"
        shutil.copy(one_second_of_sound, path)
        audio = MFile(path)
        audio["title"], audio["artist"] = [title], ["A Band"]
        audio["albumartist"], audio["album"] = ["A Band"], ["Twice"]
        audio["tracknumber"] = [str(number)]
        audio.save()
    return root, album


def a_library(root, mb=None, **settings):
    cfg = Config(library_root=root, musicbrainz=bool(mb), lyrics=False, **settings)
    return Service(cfg, root, log=lambda s: None, mb=mb)


def taken_in_without_asking(root):
    """In, with its numbers as they stand and the flag the adoption wrote."""
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)


def discs_of(album):
    plan = load_plan(album)
    return sorted({t.disc for t in plan.tracks}), plan


def two_discs():
    return TwoDiscs([["One", "Two", "Three"], ["One (mix)", "Two (mix)", "Three (mix)"]],
                    title="Twice")


# -- the album that could not be asked again ----------------------------------------------------


def test_a_take_in_skips_an_album_that_is_already_in(twice):
    """Why this package exists, stated as a case: the one pass that asked cannot reach it."""
    root, album = twice
    taken_in_without_asking(root)
    assert discs_of(album)[0] == [1], "one disc of six, as the folder states itself"

    said = []
    done = intake.take_in(a_library(root, mb=two_discs()), root,
                          intake.Choices(musicbrainz=True, lyrics=False),
                          dry_run=False, log=said.append)

    assert done.adopted == 0, "already taken in and skipped"
    assert discs_of(album)[0] == [1], "so its discs are still unknown"


def test_repair_asks_and_assigns(twice):
    root, album = twice
    taken_in_without_asking(root)
    assert "needs_a_look" in (load_plan(album).adopted or {})

    said = []
    service = a_library(root, mb=two_discs(), retag_adopted=True)
    service.log = said.append
    service.repair()

    discs, plan = discs_of(album)
    assert discs == [1, 2]
    assert [line for line in said if "discs" in line and "assigned from MusicBrainz" in line], said
    assert "needs_a_look" not in (plan.adopted or {}), "and the flag comes off"
    assert [t.disc for t in plan.tracks] == [1, 1, 1, 2, 2, 2]


def test_the_totals_follow_from_the_real_discs(twice):
    """A disc whose numbers repeat has no length (R-425), so no total was written. Once the discs
    are known each one has three tracks, and the files say so."""
    root, album = twice
    taken_in_without_asking(root)
    from noaap.tag import build_tags

    before = build_tags(load_plan(album), load_plan(album).tracks[0])
    assert "tracktotal" not in before

    a_library(root, mb=two_discs(), retag_adopted=True).repair()

    plan = load_plan(album)
    assert build_tags(plan, plan.tracks[0])["tracktotal"] == "3"


def test_a_repair_check_says_it_and_writes_nothing(twice):
    root, album = twice
    taken_in_without_asking(root)

    said = []
    service = a_library(root, mb=two_discs(), retag_adopted=True)
    service.log = said.append
    service.repair(dry_run=True)

    assert [line for line in said if "would be" in line and "assigned from MusicBrainz" in line], said
    assert discs_of(album)[0] == [1], "a check writes nothing"


def test_update_asks_too(twice, monkeypatch):
    root, album = twice
    taken_in_without_asking(root)
    from noaap.service import Outcome

    service = a_library(root, mb=two_discs(), retag_adopted=True)
    monkeypatch.setattr(service, "fetch", lambda *a, **k: Outcome("ok"))

    service.update_all(report_only=False)

    assert discs_of(album)[0] == [1, 2]


# -- and only those albums, and only on a full match --------------------------------------------


def test_an_album_whose_numbers_do_not_repeat_is_never_asked(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    album = root / "A Band" / "Plain"
    album.mkdir(parents=True)
    for number, title in ((1, "One"), (2, "Two")):
        path = album / f"{number:02d} {title}.opus"
        shutil.copy(one_second_of_sound, path)
        audio = MFile(path)
        audio["title"], audio["artist"] = [title], ["A Band"]
        audio["albumartist"], audio["album"] = ["A Band"], ["Plain"]
        audio["tracknumber"] = [str(number)]
        audio.save()
    taken_in_without_asking(root)

    asked = []

    class Counting(TwoDiscs):
        def search_releases(self, artist, album):
            asked.append(album)
            return super().search_releases(artist, album)

    a_library(root, mb=Counting([["One", "Two"]], title="Plain"), retag_adopted=True).repair()

    assert asked == [], "one lookup per album whose numbers repeat, and no others"


def test_nothing_is_assigned_on_a_partial_match_and_the_line_says_what_it_found(twice):
    root, album = twice
    taken_in_without_asking(root)
    partial = TwoDiscs([["One", "Two", "Three"], ["Something else", "Another", "A third"]],
                       title="Twice")

    said = []
    service = a_library(root, mb=partial, retag_adopted=True)
    service.log = said.append
    service.repair()

    assert discs_of(album)[0] == [1], "nothing assigned"
    plan = load_plan(album)
    assert "needs_a_look" in (plan.adopted or {}), "and the flag stays on"
    named = [line for line in said if "did not settle it" in line]
    assert named and "file(s) matched by their whole title" in named[0], said


def test_with_musicbrainz_off_nothing_is_asked(twice):
    root, album = twice
    taken_in_without_asking(root)
    asked = []

    class Counting(TwoDiscs):
        def search_releases(self, artist, album):
            asked.append(album)
            return super().search_releases(artist, album)

    service = a_library(root, mb=Counting([["One"]], title="Twice"), retag_adopted=True)
    service.cfg = Config(library_root=root, musicbrainz=False, lyrics=False, retag_adopted=True)
    service.repair()

    assert asked == []
    assert discs_of(album)[0] == [1]
