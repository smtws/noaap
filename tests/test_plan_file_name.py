"""The plan file is `.noaap.json` (R-419).

It was `.ytalbum.json` from the beginning: the format's name, kept through the rename so that a
library stayed readable by ytalbum 0.9.x, whose `iter_plans` globs for it. The only installations of
0.9.x left are this project's own, so the reason is gone — and a file named after a program that no
longer exists is a question every reader of this repository has to ask once. Nothing reads the old
name now; `noaap migrate` renames what is on disk, and what it cannot rename it names.
"""

from __future__ import annotations

import json

import pytest
from test_incremental import FakeYouTube, opus_template, vol1

from noaap import migrate
from noaap.config import Config
from noaap.download import (
    OLD_PLAN_FILE,
    PLAN_FILE,
    iter_plans,
    load_plan,
    plans_of_the_old_name,
    run,
    save_plan,
    says_the_old_name,
)
from noaap.plan import build_plan
from noaap.service import Service


def test_the_name_is_the_new_one():
    assert PLAN_FILE == ".noaap.json"
    assert OLD_PLAN_FILE == ".ytalbum.json"


@pytest.fixture
def library(tmp_path, opus_template):
    where = tmp_path / "library"
    plan = build_plan(vol1())
    run(plan, where / plan.folder, FakeYouTube(opus_template))
    return where


def test_a_plan_is_written_and_read_under_the_new_name(library):
    written = sorted(library.rglob(PLAN_FILE))
    assert len(written) == 1, sorted(p.name for p in library.rglob("*.json"))
    assert not list(library.rglob(OLD_PLAN_FILE))
    assert load_plan(written[0].parent) is not None
    assert [album for album, _ in iter_plans(library)] == [written[0].parent]


def test_a_folder_with_the_old_name_is_not_read(library):
    album = sorted(library.rglob(PLAN_FILE))[0].parent
    (album / PLAN_FILE).rename(album / OLD_PLAN_FILE)

    assert load_plan(album) is None, "nothing reads the old name"
    assert list(iter_plans(library)) == []
    assert plans_of_the_old_name(library) == [album]


def test_and_it_is_said_out_loud(library):
    album = sorted(library.rglob(PLAN_FILE))[0].parent
    (album / PLAN_FILE).rename(album / OLD_PLAN_FILE)

    line = says_the_old_name(library)
    assert OLD_PLAN_FILE in line and "noaap migrate --apply" in line
    assert album.name in line


def test_a_library_of_the_new_name_says_nothing(library):
    assert says_the_old_name(library) == ""


def test_repair_says_it_too(library, tmp_path):
    album = sorted(library.rglob(PLAN_FILE))[0].parent
    (album / PLAN_FILE).rename(album / OLD_PLAN_FILE)
    said = []
    service = Service(Config(library_root=library, musicbrainz=False, lyrics=False), library,
                      log=said.append)

    service.repair(dry_run=True)

    assert [line for line in said if "noaap migrate --apply" in line], said


def test_migrate_renames_them_and_counts(library):
    album = sorted(library.rglob(PLAN_FILE))[0].parent
    (album / PLAN_FILE).rename(album / OLD_PLAN_FILE)
    before = (album / OLD_PLAN_FILE).read_bytes()

    dry = migrate.rename_plans([library])
    assert [line for line in dry if line.startswith("would rename")], dry
    assert (album / OLD_PLAN_FILE).exists(), "a dry run renames nothing"

    done = migrate.rename_plans([library], apply=True)
    assert [line for line in done if line.startswith("renamed")], done
    assert "1 plan file(s) renamed" in done[-1]
    assert not (album / OLD_PLAN_FILE).exists()
    assert (album / PLAN_FILE).read_bytes() == before, "the file is renamed, not rewritten"


def test_a_folder_holding_both_is_left_alone_and_named(library):
    album = sorted(library.rglob(PLAN_FILE))[0].parent
    (album / OLD_PLAN_FILE).write_text(json.dumps({"schema": 1}))

    lines = migrate.rename_plans([library], apply=True)

    assert [line for line in lines if line.startswith("left alone:") and str(album) in line], lines
    assert (album / OLD_PLAN_FILE).exists(), "neither file is this pass's to choose"
    assert load_plan(album) is not None, "and the one that is read is untouched"


def test_a_watched_folder_is_swept_too(library, tmp_path):
    drop = tmp_path / "drop" / "Someone" / "An Album"
    drop.mkdir(parents=True)
    (drop / OLD_PLAN_FILE).write_text(json.dumps({"schema": 1}))

    lines = migrate.rename_plans([library, tmp_path / "drop"], apply=True)

    assert (drop / PLAN_FILE).exists(), lines
