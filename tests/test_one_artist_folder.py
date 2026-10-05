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


# -- which spelling wins, and on what ground (§9, slice 129; R-478, R-479) -----------------------


def test_the_spelling_most_albums_hold_wins_a_case_only_tie():
    """`Umbra et Imago` is in 23 albums of the user's library and `Umbra Et Imago` in 4. Two casings
    of one name are the same length, so the last word used to go to the alphabet — and `'E' < 'e'`,
    so Title Case always won: the rule would have rewritten the 23 to follow the 4."""
    from noaap.service import spelling_rank

    names = {"Umbra et Imago": set(), "Umbra Et Imago": set()}
    counts = {"Umbra et Imago": 23, "Umbra Et Imago": 4}
    best = min(names, key=lambda n: spelling_rank(n, names[n], counts[n]))

    assert best == "Umbra et Imago"


def test_musicbrainz_still_beats_the_count():
    """The user: "such stuff should follow MusicBrainz, not numbers". `DOMINUM` is in 2 albums and
    `Dominum` in 5, and MusicBrainz says the first — so the count never gets a say."""
    from noaap.models import Provenance
    from noaap.service import spelling_rank

    names = {"DOMINUM": {Provenance.MB}, "Dominum": {"file_tags"}}
    counts = {"DOMINUM": 2, "Dominum": 5}
    best = min(names, key=lambda n: spelling_rank(n, names[n], counts[n]))

    assert best == "DOMINUM"


def test_a_spelling_someone_chose_beats_everything():
    from noaap.models import Provenance
    from noaap.service import spelling_rank

    names = {"umbra et imago": {Provenance.USER}, "Umbra et Imago": {Provenance.MB}}
    counts = {"umbra et imago": 1, "Umbra et Imago": 40}
    assert min(names, key=lambda n: spelling_rank(n, names[n], counts[n])) == "umbra et imago"


def test_the_alphabet_is_the_last_word_and_only_that():
    """`Miracle of Sound` and `Miracle Of Sound` are one album each: nothing else can decide."""
    from noaap.service import spelling_rank

    names = {"Miracle of Sound": set(), "Miracle Of Sound": set()}
    counts = {"Miracle of Sound": 1, "Miracle Of Sound": 1}
    assert min(names, key=lambda n: spelling_rank(n, names[n], counts[n])) == "Miracle Of Sound"


def test_the_basis_is_named():
    from noaap.models import Provenance
    from noaap.service import spelling_basis

    both = {"Umbra et Imago": set(), "Umbra Et Imago": set()}
    counts = {"Umbra et Imago": 23, "Umbra Et Imago": 4}
    assert spelling_basis("Umbra et Imago", set(), both, counts) == "count"
    assert spelling_basis("Umbra Et Imago", set(), both, {"Umbra et Imago": 1,
                                                          "Umbra Et Imago": 1}) == "alphabet"
    assert spelling_basis("DOMINUM", {Provenance.MB}, {"DOMINUM": set(), "Dominum": set()}) \
        == "MusicBrainz release"
    assert spelling_basis("x", {Provenance.USER}, {"x": set(), "y": set()}) == "user"
    assert spelling_basis("only", set(), {"only": set()}) == "the only spelling"


def test_a_count_chosen_spelling_is_marked_for_a_later_lookup(tmp_path, one_second_of_sound):
    """R-479, point 1: the count is weak ground, so the plan says so and a pass that asks
    MusicBrainz may replace it without argument."""
    root = tmp_path / "collection"
    for album in ("One", "Two", "Three"):
        an_album(root, "Umbra et Imago", album, ["A"], one_second_of_sound)
    an_album(root, "Umbra Et Imago", "Four", ["A"], one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)

    said = []
    service = a_library(root, retag_adopted=True, rename_adopted=True)
    service.log = said.append
    service.repair()

    assert artists(root) == ["Umbra et Imago"], "the three win, not the one"
    named = [line for line in said if "elsewhere in the library" in line]
    assert named and "(count)" in named[0], said
    moved = load_plan(root / "Umbra et Imago" / "Four")
    assert moved is not None
    assert (moved.adopted or {}).get("spelling") == "by count; MusicBrainz not asked"
