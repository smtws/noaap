"""Every pass brings the albums it touches to the settings (DESIGN §9, slice 100).

The user: *"if i do an intake with deliberately few options checked to speed things up and decide
that i now want to have the image embedded everywhere i am not going to hop through 700 albums
manually."* So the settings are the state, a pass closes the difference, and the only thing kept per
album is an exception the user sets by hand.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from mutagen import File as MFile

from noaap.config import Config
from noaap.download import load_plan, save_plan, would_do
from noaap.models import AlbumPlan, Kind, PlanTrack
from noaap.service import Service
from noaap.tag import embedded_cover, tag_file
from noaap.treatment import Treatment, for_album, held_back

COVER = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb004300"
                      + "01" * 64 + "ffd9")


@pytest.fixture
def adopted(tmp_path, one_second_of_sound):
    """An album taken in quickly: its owner's names and tags, nothing embedded, no cover file."""
    library = tmp_path / "library"
    album_dir = library / "Aphelion" / "Nocturnes"
    album_dir.mkdir(parents=True)
    tracks = []
    for n, title in enumerate(["First", "Second"], 1):
        name = f"{n:02d} {title}.opus"
        shutil.copy(one_second_of_sound, album_dir / name)
        audio = MFile(album_dir / name)
        audio["title"] = [title]
        audio["artist"] = ["Aphelion"]
        audio["comment"] = ["theirs"]
        audio.save()
        tracks.append(PlanTrack(video_id=f"t{n}", number=n, artist="Aphelion", title=title,
                                filename=name, state="done", provenance={}))
    plan = AlbumPlan(source_url=str(album_dir), source_id="nocturnes", kind=Kind.OFFICIAL_ALBUM,
                     album="Nocturnes", albumartist="Aphelion", year=None, cover_url=None,
                     folder="Aphelion/Nocturnes", tracks=tracks, provider="folder")
    plan.keep_names = plan.keep_tags = True          # what adoption records: it was taken in as it stood
    plan.adopted = {"folder": "Aphelion/Nocturnes", "at": "2026-10-01", "found": {}}
    save_plan(plan, album_dir)
    (album_dir / "cover.jpg").write_bytes(COVER)     # a cover beside it, nothing embedded
    return library, album_dir, plan


def test_a_quick_take_in_is_not_a_promise_about_the_album(adopted):
    """What it was taken in with does not follow it around: the settings decide what it should have."""
    library, album_dir, plan = adopted
    cfg = Config(library_root=library)

    want = for_album(cfg, plan)
    assert want.cover_embedded is True and want.lyrics_embedded is True
    assert held_back(cfg, plan) == [], "and nothing about the album holds the settings back"

    # …while the album's own record still says, correctly, what was done to it
    assert plan.keep_names and plan.keep_tags and plan.adopted


def test_turning_embedding_on_reaches_an_adopted_album_without_visiting_it(adopted):
    library, album_dir, plan = adopted
    cfg = Config(library_root=library, musicbrainz=False, lyrics=False)
    cfg.retag_adopted = True                      # the library is told to write its tags after all

    said = would_do(plan, album_dir, COVER, library, for_album(cfg, plan))
    assert any("retagged" in line or "rewritten" in line for line in said), said

    Service(cfg, library, log=lambda s: None).repair()
    assert embedded_cover(album_dir / plan.tracks[0].filename) == COVER
    assert MFile(album_dir / plan.tracks[0].filename)["comment"] == ["theirs"], \
        "and what noaap does not model is left where it was"


def test_without_that_setting_an_adopted_album_is_left_alone(adopted):
    library, album_dir, plan = adopted
    cfg = Config(library_root=library, musicbrainz=False, lyrics=False)
    before = (album_dir / plan.tracks[0].filename).read_bytes()

    assert would_do(plan, album_dir, COVER, library, for_album(cfg, plan)) == []
    Service(cfg, library, log=lambda s: None).repair()

    assert (album_dir / plan.tracks[0].filename).read_bytes() == before
    assert embedded_cover(album_dir / plan.tracks[0].filename) is None


def test_an_exception_the_user_set_holds_through_a_pass(adopted):
    library, album_dir, plan = adopted
    cfg = Config(library_root=library, musicbrainz=False, lyrics=False)
    cfg.retag_adopted = True
    plan.exceptions = {"cover_embedded": True}     # the user, in the album view: not this one
    save_plan(plan, album_dir)

    assert held_back(cfg, plan) == ["cover_embedded"]
    Service(cfg, library, log=lambda s: None).repair()

    track = album_dir / plan.tracks[0].filename
    assert embedded_cover(track) is None, "the exception held"
    assert MFile(track)["album"] == ["Nocturnes"], "and the rest of the treatment happened"


def test_an_exception_for_the_names_keeps_them_where_the_setting_would_rename(adopted):
    library, album_dir, plan = adopted
    cfg = Config(library_root=library, musicbrainz=False, lyrics=False)
    cfg.rename_adopted = True
    plan.exceptions = {"names": True}
    save_plan(plan, album_dir)

    assert held_back(cfg, plan) == ["rename_adopted"]
    assert would_do(plan, album_dir, COVER, library, for_album(cfg, plan)) == []

    Service(cfg, library, log=lambda s: None).repair()
    assert (album_dir / "01 First.opus").exists(), "their name, because they asked for it"


def test_the_setting_alone_renames_an_adopted_album(adopted):
    library, album_dir, plan = adopted
    cfg = Config(library_root=library, musicbrainz=False, lyrics=False)
    cfg.rename_adopted = True

    said = would_do(plan, album_dir, COVER, library, for_album(cfg, plan))
    assert any("would be renamed" in line for line in said), said

    Service(cfg, library, log=lambda s: None).repair()
    assert not (album_dir / "01 First.opus").exists()
    assert list(Path(album_dir).glob("Aphelion - Nocturnes - 01*.opus")), \
        [p.name for p in album_dir.iterdir()]


def test_a_cover_beside_the_album_comes_out_of_its_own_files(adopted):
    """An adopted album has no published cover address — so its own folder is the address.

    Before this, `cover_beside` was a switch nothing acted on: only a download ever fetched a cover,
    so neither `repair` nor a take-in could put one beside an album somebody already owns.
    """
    library, album_dir, plan = adopted
    (album_dir / "cover.jpg").unlink()               # no cover file, one track carries the picture
    first = album_dir / plan.tracks[0].filename
    tag_file(first, plan, plan.tracks[0], cover=COVER)
    plan.tracks[0].tagged = ""                       # so the pass still has tags to write
    save_plan(plan, album_dir)
    cfg = Config(library_root=library, musicbrainz=False, lyrics=False)
    assert cfg.cover_beside is True

    said = would_do(plan, album_dir, None, library, for_album(cfg, plan))
    assert any("a cover would be saved beside the album" in line for line in said), said

    Service(cfg, library, log=lambda s: None).repair()
    written = sorted(album_dir.glob("cover.*"))
    assert written, [p.name for p in album_dir.iterdir()]
    assert written[0].read_bytes() == COVER, "the picture out of the album's own file"
    assert load_plan(album_dir).cover_fetched.get("sha1"), "and the plan records that noaap wrote it"


def test_without_the_setting_no_cover_is_written_beside_the_album(adopted):
    library, album_dir, plan = adopted
    (album_dir / "cover.jpg").unlink()
    tag_file(album_dir / plan.tracks[0].filename, plan, plan.tracks[0], cover=COVER)
    cfg = Config(library_root=library, musicbrainz=False, lyrics=False)
    cfg.cover_beside = False

    assert would_do(plan, album_dir, None, library, for_album(cfg, plan)) == []
    Service(cfg, library, log=lambda s: None).repair()
    assert not list(album_dir.glob("cover.*")), "nobody asked for one"
