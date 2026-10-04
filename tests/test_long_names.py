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
from noaap.plan import MAX_NAME_BYTES, TITLE_FLOOR, safe_name, track_filename


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


def test_a_stub_in_an_id3v2_frame_is_a_stub_too(tmp_path, one_second_of_mp3):
    """69 files of the user's collection carry the thirty-character value in an ID3v2.4 frame,
    because whatever wrote them copied the v1 value up. The length is the test, not the version."""
    import shutil

    from mutagen.id3 import ID3, TIT2

    album = tmp_path / "collection" / "Depeche Mode" / "Never Let Me Down Again"
    album.mkdir(parents=True)
    path = album / ("Depeche Mode - Never Let Me Down Again - 04 - "
                    "Pleasure, Little Treasure - Glitter Mix.mp3")
    shutil.copy(one_second_of_mp3, path)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=["Pleasure, Little Treasure - Gl"]))
    tags.save(path, v1=0, v2_version=4)

    assert sources_folder.read_tags(path)["title"] == "Pleasure, Little Treasure - Glitter Mix"


@pytest.mark.parametrize(("tag", "named", "whole"), [
    # the four the reviewer found in the 221 recoveries of the first run (R-412)
    ("Runnin' / Brazilian Rhyme (Int", "Runnin' - Brazilian Rhyme (Interlude)",
     "Runnin' / Brazilian Rhyme (Interlude)"),
    ("Die Brut (Columbiahalle/Berlin", "Die Brut (Columbiahalle-Berlin)",
     "Die Brut (Columbiahalle/Berlin)"),
    ("Gewaltberechtigt? (Columbiahal", "Gewaltberechtigt  (Columbiahalle-Berlin)",
     "Gewaltberechtigt? (Columbiahalle-Berlin)"),
    ("Principles Of Lust : A. Sadnes", "Principles Of Lust - A. Sadness  B. Find Love",
     "Principles Of Lust : A. Sadness  B. Find Love"),
    # and the plain case, where the name holds the tag as it is
    ("Zyklus Farbenfinsternis - Kapi", "Zyklus Farbenfinsternis - Kapitel 1- Vorboten",
     "Zyklus Farbenfinsternis - Kapitel 1- Vorboten"),
    # a name that states something else entirely recovers nothing
    ("Something Else Entirely", "A Different Song", None),
])
def test_a_recovery_keeps_the_tag_s_own_characters(tag, named, whole):
    """A file name cannot hold `/`, `?` or `:`, so what it holds there is the scheme's substitute.

    Taking the name whole wrote those substitutes over what the owner typed — 16 of the 221 title
    recoveries over the user's collection. The name is good for one thing: the part beyond where the
    field ran out (R-412).
    """
    from noaap.plan import safe_name
    from noaap.sources_folder import _carried_on

    assert _carried_on(tag, named, safe_name) == whole


def test_a_slash_in_the_tag_is_a_dash_in_the_name(tmp_path, one_second_of_mp3):
    """The name is the tag made safe for a filesystem, so the two are compared that way."""
    import shutil

    from mutagen.id3 import ID3, TIT2

    album = tmp_path / "collection" / "Goethes Erben" / "live"
    album.mkdir(parents=True)
    path = album / "Goethes Erben - live - 02 - Pascal lacht (Karlstorbahnhof-Heidelberg).mp3"
    shutil.copy(one_second_of_mp3, path)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=["Pascal lacht (Karlstorbahnhof/"]))
    tags.save(path, v1=0, v2_version=4)

    assert sources_folder.read_tags(path)["title"] == "Pascal lacht (Karlstorbahnhof/Heidelberg)"


def test_a_short_tag_is_what_its_owner_wrote(tmp_path, one_second_of_mp3):
    """`Winter` is not a cut `Winter of my soul`: below the field's length the tag means what it says."""
    import shutil

    from mutagen.id3 import ID3, TIT2

    album = tmp_path / "collection" / "Eisregen" / "Album"
    album.mkdir(parents=True)
    path = album / "Eisregen - Album - 01 - Winter of my soul [Demo Version 1996].mp3"
    shutil.copy(one_second_of_mp3, path)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=["Winter"]))
    tags.save(path, v1=0, v2_version=4)

    assert sources_folder.read_tags(path)["title"] == "Winter"


def test_a_tag_the_name_does_not_continue_still_stands(id3v1_only, tmp_path):
    other = id3v1_only.with_name("Eisregen - Farbenfinsternis - 06 - Something Else Entirely.mp3")
    id3v1_only.rename(other)
    assert sources_folder.read_tags(other)["title"] == "Zyklus Farbenfinsternis - Kapi"


def test_one_folder_s_cut_tags_are_read_as_one_value(tmp_path, one_second_of_mp3):
    """A field is thirty bytes; the file whose name does not carry the album keeps the stub.

    One folder of the user's collection then stated two albums — `15 Years After - The dusted Va`
    and `15 Years After - The dusted Variations` — and adoption refused it as two albums, which is
    exactly what a folder of two albums should get and exactly wrong here (R-410, rulings 4 and 5).
    """
    import shutil

    from mutagen.id3 import ID3, TALB, TIT2, TPE1

    from noaap import sources
    from noaap.config import Config

    album = tmp_path / "collection" / "Enigma" / "15 Years After - The dusted Variations"
    album.mkdir(parents=True)
    for n, (name, stated) in enumerate([
            ("01 Hello.mp3", "15 Years After - The dusted Va"),          # the name says no more
            ("Enigma - 15 Years After - The dusted Variations - 02 - The Cild In Us.mp3",
             "15 Years After - The dusted Va")], 1):                      # …and this one does
        path = album / name
        shutil.copy(one_second_of_mp3, path)
        tags = ID3()
        tags.add(TIT2(encoding=0, text=[f"Track {n}"]))
        tags.add(TPE1(encoding=0, text=["Enigma"]))
        tags.add(TALB(encoding=0, text=[stated]))
        tags.save(path, v1=2, v2_version=3)
        ID3(path).delete(path, delete_v1=False, delete_v2=True)

    source = sources.get("folder", Config(library_root=tmp_path / "collection"))
    source.digests = False
    collection = source.collection(str(album))

    assert {e.music.album for e in collection.entries} == {"15 Years After - The dusted Variations"}


def test_a_value_cut_a_space_short_is_still_a_cut_value(tmp_path, one_second_of_mp3):
    """The field is padded, so a value cut in a space comes back shorter than thirty once stripped:
    `The Cross Of Changes (Special` is 29, and the folder it is in states the whole name too."""
    import shutil

    from mutagen.id3 import ID3, TALB, TIT2

    from noaap import sources
    from noaap.config import Config

    album = tmp_path / "collection" / "Enigma" / "The Cross Of Changes (Special Edition)"
    album.mkdir(parents=True)
    for n, name in enumerate(["01 track.mp3",
                              "Enigma - The Cross Of Changes (Special Edition) - 02 - Track.mp3"], 1):
        stated = "The Cross Of Changes (Special"     # all ID3v1 can hold, stripped of its padding
        path = album / name
        shutil.copy(one_second_of_mp3, path)
        tags = ID3()
        tags.add(TIT2(encoding=0, text=[f"Track {n}"]))
        tags.add(TALB(encoding=0, text=[stated]))
        tags.save(path, v1=2, v2_version=3)
        ID3(path).delete(path, delete_v1=False, delete_v2=True)

    source = sources.get("folder", Config(library_root=tmp_path / "collection"))
    source.digests = False
    collection = source.collection(str(album))

    assert {e.music.album for e in collection.entries} == {"The Cross Of Changes (Special Edition)"}


def test_a_shorter_name_that_is_not_a_cut_value_is_left_alone(tmp_path, one_second_of_mp3):
    """Two albums in one folder is a thing that happens, and `Greatest Hits` is not `Greatest Hits II`."""
    import shutil

    from mutagen.id3 import ID3, TALB, TIT2

    from noaap import sources
    from noaap.config import Config

    album = tmp_path / "collection" / "Someone" / "mixed"
    album.mkdir(parents=True)
    for n, stated in enumerate(["Greatest Hits", "Greatest Hits II"], 1):
        path = album / f"0{n} track.mp3"
        shutil.copy(one_second_of_mp3, path)
        tags = ID3()
        tags.add(TIT2(encoding=0, text=[f"Track {n}"]))
        tags.add(TALB(encoding=0, text=[stated]))
        tags.save(path, v1=2, v2_version=3)
        ID3(path).delete(path, delete_v1=False, delete_v2=True)

    source = sources.get("folder", Config(library_root=tmp_path / "collection"))
    source.digests = False
    collection = source.collection(str(album))

    assert {e.music.album for e in collection.entries} == {"Greatest Hits", "Greatest Hits II"}


# -- the folder and the names under it say the same thing (§9, slice 116; R-455, item 2) ---------


def test_an_album_whose_name_begins_with_dots_loses_them_in_the_file_names_too():
    """`Crematory/...Just Dreaming` on the user's share: the folder came out `Just Dreaming` and
    every file kept `Crematory - ...Just Dreaming - 01 - …`.

    `safe_name` strips " ." from the ends of what it is given, and it was given the whole assembled
    stem — where the album's dots sit in the middle and survive.
    """
    assert safe_name("...Just Dreaming") == "Just Dreaming"
    assert track_filename("Crematory", "...Just Dreaming", 1, None, "Heaven's Throat", ext="mp3") \
        == "Crematory - Just Dreaming - 01 - Heaven's Throat.mp3"


def test_a_doubled_space_inside_a_name_is_collapsed_the_same_way():
    """`Crematory/Fly  (Single)`: folder `Fly (Single)`, files `Fly  (Single)`."""
    assert safe_name("Fly  (Single)") == "Fly (Single)"
    assert track_filename("Crematory", "Fly  (Single)", 1, None, "Fly", ext="mp3") \
        == "Crematory - Fly (Single) - 01 - Fly.mp3"


def test_a_trailing_space_goes_from_the_album_and_the_artist():
    """`Subway to Sally/Post Mortem ` and `Tanzwut/Herz aus Stein ` are both on the share."""
    assert track_filename("Tanzwut ", "Herz aus Stein ", 2, None, "Nein Nein", ext="mp3") \
        == "Tanzwut - Herz aus Stein - 02 - Nein Nein.mp3"


def test_only_the_ends_of_a_part_are_stripped():
    """A dot inside a name is part of it — `Mr. Hurley`, `A.C.A.B.` — and only the ends go.

    The trailing dot of the *last* part was already lost before this, because the whole stem was
    stripped and the title is what ends it; `Tanzwut/Freitag der 13.` is the album side of the same
    thing, and its folder on the share has been `Freitag der 13` all along.
    """
    assert track_filename("A Band", "An Album", 3, "Mr. Hurley", "A.C.A.B.", ext="mp3") \
        == "A Band - An Album - 03 - Mr. Hurley - A.C.A.B.mp3"
    assert track_filename("A Band", "Freitag der 13.", 3, None, "Schrei", ext="mp3") \
        == "A Band - Freitag der 13 - 03 - Schrei.mp3", "and now the album's too"


def test_the_part_that_is_only_dots_does_not_vanish_into_nothing():
    name = track_filename("A Band", "...", 4, None, "One", ext="mp3")
    assert name == "A Band - _ - 04 - One.mp3", "safe_name's own answer for an empty name"
