"""One spelling per artist, from the MusicBrainz artist entity (DESIGN §9, slice 138; R-494).

Slice 129 chose a case-only spelling by how many albums hold it, and marked the choice `by count;
MusicBrainz not asked` because the user's word is *"such stuff should follow MusicBrainz, not
numbers"*. This is the lookup it was waiting for — and it is asked of the **artist**, not of a
release: a release's artist *credit* is per release and may carry a sleeve's stylisation. Pinning
`DOMINUM — Night is Calling` to its 2-CD release showed exactly that, offering to rename thirteen
files to `Dominum - …` because that one sleeve says so.
"""

from __future__ import annotations

import shutil

import pytest
from mutagen import File as MFile
from test_intake import QUIET

from noaap import intake
from noaap.config import Config
from noaap.download import load_plan
from noaap.models import Provenance
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


class ArtistMB:
    """Answers the artist endpoint for one name, and counts the asking."""

    def __init__(self, says: str | None = "Dominum"):
        self.says, self.asked = says, []

    def artist(self, name):
        self.asked.append(name)
        return {"id": "a1", "name": self.says} if self.says else None

    def search_releases(self, artist, album):
        return []

    def search_recordings(self, artist, title):
        return []

    def release(self, mbid):
        return None

    def artist_albums(self, artist):
        return []


def a_library(root, mb=None, **settings):
    cfg = Config(library_root=root, lyrics=False, musicbrainz=mb is not None, **settings)
    service = Service(cfg, root, log=lambda s: None)
    service._mb = mb
    return service


def two_spellings(root, tone):
    """What the user's library held: five albums one way, two the other."""
    for n in range(1, 6):
        an_album(root, "DOMINUM", f"Loud {n}", ["One"], tone)
    for n in range(1, 3):
        an_album(root, "Dominum", f"Quiet {n}", ["One"], tone)
    return root


def by_count(root, tone):
    """A case-only pair that neither case penalty can decide, so the count does: the user's own
    `Umbra et Imago` (23 albums) against `Umbra Et Imago` (4)."""
    for n in range(1, 4):
        an_album(root, "Umbra et Imago", f"Lower {n}", ["One"], tone)
    an_album(root, "Umbra Et Imago", "Upper 1", ["One"], tone)
    return root


def artists(root):
    return sorted(p.name for p in root.iterdir() if p.is_dir())


# -- the entity decides ---------------------------------------------------------------------------


def test_the_artist_entity_outranks_the_count(tmp_path, one_second_of_sound):
    """Five albums say `DOMINUM` and two say `Dominum`; the entity says `Dominum`, so `Dominum` it
    is. Slice 129's count would have answered the other way, and said so in the plan."""
    root = two_spellings(tmp_path / "collection", one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    mb = ArtistMB("Dominum")

    said = []
    service = a_library(root, mb=mb, retag_adopted=True, rename_adopted=True)
    service.log = said.append
    service.repair()

    assert artists(root) == ["Dominum"], artists(root)
    assert [line for line in said if "(MusicBrainz artist)" in line], said
    moved = load_plan(root / "Dominum" / "Loud 1")
    assert moved is not None and moved.albumartist == "Dominum"
    assert moved.provenance["albumartist"] == Provenance.MB
    assert "spelling" not in (moved.adopted or {}), "not a count any more, so not marked as one"


def test_without_musicbrainz_the_count_still_decides(tmp_path, one_second_of_sound):
    root = by_count(tmp_path / "collection", one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)

    said = []
    service = a_library(root, retag_adopted=True, rename_adopted=True)
    service.log = said.append
    service.repair()

    assert artists(root) == ["Umbra et Imago"], "the three, by count"
    assert [line for line in said if "(count)" in line], said


def test_an_entity_that_does_not_answer_leaves_the_tie_break_alone(tmp_path, one_second_of_sound):
    root = by_count(tmp_path / "collection", one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    mb = ArtistMB(None)

    said = []
    service = a_library(root, mb=mb, retag_adopted=True, rename_adopted=True)
    service.log = said.append
    service.repair()

    assert mb.asked, "it was asked"
    assert artists(root) == ["Umbra et Imago"], "and the count answered"
    assert [line for line in said if "(count)" in line], said


def test_a_spelling_the_user_chose_outranks_the_entity(tmp_path, one_second_of_sound):
    root = two_spellings(tmp_path / "collection", one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    mine = root / "Dominum" / "Quiet 1"
    plan = load_plan(mine)
    plan.provenance["albumartist"] = Provenance.USER
    plan.albumartist = "DoMiNuM"
    from noaap.download import save_plan
    save_plan(plan, mine)

    said = []
    service = a_library(root, mb=ArtistMB("Dominum"), retag_adopted=True, rename_adopted=True)
    service.log = said.append
    service.repair()

    assert artists(root) == ["DoMiNuM"], artists(root)
    assert [line for line in said if "(user)" in line], said


def test_the_artist_is_asked_once_per_key_however_many_albums(tmp_path, one_second_of_sound):
    root = two_spellings(tmp_path / "collection", one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    mb = ArtistMB("Dominum")

    service = a_library(root, mb=mb, retag_adopted=True, rename_adopted=True)
    service.repair()

    assert len(mb.asked) == 1, mb.asked


def test_nothing_is_asked_about_an_artist_whose_albums_are_private(tmp_path, one_second_of_sound):
    """A lookup sends a name to somebody else's server, and for an album a patron paid for nobody
    asked for that (§9, slice 72). The question is what is forbidden, not only the answer."""
    root = by_count(tmp_path / "collection", one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    from noaap.download import save_plan
    for artist in ("Umbra et Imago", "Umbra Et Imago"):
        for album in (root / artist).iterdir():
            plan = load_plan(album)
            plan.lookups = False
            save_plan(plan, album)
    mb = ArtistMB("Dominum")

    service = a_library(root, mb=mb, retag_adopted=True, rename_adopted=True)
    service.repair()

    assert mb.asked == [], "its name was never sent anywhere"
    assert artists(root) == ["Umbra et Imago"], "decided here, by count"


def test_the_case_penalty_is_named_as_itself(tmp_path, one_second_of_sound):
    """`alphabet` was said where the case penalty had decided. `DOMINUM` (5 albums) loses to
    `Dominum` (2) on that ground and on no other, and the check now says which."""
    from noaap.service import spelling_basis

    both = {"DOMINUM": set(), "Dominum": set()}
    counts = {"DOMINUM": 5, "Dominum": 2}

    assert spelling_basis("Dominum", set(), both, counts) == "case"
    lower = {"umbra et imago": set(), "Umbra et Imago": set()}
    assert spelling_basis("Umbra et Imago", set(), lower, {}) == "case"
    tie = {"Umbra et Imago": set(), "Umbra Et Imago": set()}
    assert spelling_basis("Umbra et Imago", set(), tie, {"Umbra et Imago": 23,
                                                         "Umbra Et Imago": 4}) == "count"
    assert spelling_basis("Umbra Et Imago", set(), tie, {}) == "alphabet"


def test_an_update_writes_the_entitys_spelling_too(tmp_path, one_second_of_sound):
    """R-494: `repair` and `update` both apply it, so a fetch does not leave the old spelling
    standing until the next repair."""
    root = tmp_path / "collection"
    an_album(root, "DOMINUM", "Loud 1", ["One"], one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)

    said = []
    service = a_library(root, mb=ArtistMB("Dominum"), retag_adopted=True, rename_adopted=True)
    service.log = said.append
    service.update_all(deep=True)

    assert [line for line in said if "MusicBrainz calls this artist 'Dominum'" in line], said
    after = load_plan(root / "Dominum" / "Loud 1")
    assert after is not None and after.albumartist == "Dominum"
    assert after.provenance["albumartist"] == Provenance.MB
