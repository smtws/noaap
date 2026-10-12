"""`update --dry-run` names the file work an apply would do (DESIGN §9, slice 182; P130, q5).

`Letzte Instanz/Heilig` had been seated on its pinned release since 2026-10-07 and its files lagged:
every update since then took the cheap skip, and the one update check that did read it said nothing
— `0 new, 0 no longer in the source` — while an apply would have renamed 9 files and retagged 14. The
plan was ahead of the files, and the check only compared the plan with the plan.
"""

from __future__ import annotations

import pytest
from test_intake import QUIET
from test_repointing import an_album

from noaap import intake
from noaap.config import Config
from noaap.download import load_plan, save_plan
from noaap.models import Provenance
from noaap.service import Service


def a_library(root, **settings):
    cfg = Config(library_root=root, musicbrainz=False, lyrics=False, rename_adopted=True,
                 retag_adopted=True, **settings)
    return Service(cfg, root, log=lambda s: None)


def ahead_of_its_files(root, tone):
    """An album whose plan says `Zwei` where the file still says `Two` — a user's edit, which wins
    every merge, so the fresh read of the folder cannot take it back."""
    album = an_album(root / "A Band" / "An Album", ["One", "Two", "Three"], tone)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    service = a_library(root)
    service.repair()                    # into the library's names and tags first
    plan = load_plan(album)
    two = next(t for t in plan.tracks if t.title == "Two")
    two.title, two.provenance["title"] = "Zwei", Provenance.USER
    save_plan(plan, album)
    return album


def files(album):
    return sorted(p.name for p in album.iterdir() if p.is_file() and not p.name.startswith("."))


def test_the_check_names_the_renames_and_retags_and_the_apply_does_exactly_those(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    album = ahead_of_its_files(root, one_second_of_sound)
    before = files(album)

    said: list[str] = []
    service = a_library(root)
    service.log = said.append
    service.update_all(report_only=True, deep=True)

    assert files(album) == before, "a check writes nothing"
    renamed = [line for line in said if "would be renamed:" in line]
    retagged = [line for line in said if "would be retagged:" in line]
    assert len(renamed) == 1 and "Two" in renamed[0] and "Zwei" in renamed[0], said
    assert len(retagged) == 1 and "Zwei" in retagged[0], said
    assert [line for line in said if line.startswith("1 file(s) would be renamed and 1 audio file(s)")], said

    old, new = renamed[0].split(": ", 1)[1].split(" → ")
    checked = [line for line in said if line.startswith("  ")]
    said.clear()
    service.update_all(deep=True)
    # one computation asked twice: every line the check gave, the apply gives before it acts
    assert [line for line in said if line in checked] == checked, (checked, said)

    assert files(album) == sorted([*(f for f in before if f != old), new])
    said.clear()
    service.log = said.append
    service.update_all(report_only=True, deep=True)
    assert not [line for line in said if "would be" in line], "and then there is nothing left to do"


def test_an_album_the_cheap_skip_passes_by_is_not_read_for_it(tmp_path, one_second_of_sound, monkeypatch):
    """The skip is one request to the source; reading every file's tags to answer this would make it
    the cost of a deep read, for every album, on every update. So only `--deep` looks."""
    root = tmp_path / "collection"
    album = ahead_of_its_files(root, one_second_of_sound)
    service = a_library(root)
    monkeypatch.setattr(service, "_unchanged", lambda plan: "3 files, unchanged")

    said: list[str] = []
    service.log = said.append
    service.update_all(report_only=True)

    assert [line for line in said if "unchanged" in line], said
    assert not [line for line in said if "would be renamed" in line], said
    assert files(album) == files(album)


# -- a record the apply writes is a line of the check ------------------------------------------------


def test_the_group_an_apply_writes_is_a_line_of_the_check():
    """Heilig's update check of 2026-10-12 said nothing; its apply wrote the release group."""
    from test_cover_stays_true import a_youtube_plan

    from noaap.plan import changes_from

    was, now = a_youtube_plan(None), a_youtube_plan(None)
    now.release_group = "1f4ebafb"
    now.cover_fallback_url = "https://coverartarchive.org/release-group/1f4ebafb/front-500"
    now.tracks[0].mbid, now.tracks[0].mb_length = "rec", 201.0

    said = changes_from(was, now)

    assert "release group: nothing → 1f4ebafb" in said, said
    assert [line for line in said if line.startswith("cover, if that has none: from")], said
    assert [line for line in said if "a recording id" in line], said
    assert [line for line in said if "length at MusicBrainz nothing → 201.0" in line], said


@pytest.mark.parametrize("files_first", [False, True], ids=["archive only", "its files first (Heilig)"])
def test_the_check_names_the_cover_an_apply_would_save_and_the_files_it_goes_into(
        tmp_path, one_second_of_sound, monkeypatch, files_first):
    """Heilig's shape: a release with no front of its own, a group that has one, no cover beside —
    and, as Heilig's plan had, the album's own folder tried between the two."""
    from test_cover_stays_true import BLUE, GROUP, RELEASE, answering

    from noaap import download
    from noaap.download import caa_group_front, caa_release_front

    root = tmp_path / "collection"
    album = an_album(root / "A Band" / "An Album", ["One", "Two"], one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    a_library(root).repair()
    for picture in album.glob("cover.*"):
        picture.unlink()
    plan = load_plan(album)
    plan.mbid, plan.release_group, plan.cover_url = RELEASE, GROUP, caa_release_front(RELEASE)
    plan.cover_fallback_url = str(album) if files_first else None
    save_plan(plan, album)
    monkeypatch.setattr(download, "caa_answers", answering(caa_group_front(GROUP)))
    monkeypatch.setattr(download, "_archive_cover",
                        lambda url, why=None: BLUE if url == caa_group_front(GROUP) else None)

    said: list[str] = []
    service = a_library(root)
    service.log = said.append
    service.update_all(report_only=True, deep=True)

    assert [line for line in said if line.startswith("  a cover would be saved beside the album")
            and line.endswith(f"from {caa_group_front(GROUP)}")], said
    assert "  2 file(s) without a picture would get the album's once it is saved" in said, said
    assert "0 file(s) would be renamed and 2 audio file(s) would be rewritten (their tags)" in said, said
    assert not (album / "cover.jpg").exists()

    said.clear()
    service.update_all(deep=True)

    assert (album / "cover.jpg").read_bytes() == BLUE
    assert f"a cover was saved beside the album from {caa_group_front(GROUP)}" in said, said
    assert load_plan(album).cover_fetched["tried"] == caa_release_front(RELEASE), "asked, and it had none"
    said.clear()
    service.update_all(report_only=True, deep=True)
    assert not [line for line in said if "cover would be" in line], said
