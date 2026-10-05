"""Thumbnails for the grid (DESIGN §9, slice 140; R-502).

Measured on the user's library over NFS, 1,221 covers beside their albums: a card is **10.5rem
wide, about 168 px**, and it was sent the cover as it stands — 65 KiB on average, 220 KiB at worst,
2.67 MB for one view of forty and about **82 MB** for the whole grid. Not a stall (27 ms each) but
bytes nobody can see: the file crosses NFS, crosses the connection, and the browser then scales it
down to a thumb.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image
from test_incremental import opus_template
from test_web import library

from noaap.config import Config
from noaap.cover import THUMB_SIDE, thumbnail
from noaap.download import COVER_STEM
from noaap.web import App


def a_picture(side: int, colour=(120, 30, 180)) -> bytes:
    out = io.BytesIO()
    img = Image.new("RGB", (side, side), colour)
    for x in range(0, side, 7):          # something to compress, so a size change is real
        for y in range(0, side, 11):
            img.putpixel((x, y), ((x * 3) % 256, (y * 5) % 256, 90))
    img.save(out, "JPEG", quality=95)
    return out.getvalue()


def an_app(library, **kw):
    return App(Config(library_root=library, musicbrainz=False, lyrics=False, **kw), library)


def the_album(app):
    album = app.state()["albums"][0]
    return album["id"], app.album(album["id"])[0]


# -- what a thumbnail is -------------------------------------------------------------------------


def test_a_big_cover_becomes_a_small_one():
    big = a_picture(1400)
    got = thumbnail(big)

    assert got is not None
    data, mime = got
    assert mime == "image/jpeg"
    assert max(Image.open(io.BytesIO(data)).size) == THUMB_SIDE
    assert len(data) < len(big) / 4, (len(big), len(data))


def test_a_cover_that_is_already_small_is_left_alone():
    """None rather than a re-encode: a JPEG round trip is never free and buys nothing here."""
    assert thumbnail(a_picture(THUMB_SIDE)) is None
    assert thumbnail(a_picture(120)) is None


def test_the_side_is_about_twice_a_card():
    """A card is 10.5rem — about 168px — so this stays sharp on a two-times screen."""
    assert THUMB_SIDE == 336


def test_something_that_is_not_a_picture_is_not_a_thumbnail():
    assert thumbnail(b"not a picture at all") is None


# -- the cache ------------------------------------------------------------------------------------


def test_the_grid_gets_the_thumbnail_and_the_panel_the_cover(library, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = an_app(library)
    source_id, album_dir = the_album(app)
    (album_dir / f"{COVER_STEM}.jpg").write_bytes(a_picture(1400))
    app.library_changed()

    small = app.cover(source_id, thumb=True)
    whole = app.cover(source_id)

    assert small and whole
    assert len(small[0]) < len(whole[0]) / 4, (len(whole[0]), len(small[0]))
    assert max(Image.open(io.BytesIO(whole[0])).size) == 1400, "the panel still shows it large"


def test_the_cover_crosses_once_and_is_then_read_from_the_cache(library, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = an_app(library)
    source_id, album_dir = the_album(app)
    cover = album_dir / f"{COVER_STEM}.jpg"
    cover.write_bytes(a_picture(1400))
    app.library_changed()
    first = app.cover(source_id, thumb=True)

    reads = []
    real = type(cover).read_bytes
    monkeypatch.setattr(type(cover), "read_bytes",
                        lambda self: (reads.append(self), real(self))[1])

    again = app.cover(source_id, thumb=True)

    assert again[0] == first[0]
    assert cover not in reads, "the cover itself was not opened again"


def test_a_replaced_cover_gets_a_new_thumbnail(library, tmp_path, monkeypatch):
    """Keyed by what the file was when we read it — its size and its mtime — so a replacement is a
    new key and the old entry is simply never asked for again."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = an_app(library)
    source_id, album_dir = the_album(app)
    cover = album_dir / f"{COVER_STEM}.jpg"
    cover.write_bytes(a_picture(1400, (10, 200, 60)))
    app.library_changed()
    was = app.cover(source_id, thumb=True)[0]

    cover.write_bytes(a_picture(1400, (200, 10, 60)))

    now = app.cover(source_id, thumb=True)[0]
    assert now != was, "a different picture, so a different thumbnail"


def test_a_cache_that_cannot_be_written_still_answers(library, tmp_path, monkeypatch):
    """Nothing here may fail a request: without a cache it falls through to the cover itself."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = an_app(library)
    source_id, album_dir = the_album(app)
    (album_dir / f"{COVER_STEM}.jpg").write_bytes(a_picture(1400))
    app.library_changed()

    monkeypatch.setattr("pathlib.Path.mkdir",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))

    got = app.cover(source_id, thumb=True)
    assert got is not None and max(Image.open(io.BytesIO(got[0])).size) == THUMB_SIDE


def test_an_album_with_no_cover_is_still_no_cover(library, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = an_app(library)
    source_id, album_dir = the_album(app)
    for path in album_dir.glob(f"{COVER_STEM}.*"):
        path.unlink()
    app.library_changed()

    assert app.cover(source_id, thumb=True) is None
    assert app.cover(source_id) is None


def test_the_route_asks_for_a_thumbnail_only_when_told(library, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = an_app(library)
    source_id, album_dir = the_album(app)
    (album_dir / f"{COVER_STEM}.jpg").write_bytes(a_picture(1400))
    app.library_changed()

    asked = []
    real = App.cover
    monkeypatch.setattr(App, "cover",
                        lambda self, sid, thumb=False: (asked.append(thumb), real(self, sid, thumb))[1])

    app.cover(source_id, thumb=True)
    app.cover(source_id)
    assert asked == [True, False]


@pytest.mark.parametrize("side", [400, 800, 2000])
def test_every_size_above_the_side_comes_back_at_the_side(side):
    data, _ = thumbnail(a_picture(side))
    assert max(Image.open(io.BytesIO(data)).size) == THUMB_SIDE
