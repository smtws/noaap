"""A cover noaap wrote, and how an undo can tell (DESIGN §9, slice 64).

`adopt --undo` removes what noaap added and keeps what the owner made, and it decides by fingerprint:
*the bytes we wrote are ours, anything else is theirs.* For a cover file that failed, and the failure
was silent — the undo said "kept cover.jpg: it is not the file noaap wrote" about **sixteen covers
noaap had written** into a real 41 GB library.

Two things were wrong. `run` saves the plan **before** it fetches the cover and only again when a track
changes something, which for an adopted album is never — so the record of the cover died with the
process while the file stayed. And a cover for a folder album *is* the picture inside one of its own
files, which is a proof in itself and was not being used.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from mutagen.id3 import APIC, ID3
from test_folder_source import JPEG, encode

from noaap import sources
from noaap.adopt import added_by_us, carry_out, cover_proofs, give_back, ours_still, survey
from noaap.config import Config
from noaap.download import PLAN_FILE, load_plan
from noaap.service import Service

OTHER = (b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF" + b"\x00" * 24)  # a different jpeg, byte for byte


def folder():
    return sources.get("folder", Config())


@pytest.fixture
def with_picture(tmp_path) -> Path:
    """An album that carries its cover **inside its files** and has no cover file of its own.

    16 of the reference collection's 135 folders are this, and they are the ones the undo got wrong.
    """
    album = tmp_path / "A Band" / "An Album"
    for n, title in enumerate(["One", "Two"], 1):
        path = encode(album / f"A Band - An Album - {n:02d} - {title}.mp3", hz=300 + 40 * n,
                      title=title, artist="A Band", album="An Album", album_artist="A Band",
                      track=str(n))
        tags = ID3(path)
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=JPEG))
        tags.save(path)
    return album


def adopt_it(library: Path) -> Path:
    found = survey(library, library, folder())
    carry_out(found)
    return found.taking[0].album_dir


def service(library: Path) -> Service:
    cfg = Config()
    cfg.library_root = library
    return Service(cfg, library)


def files_in(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if p.is_file() and ".recycle" not in p.parts)


# -- the record that was lost -----------------------------------------------------------------------


def test_the_record_of_a_cover_is_written_when_the_cover_is(with_picture, tmp_path):
    """The cause. Before this, `cover_fetched` was `{}` in the saved plan and the file was on disk."""
    album_dir = adopt_it(tmp_path)

    service(tmp_path).update_all(deep=True)

    cover = album_dir / "cover.jpg"
    assert cover.is_file(), "the picture inside the files became the album's cover"
    recorded = json.loads((album_dir / PLAN_FILE).read_text())["cover_fetched"]
    assert recorded.get("sha1") == hashlib.sha1(cover.read_bytes()).hexdigest()


def test_and_the_undo_takes_it_back(with_picture, tmp_path):
    before = files_in(tmp_path)
    album_dir = adopt_it(tmp_path)
    service(tmp_path).update_all(deep=True)

    done = give_back(album_dir, load_plan(album_dir), tmp_path)

    assert done["failed"] == 0 and done["kept"] == 0
    assert files_in(tmp_path) == before, "the album is the collection's again, cover included"


# -- and the proof that works without a record ------------------------------------------------------


def test_a_cover_with_no_record_is_still_provably_ours(with_picture, tmp_path):
    """Every library written by 1.4.0 or 1.5.0 is this: the file is there, the record is not."""
    before = files_in(tmp_path)
    album_dir = adopt_it(tmp_path)
    service(tmp_path).update_all(deep=True)
    plan = load_plan(album_dir)
    plan.cover_fetched = {}                      # as those versions left it
    from noaap.download import save_plan
    save_plan(plan, album_dir)

    done = give_back(album_dir, load_plan(album_dir), tmp_path)

    assert done["kept"] == 0, "the picture in its own files proves it"
    assert files_in(tmp_path) == before


def test_a_cover_the_owner_made_stays(with_picture, tmp_path):
    """Neither proof, so it is theirs — and it stays whatever else the undo does."""
    album_dir = adopt_it(tmp_path)
    (album_dir / "cover.png").write_bytes(OTHER)

    done = give_back(album_dir, load_blan := load_plan(album_dir), tmp_path)

    assert done["kept"] == 1
    assert (album_dir / "cover.png").read_bytes() == OTHER
    assert load_blan is not None


def test_what_counts_as_a_proof(with_picture, tmp_path):
    album_dir = adopt_it(tmp_path)
    plan = load_plan(album_dir)
    picture = hashlib.sha1(JPEG).hexdigest()

    assert cover_proofs(album_dir, plan) == (picture,), "no record yet, and one picture in the files"
    plan.cover_fetched = {"sha1": "deadbeef"}
    assert cover_proofs(album_dir, plan) == ("deadbeef", picture)

    (album_dir / "cover.jpg").write_bytes(JPEG)
    assert [proofs for path, proofs in added_by_us(album_dir, plan)
            if path.name == "cover.jpg"] == [("deadbeef", picture)]


@pytest.mark.parametrize("proofs,is_ours", [
    (("deadbeef",), False),
    ((), False),
    (None, False),
    ((hashlib.sha1(JPEG).hexdigest(),), True),
    (hashlib.sha1(JPEG).hexdigest(), True),                      # one, as a plain string
    (("deadbeef", hashlib.sha1(JPEG).hexdigest()), True),        # any of them is enough
    ((hashlib.sha1(JPEG).hexdigest()[:16],), True),              # a 16-character lyrics_sha
])
def test_any_one_fingerprint_proves_it(tmp_path, proofs, is_ours):
    path = tmp_path / "cover.jpg"
    path.write_bytes(JPEG)

    assert ours_still(path, proofs) is is_ours
