"""What gets shorter when a name is too long, and a tag that was cut by its format
(DESIGN §9, slice 103; R-410, ruling 4).

Two different ways a file ended up named after a stub. One is this program's: `safe_name` cut
whatever was at the end of the name, which is always the title — the one part that says which song
it is. The other is the owner's format: an ID3v1 field holds thirty bytes, so 138 files of the
user's collection state `Zyklus Farbenfinsternis - Kapi` while the name beside them spells the song
out in full, and renaming to the tag threw the rest away.
"""

from __future__ import annotations

import shutil

import pytest
from mutagen.id3 import ID3, TALB, TIT2, TPE1

from noaap import sources_folder
from noaap.plan import MAX_NAME_BYTES, TITLE_FLOOR, track_filename


def test_a_name_that_fits_is_left_alone():
    assert track_filename("Eisregen", "Farbenfinsternis", 6, None,
                          "Zyklus Farbenfinsternis - Kapitel 1- Vorboten") == (
        "Eisregen - Farbenfinsternis - 06 - Zyklus Farbenfinsternis - Kapitel 1- Vorboten.opus")


def test_the_album_artist_goes_first_because_the_folder_says_it():
    name = track_filename("A" * 120, "B" * 100, 6, None, "The Song That Says Which Song This Is")
    assert name.startswith("B" * 100), name
    assert "The Song That Says Which Song This Is.opus" in name
    assert len(name.encode()) <= MAX_NAME_BYTES


def test_then_the_album_goes_and_the_title_survives_whole():
    name = track_filename("A" * 120, "B" * 200, 6, None, "The Song That Says Which Song This Is")
    assert name == "06 - The Song That Says Which Song This Is.opus"


def test_only_then_is_the_title_cut_and_it_says_so():
    name = track_filename("A" * 120, "B" * 200, 6, None, "T" * 300)
    assert len(name.encode()) <= MAX_NAME_BYTES
    assert name.endswith("….opus"), name
    assert len(name.split(" - ", 1)[1]) > TITLE_FLOOR


def test_a_title_is_never_cut_to_a_stub_while_the_artist_is_kept():
    """The old rule kept `AlbumArtist - Album - NN - ` whole and cut the title to nothing."""
    name = track_filename("A" * 110, "B" * 110, 6, None, "Kapitel 1- Vorboten")
    assert "Kapitel 1- Vorboten" in name, name


@pytest.fixture
def id3v1_only(tmp_path, one_second_of_mp3):
    """An mp3 tagged the way 2003 tagged them: thirty characters, and the name holds the rest."""
    album = tmp_path / "collection" / "Eisregen" / "Farbenfinsternis"
    album.mkdir(parents=True)
    path = album / "Eisregen - Farbenfinsternis - 06 - Zyklus Farbenfinsternis - Kapitel 1- Vorboten.mp3"
    shutil.copy(one_second_of_mp3, path)
    tags = ID3()
    tags.add(TIT2(encoding=0, text=["Zyklus Farbenfinsternis - Kapi"]))
    tags.add(TPE1(encoding=0, text=["Eisregen"]))
    tags.add(TALB(encoding=0, text=["Farbenfinsternis"]))
    tags.save(path, v1=2, v2_version=3)
    # and then the ID3v2 block goes, which is what those files look like
    ID3(path).delete(path, delete_v1=False, delete_v2=True)
    return path


def test_the_name_is_read_where_the_tag_was_cut_by_its_format(id3v1_only):
    read = sources_folder.read_tags(id3v1_only)
    assert read["title"] == "Zyklus Farbenfinsternis - Kapitel 1- Vorboten"
    assert read["artist"] == "Eisregen"


def test_a_tag_the_name_does_not_continue_still_stands(id3v1_only, tmp_path):
    other = id3v1_only.with_name("Eisregen - Farbenfinsternis - 06 - Something Else Entirely.mp3")
    id3v1_only.rename(other)
    assert sources_folder.read_tags(other)["title"] == "Zyklus Farbenfinsternis - Kapi"
