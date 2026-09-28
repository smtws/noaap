"""A library that survives being moved (DESIGN §9, slice 60).

The fact this package exists for is not that a moved library breaks. **It is that a moved library
works** — by reading the files of the library it was copied from. Measured on a real one: all 2000
refs of a copied collection resolved, every one of them into the original, so the copy played,
measured and merged using somebody else's files until the day the original was deleted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from noaap.cli import _is_conversion, _plan_differences


def test_a_change_carries_both_values():
    """Whether a change is a fault or a conversion can only be told by looking at them."""
    gone, changed = _plan_differences({"a": "/x/y", "b": 1}, {"a": "y", "b": 2})

    assert gone == []
    assert sorted(changed) == [("a", "/x/y", "y"), ("b", 1, 2)]


@pytest.mark.parametrize("before,after,is_it", [
    ("/library/A Band/An Album/01.mp3", "01.mp3", True),          # relative to the album
    ("/library/A Band/An Album", "A Band/An Album", True),        # relative to the library
    ("/library/A Band/An Album/01.mp3", "02.mp3", False),         # a different file
    ("/elsewhere/01.mp3", "01.mp3", False),                       # not under this library at all
    ("/library/A Band/An Album/01.mp3", "/library/A Band/An Album/01.mp3", False),
    ("01.mp3", "01.mp3", False),
    ("/library/A Band/An Album/01.mp3", "", False),
    (7, 8, False),
])
def test_only_the_rewrite_itself_counts_as_a_conversion(before, after, is_it):
    """Nothing else may hide behind it: a value that changed for another reason is a change a
    person has to be told about."""
    album = Path("/library/A Band/An Album")

    assert _is_conversion(before, after, album, Path("/library")) is is_it


def test_verify_tells_a_conversion_from_a_loss(tmp_path, capsys):
    """R-207, ruling 2: a conversion is reported as a change, and it is not a fault."""
    from noaap.cli import _verify_plans
    from noaap.config import Config
    from noaap.download import PLAN_FILE, save_plan
    from noaap.models import AlbumPlan, Kind, PlanTrack

    album_dir = tmp_path / "A Band" / "An Album"
    track = PlanTrack(video_id="aaaaaaaaaaa", number=1, artist="A Band", title="One",
                      filename="01 - One.opus", provenance={}, state="done")
    plan = AlbumPlan(source_url="https://y/1", source_id="p1", kind=Kind.OFFICIAL_ALBUM,
                     album="An Album", albumartist="A Band", year=None, cover_url=None,
                     folder="A Band/An Album", tracks=[track])
    save_plan(plan, album_dir)

    assert _verify_plans(Config(), tmp_path) == 0
    said = capsys.readouterr().out
    assert "1 byte-identical" in said and "would become relative" not in said
