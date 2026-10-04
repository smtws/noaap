"""One spelling per artist, even for an album taken in as it stood (DESIGN §9, slice 126; R-474).

Measured on the user's share after the merge: `Die Legende Von Nord` holds four albums and
`Die Legende von Nord` holds `Angst im Dunkeln`, whose own tags say `von`. On a case-sensitive
filesystem those are two artists, two folders and two places to look.

`_apply_spelling` settles this, and it is gated behind `tidy_adopted_tags` — rightly, because
reading a folder's tags the way a video title is read is what slice 111 forbids. But a case-only
difference is not a reading of anything: nothing of what the tag says changes, only how it is cased,
and leaving it costs one folder per spelling.
"""

from __future__ import annotations

import shutil

import pytest
from mutagen import File as MFile
from test_intake import QUIET

from noaap import intake
from noaap.config import Config
from noaap.download import load_plan
from noaap.service import Service


def an_album(root, artist, album, titles, tone):
    folder = root / artist / album
    folder.mkdir(parents=True, exist_ok=True)
    for n, title in enumerate(titles, 1):
        path = folder / f"{n:02d} {title}.opus"
        shutil.copy(tone, path)
        audio = MFile(path)
        audio["title"], audio["artist"] = [title], [artist]
        audio["albumartist"], audio["album"] = [artist], [album]
        audio["tracknumber"] = [str(n)]
        audio.save()
    return folder


def a_library(root, **settings):
    cfg = Config(library_root=root, musicbrainz=False, lyrics=False, **settings)
    return Service(cfg, root, log=lambda s: None)


@pytest.fixture
def two_spellings(tmp_path, one_second_of_sound):
    """Four albums saying `Von`, one saying `von` — the shape on the share."""
    root = tmp_path / "collection"
    for album in ("Frost", "Lang lebe der König", "Magie und Melodie", "Tod und Spiele"):
        an_album(root, "Die Legende Von Nord", album, ["One"], one_second_of_sound)
    an_album(root, "Die Legende von Nord", "Angst im Dunkeln", ["One"], one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    return root


def artists(root):
    return sorted(p.name for p in root.iterdir() if p.is_dir())


# -- the case that was left behind ---------------------------------------------------------------


def test_an_adopted_album_gets_the_librarys_casing(two_spellings):
    root = two_spellings
    assert artists(root) == ["Die Legende Von Nord", "Die Legende von Nord"], "two folders"

    said = []
    service = a_library(root, retag_adopted=True, rename_adopted=True)
    service.log = said.append
    service.repair()

    assert artists(root) == ["Die Legende Von Nord"], "one artist, one folder"
    assert [line for line in said if "elsewhere in the library" in line], said
    assert [line for line in said if "given the library's spelling" in line], said
    moved = load_plan(root / "Die Legende Von Nord" / "Angst im Dunkeln")
    assert moved is not None and moved.albumartist == "Die Legende Von Nord"


def test_the_check_says_it_and_writes_nothing(two_spellings):
    root = two_spellings

    said = []
    service = a_library(root, retag_adopted=True, rename_adopted=True)
    service.log = said.append
    service.repair(dry_run=True)

    assert [line for line in said if "would be given the library's spelling" in line], said
    assert artists(root) == ["Die Legende Von Nord", "Die Legende von Nord"], "nothing moved"


def test_the_tag_follows_the_plan(two_spellings):
    root = two_spellings

    a_library(root, retag_adopted=True, rename_adopted=True).repair()

    one = next((root / "Die Legende Von Nord" / "Angst im Dunkeln").glob("*.opus"))
    assert MFile(one)["albumartist"] == ["Die Legende Von Nord"]


# -- and only case ------------------------------------------------------------------------------


def test_a_different_name_is_not_a_different_casing(tmp_path, one_second_of_sound):
    """`JBO` against `J.B.O.` is `_apply_spelling`'s question and stays behind `tidy_adopted_tags`:
    this one only ever changes how a name is cased."""
    root = tmp_path / "collection"
    an_album(root, "J.B.O.", "Laut", ["One"], one_second_of_sound)
    an_album(root, "JBO", "Leise", ["One"], one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    before = artists(root)

    a_library(root, retag_adopted=True, rename_adopted=True).repair()

    assert artists(root) == before, "two names, and this rule does not touch them"


def test_a_spelling_the_user_chose_is_never_touched(two_spellings):
    from noaap.download import save_plan
    from noaap.models import Provenance

    root = two_spellings
    album = root / "Die Legende von Nord" / "Angst im Dunkeln"
    plan = load_plan(album)
    plan.provenance["albumartist"] = Provenance.USER
    save_plan(plan, album)

    a_library(root, retag_adopted=True, rename_adopted=True).repair()

    assert album.is_dir(), "theirs, and it stays"
    assert load_plan(album).albumartist == "Die Legende von Nord"


def test_one_album_of_one_spelling_is_left_alone(tmp_path, one_second_of_sound):
    """Nothing to decide between: one spelling is the library's spelling."""
    root = tmp_path / "collection"
    an_album(root, "Die Legende von Nord", "Angst im Dunkeln", ["One"], one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)

    said = []
    service = a_library(root, retag_adopted=True, rename_adopted=True)
    service.log = said.append
    service.repair()

    assert artists(root) == ["Die Legende von Nord"]
    assert not [line for line in said if "elsewhere in the library" in line], said
