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
    """Two strengths, kept apart: the hash noaap recorded, and the picture in the album's own files."""
    album_dir = adopt_it(tmp_path)
    plan = load_plan(album_dir)
    picture = hashlib.sha1(JPEG).hexdigest()

    assert cover_proofs(album_dir, plan) == (picture,), "the weaker proof, from the files themselves"

    plan.cover_fetched = {"sha1": "deadbeef"}
    (album_dir / "cover.jpg").write_bytes(JPEG)
    mine = [one for one in added_by_us(album_dir, plan) if one.path.name == "cover.jpg"]
    assert [(one.strong, one.weak) for one in mine] == [(("deadbeef",), (picture,))]


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


# -- and what an undo may delete, as against set aside (R-223) --------------------------------------


def test_what_was_in_the_folder_at_adoption_is_recorded(with_picture, tmp_path):
    """Ruling 1. Every non-audio file that was there, by its path and its hash."""
    (with_picture / "notes.txt").write_text("mine", encoding="utf-8")
    (with_picture / "cd1").mkdir(exist_ok=True)
    (with_picture / "cd1" / "cover.jpg").write_bytes(OTHER)

    album_dir = adopt_it(tmp_path)

    found = load_plan(album_dir).adopted["found"]
    assert set(found) == {"notes.txt", "cd1/cover.jpg"}
    assert found["cd1/cover.jpg"] == hashlib.sha1(OTHER).hexdigest()
    assert all(not name.endswith(".mp3") for name in found), "audio is never ours to remove anyway"


def test_a_file_that_was_there_at_adoption_is_never_removed(with_picture, tmp_path):
    """Ruling 1, and the reason: an undo deletes, so a wrong positive is a file gone for good.

    The hardest case there is — the owner's own cover is **byte for byte the picture inside their own
    files**, which is exactly what the weaker proof looks for. The record of what was there settles it.
    """
    (with_picture / "cover.jpg").write_bytes(JPEG)
    before = files_in(tmp_path)
    album_dir = adopt_it(tmp_path)

    done = give_back(album_dir, load_plan(album_dir), tmp_path)

    assert done["removed"] == 1 and done["binned"] == 0, "the plan, and nothing else"
    assert files_in(tmp_path) == before
    assert (album_dir / "cover.jpg").read_bytes() == JPEG


def test_a_cover_the_weaker_proof_found_goes_to_the_bin(with_picture, tmp_path):
    """Ruling 2. noaap wrote it and lost the record — the shape every 1.4.0/1.5.0 library is in."""
    from noaap.download import save_plan
    from noaap.recycle import entries

    before = files_in(tmp_path)
    album_dir = adopt_it(tmp_path)
    service(tmp_path).update_all(deep=True)
    plan = load_plan(album_dir)
    plan.cover_fetched = {}                 # as those versions left it
    plan.adopted.pop("found", None)         # and with no record of what was there either
    save_plan(plan, album_dir)

    done = give_back(album_dir, load_plan(album_dir), tmp_path)

    assert done["binned"] == 1 and done["kept"] == 0
    assert files_in(tmp_path) == before, "the album is the collection's again"
    binned = entries(tmp_path)
    assert len(binned) == 1 and binned[0].is_file_entry
    assert binned[0].data["where"] == "cover.jpg"
    assert binned[0].data["evidence"]["proof"] == "the album's own embedded picture"
    assert binned[0].data["evidence"]["recorded"] is None


def test_and_the_recorded_hash_deletes_outright(with_picture, tmp_path):
    """The other half of ruling 2: where noaap's own record matches, nothing is set aside."""
    before = files_in(tmp_path)
    album_dir = adopt_it(tmp_path)
    service(tmp_path).update_all(deep=True)   # writes the cover *and* records it now

    done = give_back(album_dir, load_plan(album_dir), tmp_path)

    assert done["removed"] == 2 and done["binned"] == 0, "the plan and the cover"
    assert files_in(tmp_path) == before
    assert not (tmp_path / ".recycle").exists()


def test_a_binned_cover_can_be_put_back(with_picture, tmp_path):
    """Which is the point of the bin, and of not deleting on the weaker proof."""
    from noaap.download import save_plan
    from noaap.recycle import entries

    album_dir = adopt_it(tmp_path)
    service(tmp_path).update_all(deep=True)
    plan = load_plan(album_dir)
    plan.cover_fetched, _ = {}, plan.adopted.pop("found", None)
    save_plan(plan, album_dir)
    give_back(album_dir, load_plan(album_dir), tmp_path)

    out = service(tmp_path).restore(entries(tmp_path)[0].id)

    assert out.status == "ok"
    assert (album_dir / "cover.jpg").read_bytes() == JPEG


def test_the_run_says_how_many_it_could_not_decide(with_picture, tmp_path, capsys):
    """Ruling 3. A library adopted before this version has no record of what was there, so a file with
    neither proof is left — and the count is said out loud rather than buried in a per-file line."""
    from noaap.download import save_plan

    album_dir = adopt_it(tmp_path)
    plan = load_plan(album_dir)
    plan.adopted.pop("found", None)
    save_plan(plan, album_dir)
    (album_dir / "cover.png").write_bytes(OTHER)      # neither proof: it is theirs, or nobody knows

    said = []
    done = give_back(album_dir, load_plan(album_dir), tmp_path, log=said.append)

    assert done["kept"] == 1 and done["binned"] == 0
    assert (album_dir / "cover.png").is_file()
    assert any("nothing here proves noaap wrote it" in line for line in said)
