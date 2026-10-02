"""One part of a collection, with the whole collection's names (R-417, point 1).

The first apply over somebody's twenty years is one artist, and until now there was no way to say
so. Pointing the pass at the artist folder is the wrong answer — measured, and refused since (see
`test_root_and_library.py`) — because the scheme files an album under `<album artist>/<album>` and
the artist folder is already that. `--only` chooses which albums are in the pass and changes nothing
else: the root and the library stay the collection, so every name, every batch and every line is
what the whole pass would produce.
"""

from __future__ import annotations

import pytest
from test_collisions import a_service, an_album
from test_intake import QUIET

from noaap import intake, staged


@pytest.fixture
def two_artists(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    an_album(root / "Crematory" / "Act Seven", ["Tears of Time", "Shadowmaker"],
             album="Act Seven", artist="Crematory", one_second_of_sound=one_second_of_sound)
    an_album(root / "Crematory" / "Awake", ["Awake"], album="Awake", artist="Crematory",
             one_second_of_sound=one_second_of_sound)
    an_album(root / "Eisregen" / "Farbenfinsternis", ["Dein Blut"], album="Farbenfinsternis",
             artist="Eisregen", one_second_of_sound=one_second_of_sound)
    return root


def test_only_one_artist_is_planned(two_artists):
    said = []
    done = intake.take_in(a_service(two_artists), two_artists, QUIET, dry_run=True,
                          only=["Crematory"], log=said.append)

    assert "only: Crematory (2 albums)" in said
    assert done.adopted == 2
    assert not [line for line in done.would if "Eisregen" in line], done.would


def test_only_one_album_is_planned(two_artists):
    done = intake.take_in(a_service(two_artists), two_artists, QUIET, dry_run=True,
                          only=["Crematory/Act Seven"], log=lambda s: None)
    assert done.adopted == 1
    assert done.tracks == 2


def test_the_name_is_matched_as_it_reads(two_artists):
    """`adopt --only` matches an artist by name through `text_key`; this matches the same way."""
    done = intake.take_in(a_service(two_artists), two_artists, QUIET, dry_run=True,
                          only=["crematory"], log=lambda s: None)
    assert done.adopted == 2


def test_a_path_that_chooses_nothing_is_refused_by_name(two_artists):
    said = []
    done = intake.take_in(a_service(two_artists), two_artists, QUIET, dry_run=True,
                          only=["Nobody"], log=said.append)

    assert done.stopped == "--only Nobody: no album under that path"
    assert done.stopped in said
    assert done.adopted == 0, "nothing is planned when the selection is wrong"


def test_the_names_are_the_whole_collection_s(two_artists):
    """The point of the flag: the scheme files Act Seven under Crematory, not under itself."""
    done = intake.take_in(a_service(two_artists), two_artists, QUIET, dry_run=True,
                          only=["Crematory"], log=lambda s: None)
    moves = [line for line in done.would if "the album folder would move" in line]
    assert moves == [], "the albums are already where the scheme wants them"
    names = [line for line in done.would if "would be renamed" in line]
    assert all("Crematory - " in line for line in names), names


def test_what_is_not_taken_in_is_limited_to_the_selection(two_artists, one_second_of_sound):
    from test_leftovers import audio
    audio(two_artists / "Crematory" / ".thumb", "01 x.opus", title="x", album="x",
          artist="Crematory", one_second_of_sound=one_second_of_sound)
    audio(two_artists / "Eisregen" / ".thumb", "01 y.opus", title="y", album="y",
          artist="Eisregen", one_second_of_sound=one_second_of_sound)

    done = intake.take_in(a_service(two_artists), two_artists, QUIET, dry_run=True,
                          only=["Crematory"], log=lambda s: None)

    section = [line for line in done.would if line.strip().startswith(("Crematory/", "Eisregen/"))]
    assert any("Crematory/.thumb" in line for line in section), done.would
    assert not [line for line in section if "Eisregen" in line], "the other artist is not this run's"


def test_a_staged_run_chooses_the_same_albums(two_artists, tmp_path):
    said = []
    done = staged.take_in_staged(a_service(two_artists), two_artists, QUIET,
                                 staging=tmp_path / "staging", dry_run=True,
                                 only=["Crematory"], log=said.append)
    assert "only: Crematory (2 albums)" in said
    assert done.albums == 2 and done.adopted == 2
    assert not [line for line in said if "Eisregen" in line], said


def test_a_staged_run_refuses_a_selection_that_chooses_nothing(two_artists, tmp_path):
    done = staged.take_in_staged(a_service(two_artists), two_artists, QUIET,
                                 staging=tmp_path / "staging", dry_run=True,
                                 only=["Nobody"], log=lambda s: None)
    assert done.stopped == "--only Nobody: no album under that path"
    assert done.batches == 0
