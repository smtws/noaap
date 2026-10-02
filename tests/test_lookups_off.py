"""A dry run promises only the lookups the apply can make (R-427, point 2).

Measured on the user's own collection: with `musicbrainz = false` in the config and no `--no-mb` on
the command line, the dry run printed `would ask MusicBrainz about 10 track(s)` twenty-one times and
an apply with the same config would have asked nothing — `_look_up` needs a client and the setting
decides whether one exists. The flags say what to do tonight; the settings say whether the thing can
be asked at all.
"""

from __future__ import annotations

import pytest
from test_collisions import an_album

from noaap import intake, staged
from noaap.config import Config
from noaap.service import Service


@pytest.fixture
def collection(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    an_album(root / "A Band" / "An Album", ["One", "Two"], album="An Album", artist="A Band",
             one_second_of_sound=one_second_of_sound)
    return root


def a_service(root, **settings):
    return Service(Config(library_root=root, **settings), root, log=lambda s: None)


def test_no_lookup_is_promised_where_the_setting_is_off(collection):
    """The run asks for both; the library has both switched off."""
    said = []
    wants = intake.Choices(musicbrainz=True, lyrics=True)

    done = intake.take_in(a_service(collection, musicbrainz=False, lyrics=False), collection,
                          wants, dry_run=True, log=said.append)

    assert not [line for line in done.would if "would ask" in line], done.would
    assert "  lookups off (config): nothing is asked of MusicBrainz" in said
    assert "  lookups off (config): nothing is asked of LRCLIB" in said


def test_it_is_said_once_and_not_per_album(collection, one_second_of_sound):
    an_album(collection / "A Band" / "Another", ["Three"], album="Another", artist="A Band",
             one_second_of_sound=one_second_of_sound)
    said = []

    intake.take_in(a_service(collection, musicbrainz=False, lyrics=False), collection,
                   intake.Choices(musicbrainz=True, lyrics=True), dry_run=True, log=said.append)

    assert len([line for line in said if "lookups off" in line]) == 2, said


def test_where_the_client_exists_the_promise_stands(collection):
    done = intake.take_in(a_service(collection, musicbrainz=True, lyrics=False), collection,
                          intake.Choices(musicbrainz=True, lyrics=False), dry_run=True,
                          log=lambda s: None)

    assert [line for line in done.would if "would ask MusicBrainz" in line], done.would


def test_a_run_that_asks_for_nothing_says_nothing(collection):
    said = []
    intake.take_in(a_service(collection, musicbrainz=False, lyrics=False), collection,
                   intake.Choices(musicbrainz=False, lyrics=False), dry_run=True, log=said.append)

    assert not [line for line in said if "lookups off" in line], said


def test_a_staged_run_says_it_too(collection, tmp_path):
    said = []
    staged.take_in_staged(a_service(collection, musicbrainz=False, lyrics=False), collection,
                          intake.Choices(musicbrainz=True, lyrics=True),
                          staging=tmp_path / "staging", dry_run=True, log=said.append)

    assert [line for line in said if "lookups off" in line], said
    assert not [line for line in said if "would ask" in line], said
