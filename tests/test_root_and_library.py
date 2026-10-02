"""A pass that renames is refused below its library (R-417, point 2).

Measured on a copy before it was closed: with the library at the collection and the pass pointed at
`…/Musik/Crematory`, the **staged** dry run said no folder would move and the apply made
`…/Musik/Crematory/Crematory/Act Seven` — the artist filed inside itself — because a staged run
hands its inner pass the copy it made, so the base for `<album artist>/<album>` is the take-in root.
The **plain** pass has the same flaw the other way round: its dry run promised
`the album folder would move to Crematory/Act Seven` and the apply moved nothing, because it
relocates only where the root *is* the library.

Neither base is right for an artist folder, so neither is chosen: the pass says so and stops, and
`--only` is how one part of a collection is taken in.
"""

from __future__ import annotations

import pytest
from test_collisions import a_service, an_album
from test_intake import QUIET

from noaap import intake, staged


@pytest.fixture
def collection(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    an_album(root / "Crematory" / "Act Seven", ["Tears of Time"], album="Act Seven",
             artist="Crematory", one_second_of_sound=one_second_of_sound)
    return root


def test_a_staged_run_below_the_library_is_refused(collection, tmp_path):
    said = []
    done = staged.take_in_staged(a_service(collection), collection / "Crematory", QUIET,
                                 staging=tmp_path / "staging", dry_run=True, log=said.append)

    assert done.stopped, said
    assert "renames into noaap's scheme" in done.stopped
    assert str(collection / "Crematory") in done.stopped and str(collection) in done.stopped
    assert "--only Crematory" in done.stopped and "--names keep" in done.stopped
    assert done.batches == 0, "it is refused before it plans"


def test_the_plain_pass_is_refused_the_same_way(collection):
    done = intake.take_in(a_service(collection), collection / "Crematory", QUIET, dry_run=True,
                          log=lambda s: None)
    assert done.stopped and "--names keep" in done.stopped
    assert done.adopted == 0


def test_keeping_the_names_is_the_way_out(collection, tmp_path):
    keep = intake.Choices(names="keep", musicbrainz=False, lyrics=False)
    done = staged.take_in_staged(a_service(collection), collection / "Crematory", keep,
                                 staging=tmp_path / "staging", dry_run=True, log=lambda s: None)
    assert not done.stopped
    assert done.albums == 1


def test_the_whole_collection_still_runs(collection, tmp_path):
    done = staged.take_in_staged(a_service(collection), collection, QUIET,
                                 staging=tmp_path / "staging", dry_run=True, log=lambda s: None)
    assert not done.stopped
    assert done.albums == 1


def test_and_so_does_one_part_of_it(collection, tmp_path):
    done = staged.take_in_staged(a_service(collection), collection, QUIET,
                                 staging=tmp_path / "staging", dry_run=True,
                                 only=["Crematory"], log=lambda s: None)
    assert not done.stopped
    assert done.albums == 1
