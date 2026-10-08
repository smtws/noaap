"""A cover the user chooses (DESIGN §9, slice 156; R-519 item 2).

The model for this was already in the plan: `_cover` replaces a picture only while
`cover_fetched`'s sha1 says noaap wrote it, and treats anything else as the owner's. So a cover
handed over here is written and that record is **cleared** — which is what keeps the next pass, the
next MusicBrainz match and the Cover Art Archive off it.

`cover_beside` says whether a copy stays in the folder, `cover_embedded` whether it goes into the
files, and the bytes reach the pass either way — so a library that keeps covers only inside its
files can still be given one.
"""

from __future__ import annotations

import base64
import struct
import zlib
from pathlib import Path

import pytest
from test_intake import QUIET
from test_repointing import a_library, an_album

from noaap import intake
from noaap.config import Config
from noaap.download import COVER_STEM, load_plan
from noaap.models import Provenance
from noaap.service import Service
from noaap.tag import embedded_cover, image_mime
from noaap.web import App

HDR = {"X-Noaap": "1", "Content-Type": "application/json"}
TITLES = ["One", "Two", "Three"]


def a_png(side: int = 8, colour: bytes = b"\xff\x00\x00") -> bytes:
    """A real PNG, because `image_mime` reads the bytes and will not take a pretend one."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    head = struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0)
    rows = b"".join(b"\x00" + colour * side for _ in range(side))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head)
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


@pytest.fixture
def collection(tmp_path, one_second_of_sound):
    root = tmp_path / "library"
    an_album(root / "A Band" / "An Album", TITLES, one_second_of_sound,
             artist="A Band", album="An Album")
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    return root


def a_service(library, **settings) -> Service:
    cfg = Config(library_root=library, musicbrainz=False, lyrics=False, **settings)
    return Service(cfg, library, log=lambda s: None)


def the_album(library) -> tuple[Path, str]:
    album_dir = next(library.glob("*/*/.noaap.json")).parent
    return album_dir, load_plan(album_dir).source_id


# -- what a chosen cover is ----------------------------------------------------------------------


def test_a_chosen_cover_is_written_beside_the_album(collection):
    album_dir, source_id = the_album(collection)
    service = a_service(collection)

    out = service.set_cover(source_id, a_png())

    assert out.status == "ok", out.message
    saved = sorted(album_dir.glob(f"{COVER_STEM}.*"))
    assert [p.name for p in saved] == ["cover.png"]
    assert image_mime(saved[0].read_bytes()) == "image/png"


def test_it_is_the_users_so_nothing_replaces_it(collection):
    """`cover_fetched` is how a plan remembers a cover *noaap* wrote. Cleared, the picture is the
    owner's — which is the rule `_cover` has always followed for one they put there themselves."""
    album_dir, source_id = the_album(collection)
    service = a_service(collection)
    plan = load_plan(album_dir)
    plan.cover_fetched = {"url": "https://example.test/old.jpg", "sha1": "whatever"}
    from noaap.download import save_plan
    save_plan(plan, album_dir)

    service.set_cover(source_id, a_png())

    assert load_plan(album_dir).cover_fetched == {}


def test_an_address_is_remembered_as_the_users_own(collection):
    album_dir, source_id = the_album(collection)
    service = a_service(collection)

    service.set_cover(source_id, a_png(), "https://example.test/art.png")

    after = load_plan(album_dir)
    assert after.cover_url == "https://example.test/art.png"
    assert after.provenance["cover_url"] == Provenance.USER


def test_an_older_cover_file_is_replaced_not_left_beside_it(collection):
    album_dir, source_id = the_album(collection)
    (album_dir / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0 old jpeg bytes")
    service = a_service(collection)

    service.set_cover(source_id, a_png())

    assert [p.name for p in sorted(album_dir.glob(f"{COVER_STEM}.*"))] == ["cover.png"]


def test_something_that_is_not_a_picture_is_refused(collection):
    album_dir, source_id = the_album(collection)
    service = a_service(collection)

    out = service.set_cover(source_id, b"<html>a consent page, saved as a cover</html>")

    assert out.status == "failed"
    assert "not a picture" in out.message
    assert not list(album_dir.glob(f"{COVER_STEM}.*")), "and nothing was written"


def test_an_unknown_album_is_refused(collection):
    out = a_service(collection).set_cover("/nowhere", a_png())
    assert out.status == "failed"
    assert "unknown album" in out.message


# -- what the library's settings decide ----------------------------------------------------------


def test_cover_embedded_puts_it_in_every_file(collection):
    album_dir, source_id = the_album(collection)
    service = a_service(collection, cover_embedded=True)

    service.set_cover(source_id, a_png())

    for path in sorted(album_dir.glob("*.opus")):
        assert embedded_cover(path) == a_png(), f"{path.name} has no picture"


def test_a_library_that_keeps_covers_only_in_the_files_still_gets_one(collection):
    """`cover_beside=False` left nothing to embed: the pass is told not to fetch, finds no file
    beside the album and has nothing to write. The chosen bytes are handed to it directly."""
    album_dir, source_id = the_album(collection)
    service = a_service(collection, cover_beside=False, cover_embedded=True)

    service.set_cover(source_id, a_png())

    assert not list(album_dir.glob(f"{COVER_STEM}.*")), "no copy in the folder, as asked"
    for path in sorted(album_dir.glob("*.opus")):
        assert embedded_cover(path) == a_png(), f"{path.name} has no picture"


def test_cover_embedded_off_leaves_the_files_alone(collection):
    album_dir, source_id = the_album(collection)
    service = a_service(collection, cover_beside=True, cover_embedded=False)

    service.set_cover(source_id, a_png())

    assert (album_dir / "cover.png").exists()
    for path in sorted(album_dir.glob("*.opus")):
        assert embedded_cover(path) is None, f"{path.name} was given a picture"


# -- through the endpoint ------------------------------------------------------------------------


def an_app(library, **settings):
    cfg = Config(library_root=library, musicbrainz=False, lyrics=False, **settings)
    return App(cfg, library, port=0,
               service_factory=lambda job: Service(cfg, library, log=job.log.append))


def test_the_endpoint_takes_a_file_and_writes_it(collection):
    import time

    album_dir, source_id = the_album(collection)
    app = an_app(collection)

    job = app.submit("cover", {"id": source_id, "data": base64.b64encode(a_png()).decode()})
    for _ in range(1500):
        if job.state not in ("queued", "running"):
            break
        time.sleep(0.02)

    assert job.state == "done", job.log
    assert job.lane == "write", "it writes files, so it runs alone"
    assert (album_dir / "cover.png").exists()


def test_an_address_that_is_not_http_is_refused_before_anything_runs(collection):
    _, source_id = the_album(collection)
    app = an_app(collection)

    with pytest.raises(ValueError, match="http"):
        app.submit("cover", {"id": source_id, "url": "file:///etc/passwd"})


def test_a_body_with_neither_a_file_nor_an_address_is_refused(collection):
    _, source_id = the_album(collection)
    with pytest.raises(ValueError, match="no picture"):
        an_app(collection).submit("cover", {"id": source_id})


def test_bytes_that_are_not_base64_are_refused(collection):
    _, source_id = the_album(collection)
    with pytest.raises(ValueError, match="one piece"):
        an_app(collection).submit("cover", {"id": source_id, "data": "not base64 at all!!"})


def test_the_endpoint_needs_the_write_header(collection):
    import threading

    import httpx

    _, source_id = the_album(collection)
    app = an_app(collection)
    srv = app.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}", timeout=10) as c:
            body = {"id": source_id, "data": base64.b64encode(a_png()).decode()}
            assert c.post("/api/cover", json=body).status_code == 403
            assert c.post("/api/cover", json=body, headers=HDR).status_code == 202
    finally:
        srv.shutdown()
