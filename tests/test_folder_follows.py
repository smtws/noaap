"""A plan whose source is its own folder follows that folder (DESIGN §9, slice 178; P126).

The library-wide apply of 2026-10-10 renamed 246 adopted albums — `Within Temptation/Shed my Skin`
became `Shed My Skin` — and left `source_url`, `source_id` and `cover_fallback_url` on the old name.
The dry run of 2026-10-11 then handed each of those paths to yt-dlp and failed them all with
`ERROR: [generic] '<path>' is not a valid URL`: 246 albums nobody could say anything about.
"""

from __future__ import annotations

import shutil

import pytest
from test_intake import QUIET
from test_repointing import a_library, an_album

from noaap import intake, sources
from noaap.download import follow_folder, load_plan, point_at, points_elsewhere, relocate, save_plan
from noaap.service import Service


def adopted(root, tone, folder="a band/an album"):
    """An adopted album in the owner's spelling, every address naming that folder — the state the
    246 were in before the apply moved them."""
    album = an_album(root / folder, ["One", "Two"], tone, artist="A Band", album="An Album")
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    if (root / "A Band" / "An Album").is_dir() and not album.is_dir():
        shutil.move(root / "A Band" / "An Album", album)
        shutil.rmtree(root / "A Band", ignore_errors=True)
    (album / "cover.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    plan = load_plan(album)
    plan.folder = folder
    plan.source_id = plan.source_url = str(album)
    plan.cover_url = str(album / "cover.jpg")
    plan.cover_fallback_url = str(album / "cover.jpg")
    for track in plan.tracks:
        track.video_id = str(album / track.filename)
        track.sync_candidates()
    save_plan(plan, album)
    return album


class NeverYtDlp:
    """A default provider that fails the test if a path ever reaches it."""

    name = "youtube"

    def handles(self, address):
        return "youtube" in address

    def capabilities(self):
        return frozenset()

    def collection(self, address):
        raise AssertionError(f"a path reached yt-dlp: {address}")

    def changed(self, address):
        raise AssertionError(f"a path reached yt-dlp: {address}")


# -- 1. a rename by the album line moves the three fields with the folder ----------------------------


def test_an_update_that_renames_the_album_moves_all_three_fields(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    album = adopted(root, one_second_of_sound)

    service = a_library(root, retag_adopted=True, rename_adopted=True)
    service.update_all(deep=True)

    moved = root / "A Band" / "An Album"
    assert moved.is_dir() and not album.exists(), "the update moved it into the scheme"
    after = load_plan(moved)
    assert after.source_url == after.source_id == str(moved)
    assert after.cover_fallback_url == str(moved / "cover.jpg"), after.cover_fallback_url
    assert points_elsewhere(after, moved) is False


def test_relocate_moves_what_names_the_folder_and_nothing_else(tmp_path, one_second_of_sound):
    """In the same write as the plan: `relocate` changes the plan, whoever saves it saves both."""
    root = tmp_path / "collection"
    album = adopted(root, one_second_of_sound)
    plan = load_plan(album)
    plan.cover_url = "https://coverartarchive.org/release/x/front-500"

    service = a_library(root, rename_adopted=True)
    from noaap.treatment import for_album
    here = relocate(album, plan, root, for_album(service.cfg, plan))

    assert here == root / "A Band" / "An Album"
    assert plan.source_url == plan.source_id == str(here)
    assert plan.cover_fallback_url == str(here / "cover.jpg")
    assert plan.cover_url == "https://coverartarchive.org/release/x/front-500", "a URL is not a folder"


def test_a_folder_whose_name_begins_with_another_is_not_inside_it():
    from noaap.models import AlbumPlan, Kind
    plan = AlbumPlan(source_url="/m/A/What Lies Beneath (Special Edition)",
                     source_id="/m/A/What Lies Beneath (Special Edition)", kind=Kind.OFFICIAL_ALBUM,
                     album="What Lies Beneath", albumartist="A", year=None, cover_url=None,
                     folder="A/What Lies Beneath", tracks=[])
    assert follow_folder(plan, "/m/A/What Lies Beneath", "/m/A/Elsewhere") is False
    assert plan.source_url == "/m/A/What Lies Beneath (Special Edition)"


def test_a_case_only_and_a_longer_old_name_are_both_re_pointed(tmp_path, one_second_of_sound):
    """20 of the 246 named a folder whose name *begins* with the new one's; 113 differed in case."""
    root = tmp_path / "collection"
    album = adopted(root, one_second_of_sound, folder="A Band/An Album")
    for old in (album.parent / "An Album (Special Edition)", album.parent / "an album"):
        plan = load_plan(album)
        plan.source_id = plan.source_url = str(old)
        plan.cover_fallback_url = str(old / "cover.jpg")

        assert points_elsewhere(plan, album) is True, old
        assert point_at(plan, album) is True
        assert plan.source_url == plan.source_id == str(album)
        assert plan.cover_fallback_url == str(album / "cover.jpg")


# -- 2. update never hands a filesystem path to yt-dlp ------------------------------------------------


def stale(root, tone):
    """The state the share is in now: moved, and the plan still on the old name."""
    album = adopted(root, tone, folder="A Band/An Album")
    plan = load_plan(album)
    old = album.parent / "An album"
    plan.source_id = plan.source_url = str(old)
    plan.cover_fallback_url = str(old / "cover.jpg")
    save_plan(plan, album)
    return album, old


@pytest.mark.parametrize("report_only", [True, False])
def test_a_folder_that_is_gone_fails_in_one_sentence(tmp_path, one_second_of_sound, report_only):
    root = tmp_path / "collection"
    album, old = stale(root, one_second_of_sound)
    said = []
    service = Service(a_library(root).cfg, root, log=said.append, yt=NeverYtDlp())

    [outcome] = service.update_all(report_only=report_only, deep=True)

    assert outcome.status == "failed"
    failed = [line for line in said if "failed" in line]
    assert failed == [f"  failed: the folder this album was read from is gone ({old}); the album is in "
                      f"{album} — `noaap repair` points its plan there"], said
    assert not [line for line in said if "not a valid URL" in line]


def test_a_path_nobody_claims_is_never_given_to_the_default_provider(tmp_path):
    service = Service(a_library(tmp_path).cfg, tmp_path, yt=NeverYtDlp())
    with pytest.raises(sources.SourceError, match="there is no folder at /gone/A Band/An Album"):
        service.source_for_address("/gone/A Band/An Album")
    assert isinstance(service.source_for_address("https://www.youtube.com/playlist?list=x"), NeverYtDlp)


# -- 3. repair mends the stored ones ------------------------------------------------------------------


def test_repair_re_points_a_stale_plan_names_it_and_counts_it(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    album, old = stale(root, one_second_of_sound)
    said = []
    service = a_library(root)
    service.log = said.append

    service.repair(dry_run=True)

    assert "  would be re-pointed at its own folder (its plan named 'An album')" in said, said
    assert "1 plan(s) would be re-pointed at their own album folder" in said
    assert load_plan(album).source_url == str(old), "a check writes nothing"

    said.clear()
    service.repair()

    after = load_plan(album)
    assert after.source_url == after.source_id == str(album)
    assert after.cover_fallback_url == str(album / "cover.jpg")
    assert "1 plan(s) re-pointed at their own album folder" in said, said

    said.clear()
    service.yt = NeverYtDlp()
    service.update_all(report_only=True, deep=True)
    assert not [line for line in said if "failed" in line], said
