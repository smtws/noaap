"""A picture is what its bytes say (DESIGN §9, slice 127; R-474 item 2, R-475).

The repair check named three albums of the user's as wanting a cover — `Cyndi Lauper/To Memphis
With Love Live`, `Marilyn Manson/Dead To The World Tour`, `Wolfsheim/Dreaming Apes` — each already
holding a `cover.jpg`. The ruling supposed the address was being read as unfetched. It was not: the
first is a **BMP** and the other two are **GIFs**, and `image_mime` knew jpeg, png and webp only. So
noaap saw no cover there at all, said one was missing on every pass, and would have written another
beside it. The line was true; the reading of a picture was what was wrong.
"""

from __future__ import annotations

import shutil

import pytest
from mutagen import File as MFile
from mutagen.id3 import ID3
from test_intake import QUIET

from noaap import intake
from noaap.config import Config
from noaap.download import _cover, _named_for_what_it_is
from noaap.service import Service
from noaap.tag import EMBEDDABLE, as_jpeg, image_mime, tag_file

GIF = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00," \
      b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb004300" + "01" * 64 + "ffd9")


def a_bmp() -> bytes:
    from io import BytesIO

    from PIL import Image

    out = BytesIO()
    Image.new("RGB", (4, 4), (120, 30, 30)).save(out, format="BMP")
    return out.getvalue()


# -- what counts as a picture --------------------------------------------------------------------


def test_gif_and_bmp_are_pictures():
    assert image_mime(GIF) == "image/gif"
    assert image_mime(a_bmp()) == "image/bmp"
    assert image_mime(b"<html>consent page</html>") is None


def test_a_tag_gets_a_jpeg_and_a_png_stays_a_png():
    """`APIC`, the Vorbis picture block and `covr` are read by players that know jpeg and png."""
    assert as_jpeg(JPEG) is JPEG, "already embeddable: untouched"
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    assert as_jpeg(png) is png, "a png is embeddable too, and converting would cost quality"
    made = as_jpeg(GIF)
    assert made is not None and image_mime(made) == "image/jpeg"
    assert image_mime(as_jpeg(a_bmp())) == "image/jpeg"
    assert as_jpeg(b"<html>consent page</html>") is None


def test_a_gif_cover_is_embedded_as_a_jpeg(tmp_path, one_second_of_mp3, one_second_of_sound):
    """Both writers that carry a picture, so neither puts a gif where no player will show it."""
    root = tmp_path / "collection"
    album = root / "A Band" / "An Album"
    album.mkdir(parents=True)
    mp3 = album / "01 One.mp3"
    shutil.copy(one_second_of_mp3, mp3)
    opus = album / "02 Two.opus"
    shutil.copy(one_second_of_sound, opus)
    for p, title in ((mp3, "One"), (opus, "Two")):
        audio = MFile(p, easy=True) if p.suffix == ".opus" else None
        if audio is not None:
            audio["title"], audio["artist"] = [title], ["A Band"]
            audio["albumartist"], audio["album"] = ["A Band"], ["An Album"]
            audio["tracknumber"] = ["2"]
            audio.save()
    from noaap import sources
    source = sources.get("folder", Config(library_root=root))
    source.digests = False
    plan = intake._adopted(album, root, source, log=lambda s: None)

    for track in plan.tracks:
        tag_file(album / track.filename, plan, track, cover=GIF, keep_unknown=True)

    inside = ID3(mp3, load_v1=False).getall("APIC")
    assert inside and inside[0].mime in EMBEDDABLE, inside and inside[0].mime


# -- and the file beside the album is called what it is -----------------------------------------


def test_a_cover_jpg_that_is_a_gif_is_renamed(tmp_path):
    album = tmp_path / "An Album"
    album.mkdir()
    wrong = album / "cover.jpg"
    wrong.write_bytes(GIF)

    now = _named_for_what_it_is(wrong, GIF)

    assert now.name == "cover.gif"
    assert now.read_bytes() == GIF, "the picture is theirs and is kept exactly"
    assert not wrong.exists()


def test_a_name_that_is_already_right_is_left_alone(tmp_path):
    album = tmp_path / "An Album"
    album.mkdir()
    right = album / "cover.jpg"
    right.write_bytes(JPEG)

    assert _named_for_what_it_is(right, JPEG) == right


def test_a_target_that_is_taken_is_never_overwritten(tmp_path):
    album = tmp_path / "An Album"
    album.mkdir()
    wrong = album / "cover.jpg"
    wrong.write_bytes(GIF)
    taken = album / "cover.gif"
    taken.write_bytes(b"GIF89a somebody else's")

    assert _named_for_what_it_is(wrong, GIF) == wrong, "theirs, and not this function's to replace"
    assert taken.read_bytes() == b"GIF89a somebody else's"


def test_the_album_now_has_a_cover_and_nothing_is_reported(tmp_path, one_second_of_sound):
    """The three albums, end to end: the cover is found, so no second one is written."""
    from noaap import sources

    root = tmp_path / "collection"
    album = root / "A Band" / "An Album"
    album.mkdir(parents=True)
    path = album / "01 One.opus"
    shutil.copy(one_second_of_sound, path)
    audio = MFile(path)
    audio["title"], audio["artist"] = ["One"], ["A Band"]
    audio["albumartist"], audio["album"] = ["A Band"], ["An Album"]
    audio["tracknumber"] = ["1"]
    audio.save()
    (album / "cover.jpg").write_bytes(GIF)
    source = sources.get("folder", Config(library_root=root))
    source.digests = False
    plan = intake._adopted(album, root, source, log=lambda s: None)

    got = _cover(plan, album, source, fetch=False)

    assert got == GIF, "the cover is there and is used"
    assert (album / "cover.gif").is_file() and not (album / "cover.jpg").exists()


def test_a_cover_file_that_is_no_picture_is_said_out_loud(tmp_path, one_second_of_sound, caplog):
    from noaap import sources

    root = tmp_path / "collection"
    album = root / "A Band" / "An Album"
    album.mkdir(parents=True)
    path = album / "01 One.opus"
    shutil.copy(one_second_of_sound, path)
    audio = MFile(path)
    audio["title"], audio["artist"] = ["One"], ["A Band"]
    audio["albumartist"], audio["album"] = ["A Band"], ["An Album"]
    audio["tracknumber"] = ["1"]
    audio.save()
    (album / "cover.jpg").write_bytes(b"<html>consent page</html>")
    source = sources.get("folder", Config(library_root=root))
    source.digests = False
    plan = intake._adopted(album, root, source, log=lambda s: None)

    with caplog.at_level("WARNING"):
        got = _cover(plan, album, source, fetch=False)

    assert got is None
    assert [r for r in caplog.records if "not a picture this program can read" in r.getMessage()]
    assert (album / "cover.jpg").read_bytes() == b"<html>consent page</html>", "left as it is"
