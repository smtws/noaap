"""What a dry run over a share is allowed to read (DESIGN §9, slice 103; R-410, ruling 8).

The first dry run over the user's collection took 2 h 52 min for 16,559 tracks. A profile of one
artist said where it went: 87% of it inside `ffmpeg`, one process per file, computing a packet
digest that an adoption never uses. `take_in_staged` turns that off — and then handed `_as_if` a
*fresh* source for every batch, with the flag back at its default. The listing was read three times
over for the same reason: once for the sizes, once for the collision check, once for the batches,
and reading it opens the first file of every album.
"""

from __future__ import annotations

import pytest
from test_collisions import a_service, an_album
from test_intake import QUIET

from noaap import sources, sources_folder, staged


@pytest.fixture
def collection(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    for artist, album in (("Aphelion", "Nocturnes"), ("Aphelion", "Vigil"), ("Bramblewood", "Hollow")):
        an_album(root / artist / album, ["First", "Second"], album=album, artist=artist,
                 one_second_of_sound=one_second_of_sound)
    return root


def test_a_staged_dry_run_digests_nothing(collection, tmp_path, monkeypatch):
    """A packet digest is an ffmpeg remux of the whole file, and an adoption never reads one."""
    calls = []
    monkeypatch.setattr(sources_folder, "stream_sha",
                        lambda path: calls.append(path) or "deadbeef")

    staged.take_in_staged(a_service(collection), collection, QUIET,
                          staging=tmp_path / "staging", dry_run=True, log=lambda s: None)

    assert calls == [], f"{len(calls)} file(s) were digested for a dry run"


def test_the_collection_is_listed_once(collection, tmp_path, monkeypatch):
    """Listing opens the first file of every album: 157 s for the user's 1,311 of them."""
    listings = []
    real = sources_folder.FolderSource.listing

    def counted(self, address):
        listings.append(address)
        return real(self, address)

    monkeypatch.setattr(sources_folder.FolderSource, "listing", counted)
    staged.take_in_staged(a_service(collection), collection, QUIET,
                          staging=tmp_path / "staging", dry_run=True, log=lambda s: None)

    assert listings == [str(collection)], listings


def test_the_dry_run_still_says_the_same_thing(collection, tmp_path):
    """Reading less is only worth anything if the answer does not change."""
    lines = []
    staged.take_in_staged(a_service(collection), collection, QUIET,
                          staging=tmp_path / "staging", dry_run=True, log=lines.append)
    assert any("3 album(s), 6 track(s) would be taken in" in line for line in lines), lines
