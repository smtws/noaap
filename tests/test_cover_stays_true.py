"""The cover record stays true (DESIGN §9, slice 181; P129).

The share's passes of 2026-10-11 left four things a reader of a cover line could not trust:

1. a MusicBrainz 503, or a lookup where none fitted, put the folder's own `./cover.jpg` in place of
   the release's Cover Art Archive front — a transient non-answer rewrote the plan
   (`Depeche Mode/Policy of Truth`);
2. `Lord of the Lost/Swan Songs II` was announced as a cover change on every check, because YouTube
   signs its thumbnail addresses afresh on every read and the signature was compared;
3. the repair announced 20 covers "fetched again" and 6 "saved from" the archive: 19 + 5 answered 404
   and only five of those said so, the same-bytes one said nothing, and the from-files four said
   nothing either;
4. 19 of those releases have no front of their own, and their group's front answers.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image
from test_intake import QUIET
from test_repointing import an_album

from noaap import download, intake
from noaap.config import Config
from noaap.download import (
    _cover,
    caa_group_front,
    caa_release_front,
    cover_would,
    load_plan,
    save_plan,
    written,
)
from noaap.mb import MusicBrainzError
from noaap.models import AlbumPlan, Kind, PlanTrack
from noaap.plan import changes_from
from noaap.service import Service

RELEASE = "db25c784-8136-4d7f-a300-969eb7e2fe8d"     # Sagittarius/The Kingdom Come's
GROUP = "907234d1-1fa5-471b-b9cc-48d2e212bc0c"


def cover_key(url):
    from noaap.plan import cover_key  # new in slice 181; imported here so every other case runs on 1.56.0

    return cover_key(url)


def a_picture(colour) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (80, 80), colour).save(out, "JPEG")
    return out.getvalue()


RED, BLUE = a_picture((200, 0, 0)), a_picture((0, 0, 200))


def a_library(root, mb=None, **settings):
    cfg = Config(library_root=root, musicbrainz=bool(mb), lyrics=False, **settings)
    return Service(cfg, root, log=lambda s: None, mb=mb)


def taken_in(root, tone):
    album = an_album(root / "A Band" / "An Album", ["One", "Two"], tone)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    for picture in album.glob("cover.*"):
        picture.unlink()
    return album


def with_a_release(album):
    plan = load_plan(album)
    plan.mbid, plan.release_group = RELEASE, GROUP
    plan.cover_url = caa_release_front(RELEASE)
    save_plan(plan, album)
    return plan


@pytest.fixture
def nothing_fetched(monkeypatch):
    """No test reaches the archive; a fetch that is asked for anyway is recorded."""
    asked: list[str] = []
    monkeypatch.setattr(download, "_archive_cover", lambda url, why=None: asked.append(url))
    return asked


# -- 1. a non-answer leaves the plan as it was ------------------------------------------------------


class Unavailable:
    """MusicBrainz answering 503 to everything."""

    def search_releases(self, artist, album):
        raise MusicBrainzError("503 Service Unavailable")

    def release(self, mbid):
        raise MusicBrainzError("503 Service Unavailable")

    def search_recordings(self, artist, title):
        raise MusicBrainzError("503 Service Unavailable")

    def artist(self, name):
        return None

    def artist_albums(self, artist):
        return []


class NoneFits(Unavailable):
    """MusicBrainz answering, with one release whose tracks are other songs."""

    def search_releases(self, artist, album):
        return [{"id": "0ther", "title": "An Album", "score": 100, "track-count": 2,
                 "status": "Official", "artist-credit": [{"name": "A Band", "artist": {"name": "A Band"}}],
                 "date": "2001"}]

    def release(self, mbid):
        credit = [{"name": "A Band", "artist": {"name": "A Band"}}]
        return {"id": mbid, "title": "An Album", "date": "2001", "artist-credit": credit,
                "release-group": {"id": "0ther-group"},
                "media": [{"position": 1, "tracks": [
                    {"position": i, "title": t, "artist-credit": credit, "length": 1000,
                     "recording": {"id": f"r{i}", "length": 1000}}
                    for i, t in enumerate(["Something", "Else"], 1)]}]}

    def search_recordings(self, artist, title):
        return []


@pytest.mark.parametrize("mb", [Unavailable, NoneFits], ids=["503", "none fitted"])
def test_a_lookup_that_gives_no_release_changes_no_cover_record(tmp_path, one_second_of_sound,
                                                                 nothing_fetched, mb):
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    before = with_a_release(album)

    said: list[str] = []
    service = a_library(root, mb=mb())
    service.log = said.append
    service.update_all(report_only=True, deep=True)

    assert not [line for line in said if line.strip().startswith("cover:")], said
    assert [line for line in said if "no release" in line and "not compared" in line], said

    service.update_all(deep=True)

    after = load_plan(album)
    assert (after.cover_url, after.cover_fallback_url, after.release_group, after.mbid) == \
           (before.cover_url, before.cover_fallback_url, before.release_group, before.mbid)


def test_a_reread_of_the_folder_keeps_the_release_s_cover(tmp_path, one_second_of_sound, nothing_fetched):
    """`reread` asks nobody, so it is a non-answer by construction."""
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    before = with_a_release(album)

    a_library(root).reread(album)

    after = load_plan(album)
    assert (after.cover_url, after.release_group) == (before.cover_url, before.release_group)


# -- 2. a signed thumbnail is the same address under a new signature --------------------------------

SIGNED = ("https://i9.ytimg.com/s_p/OLAK5uy_nw-JBmzN855Ek6H6L84OGxi7r_yg7cVj4/sddefault.jpg"
          "?sqp=CLCjrtYGir7X7AMICITro70GEAE=&rs=AOn4CLBlS1YFDY-QVZ6iNzd5TVjMC3BTxw&v=1739126148")
SIGNED_AGAIN = ("https://i9.ytimg.com/s_p/OLAK5uy_nw-JBmzN855Ek6H6L84OGxi7r_yg7cVj4/sddefault.jpg"
                "?sqp=CJGQs9YGir7X7AMICITro70GEAE=&rs=AOn4CLDw1Xn0LQyNbqk8H8U2NzkXu8Xb3Q&v=1739126148")
UNSIGNED = "https://i9.ytimg.com/s_p/OLAK5uy_nw-JBmzN855Ek6H6L84OGxi7r_yg7cVj4/sddefault.jpg?v=1739126148"


def a_youtube_plan(cover_url):
    track = PlanTrack(video_id="abcdefghijk", number=1, artist="Lord of the Lost", title="Fists Up",
                      filename="01 Fists Up.opus", provenance={}, state="done")
    return AlbumPlan(source_url="https://music.youtube.com/playlist?list=OLAK5uy_x", source_id="OLAK5uy_x",
                     kind=Kind.OFFICIAL_ALBUM, album="Swan Songs II", albumartist="Lord of the Lost",
                     year=2020, cover_url=cover_url, folder="Lord of the Lost/Swan Songs II",
                     tracks=[track])


def test_the_signature_is_not_part_of_the_address():
    assert cover_key(SIGNED) == cover_key(SIGNED_AGAIN) == UNSIGNED
    assert cover_key(SIGNED.replace("v=1739126148", "v=1800000000")) != UNSIGNED, "v= is the picture"
    plain = "https://i.ytimg.com/vi/abcdefghijk/maxresdefault.jpg"
    assert cover_key(plain) == plain
    assert cover_key(caa_release_front(RELEASE)) == caa_release_front(RELEASE)
    assert cover_key(None) is None


def test_two_signatures_of_one_picture_are_no_change():
    assert not [line for line in changes_from(a_youtube_plan(SIGNED), a_youtube_plan(SIGNED_AGAIN))
                if line.startswith("cover")]
    moved = a_youtube_plan(SIGNED.replace("v=1739126148", "v=1800000000"))
    assert [line for line in changes_from(a_youtube_plan(SIGNED), moved) if line.startswith("cover")]


def test_the_plan_records_the_address_without_its_signature(tmp_path):
    plan = a_youtube_plan(SIGNED)
    plan.cover_fallback_url = SIGNED
    plan.cover_fetched = {"url": SIGNED, "sha1": "x", "tried": SIGNED}

    out = written(plan, tmp_path)

    assert out["cover_url"] == out["cover_fallback_url"] == UNSIGNED
    assert out["cover_fetched"] == {"url": UNSIGNED, "sha1": "x", "tried": UNSIGNED}
    assert plan.cover_url == SIGNED, "the pass that read it keeps the address that works"


def test_a_new_signature_is_neither_announced_nor_fetched_nor_re_recorded(tmp_path, monkeypatch):
    album = tmp_path / "Lord of the Lost" / "Swan Songs II"
    album.mkdir(parents=True)
    (album / "cover.jpg").write_bytes(RED)
    plan = a_youtube_plan(SIGNED)
    plan.cover_fetched = {"url": SIGNED, "sha1": download._sha1(RED)}
    save_plan(plan, album)
    stored = load_plan(album)
    stored.cover_url = SIGNED_AGAIN                       # what the next read of the playlist says
    recorded = dict(stored.cover_fetched)
    monkeypatch.setattr(download, "_download_cover", lambda *a, **k: pytest.fail("fetched again"))

    assert cover_would(stored, album) == []
    assert _cover(stored, album, source=None) == RED
    assert stored.cover_fetched == recorded


# -- 3. the cover lines say what is true --------------------------------------------------------------


def answering(*urls):
    """The archive as the dry run's look sees it: these addresses answer, every other one is a 404."""
    return lambda url: None if url in urls else "coverartarchive.org answered 404"


def a_repair(root, dry_run, **kw):
    said: list[str] = []
    service = a_library(root, retag_adopted=True, **kw)
    service.log = said.append
    service.repair(dry_run=dry_run)
    return said


def test_the_check_says_could_not_fetch_when_the_archive_has_nothing(tmp_path, one_second_of_sound,
                                                                     monkeypatch, nothing_fetched):
    """Sagittarius/The Kingdom Come: release front 404, group front 404, no picture in the files."""
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    with_a_release(album)
    monkeypatch.setattr(download, "caa_answers", answering())

    said = a_repair(root, dry_run=True)

    assert not [line for line in said if "would be saved beside the album from" in line], said
    lines = [line for line in said if "could not fetch" in line]
    assert lines and caa_release_front(RELEASE) in lines[0] and caa_group_front(GROUP) in lines[0], said
    assert "404" in lines[0]
    assert not [url for url in nothing_fetched if "coverartarchive" in url], "a look is a HEAD, not a fetch"


def test_the_check_names_the_address_that_answers(tmp_path, one_second_of_sound, monkeypatch, nothing_fetched):
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    with_a_release(album)
    monkeypatch.setattr(download, "caa_answers", answering(caa_group_front(GROUP)))

    said = a_repair(root, dry_run=True)

    lines = [line for line in said if "a cover would be saved beside the album" in line]
    assert lines and caa_group_front(GROUP) in lines[0], said
    assert caa_release_front(RELEASE) not in lines[0].split("from")[-1], "only the address that answers"


def ours_beside(album, picture, url):
    (album / "cover.jpg").write_bytes(picture)
    plan = load_plan(album)
    plan.mbid, plan.release_group = RELEASE, GROUP
    plan.cover_url = caa_release_front(RELEASE)
    plan.cover_fetched = {"url": url, "sha1": download._sha1(picture)}
    save_plan(plan, album)


def test_the_check_says_what_a_fetch_again_compares(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    ours_beside(album, RED, caa_group_front(GROUP))

    lines = cover_would(load_plan(album), album)

    assert len(lines) == 1 and caa_release_front(RELEASE) in lines[0], lines
    assert caa_group_front(GROUP) in lines[0], "and where it goes when the release has no front"
    assert "only if the picture differs" in lines[0], lines


def test_a_fetch_again_that_fails_says_so(tmp_path, one_second_of_sound, monkeypatch):
    """The 19 of 2026-10-11: release front 404 — and here the group too — and not a word."""
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    ours_beside(album, RED, "https://elsewhere.example/front")
    monkeypatch.setattr(download, "_archive_cover",
                        lambda url, why=None: why.append("coverartarchive.org answered 404") if why is not None else None)

    said = a_repair(root, dry_run=False)

    lines = [line for line in said if "not fetched again" in line]
    assert len(lines) == 1 and caa_release_front(RELEASE) in lines[0] and "404" in lines[0], said
    assert (album / "cover.jpg").read_bytes() == RED


def test_the_same_picture_again_is_said_and_left_alone(tmp_path, one_second_of_sound, monkeypatch):
    """What the 19 would have been with the group behind the release: their picture *is* the group's."""
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    ours_beside(album, RED, caa_group_front(GROUP))
    stamp = (album / "cover.jpg").stat().st_mtime_ns
    fronts = {caa_group_front(GROUP): RED}
    monkeypatch.setattr(download, "_archive_cover", lambda url, why=None: fronts.get(url))

    said = a_repair(root, dry_run=False)

    lines = [line for line in said if "the picture already beside the album" in line]
    assert len(lines) == 1 and caa_group_front(GROUP) in lines[0], said
    assert (album / "cover.jpg").stat().st_mtime_ns == stamp, "not written again"
    assert load_plan(album).cover_fetched["tried"] == caa_release_front(RELEASE)


# -- 4. a release with no front of its own gets its group's -------------------------------------------


def test_a_fetch_again_falls_back_to_the_group(tmp_path, one_second_of_sound, monkeypatch):
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    ours_beside(album, RED, "https://elsewhere.example/front")
    fronts = {caa_group_front(GROUP): BLUE}
    monkeypatch.setattr(download, "_archive_cover", lambda url, why=None: fronts.get(url))

    said = a_repair(root, dry_run=False)

    assert (album / "cover.jpg").read_bytes() == BLUE
    assert load_plan(album).cover_fetched["url"] == caa_group_front(GROUP)
    assert [line for line in said if "replaced" in line and caa_group_front(GROUP) in line], said


def test_a_folder_with_no_picture_in_its_files_says_so_once(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    plan = load_plan(album)
    plan.cover_url = str(album)
    plan.tracks[0].video_id = str(album / "an old name.opus")   # something else for the pass to do
    plan.tracks[0].sync_candidates()
    save_plan(plan, album)

    said = a_repair(root, dry_run=False)

    assert len([line for line in said if "no picture in its files" in line]) == 1, said


# -- q7: an ids-only fault is not a folder named elsewhere --------------------------------------------


def test_an_ids_only_re_point_says_what_it_re_pointed(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    plan = load_plan(album)
    for track in plan.tracks:
        track.video_id = str(album / f"old {track.filename}")
        track.sync_candidates()
    save_plan(plan, album)

    said = a_repair(root, dry_run=False)

    assert [line for line in said if "re-pointed 2 track id(s) at their files" in line], said
    assert not [line for line in said if "elsewhere" in line], said
