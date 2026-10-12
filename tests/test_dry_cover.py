"""A dry run never writes a cover (DESIGN §9, slice 179; P127).

`repair --dry-run` on the user's share replaced `Sonata Arctica/For the Sake of Revenge/cover.jpg` on
2026-10-11. It asked `_cover(fetch=False)` for the bytes on disk, to compare the tracks with them,
and that branch fetched the plan's newer Cover Art Archive address and wrote it, because the cover
there was one this program had saved. The plan was not saved, since a check saves nothing, so it
went on recording the picture that had just been removed. The apply that followed took the new file
for the owner's and embedded it in 15 tracks.
"""

from __future__ import annotations

import hashlib
import io

from PIL import Image
from test_intake import QUIET
from test_repointing import a_library, an_album

from noaap import download, intake
from noaap.download import _cover, cover_would, load_plan, save_plan

OLD = "https://coverartarchive.org/release-group/old/front-500"
NEW = "https://coverartarchive.org/release/new/front-500"


def picture(colour, size=(80, 80), fmt="JPEG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, fmt)
    return out.getvalue()


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def everything(root):
    return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(root.rglob("*")) if p.is_file()}


def ours_with_a_newer_address(root, tone):
    """The Sonata state: a cover of ours, and a plan whose address is one it has not tried."""
    album = an_album(root / "A Band" / "An Album", ["One", "Two"], tone)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    old = picture("red")
    (album / "cover.jpg").write_bytes(old)
    plan = load_plan(album)
    plan.cover_fetched = {"url": OLD, "sha1": sha1(old)}
    plan.cover_url = NEW
    save_plan(plan, album)
    return album, old


def test_neither_dry_run_touches_a_file_and_the_apply_writes_once(tmp_path, one_second_of_sound,
                                                                 monkeypatch):
    root = tmp_path / "collection"
    album, old = ours_with_a_newer_address(root, one_second_of_sound)
    new = picture("blue")
    asked = []
    monkeypatch.setattr(download, "_download_cover",
                        lambda url, source, why=None: asked.append(url) or (url, new))
    said = []
    service = a_library(root)
    service.log = said.append
    before = everything(root)

    service.repair(dry_run=True)
    assert everything(root) == before, "a check changed a file"
    assert f"  the cover would be fetched again from {NEW}, and replaced only if the picture differs from the one beside the album" in said, said

    service.update_all(report_only=True, deep=True)
    assert everything(root) == before, "an update check changed a file"
    assert asked == [], "a dry run asked for a cover"

    said.clear()
    service.repair()

    assert (album / "cover.jpg").read_bytes() == new
    assert load_plan(album).cover_fetched == {"url": NEW, "sha1": sha1(new)}, "the record is the file"
    assert asked == [NEW], "fetched once"


def test_a_look_neither_renames_nor_squares(tmp_path):
    """The other two writes on the same path: a name the bytes disagree with, a padded cover of ours."""
    from noaap.models import AlbumPlan, Kind
    album = tmp_path / "A Band" / "An Album"
    album.mkdir(parents=True)
    framed = Image.new("RGB", (120, 80), "black")           # a square picture between two bars
    framed.paste(Image.new("RGB", (80, 80), "green"), (20, 0))
    out = io.BytesIO()
    framed.save(out, "PNG")
    png = out.getvalue()
    (album / "cover.jpg").write_bytes(png)
    plan = AlbumPlan(source_url=str(album), source_id=str(album), kind=Kind.OFFICIAL_ALBUM,
                     album="An Album", albumartist="A Band", year=None, cover_url=NEW,
                     folder="A Band/An Album", tracks=[])
    plan.cover_fetched = {"url": OLD, "sha1": sha1(png)}
    before = everything(tmp_path)

    assert _cover(plan, album, source=None, fetch=False) == png
    assert everything(tmp_path) == before
    lines = cover_would(plan, album)
    assert "cover.jpg would be renamed cover.png, which is what it holds" in lines, lines
    assert "the cover would be squared" in lines
    assert f"the cover would be fetched again from {NEW}, and replaced only if the picture differs from the one beside the album" in lines


def test_somebodys_own_cover_is_never_offered_for_replacing(tmp_path):
    from noaap.models import AlbumPlan, Kind
    album = tmp_path / "A Band" / "An Album"
    album.mkdir(parents=True)
    (album / "cover.jpg").write_bytes(picture("red"))
    plan = AlbumPlan(source_url=str(album), source_id=str(album), kind=Kind.OFFICIAL_ALBUM,
                     album="An Album", albumartist="A Band", year=None, cover_url=NEW,
                     folder="A Band/An Album", tracks=[])
    assert cover_would(plan, album) == []
