"""An album with no cover of its own takes its release's artwork (DESIGN §9, slice 141; R-504).

The user pinned `Apocalyptica/Cult` to release `73fcbc7e…` and the lyrics followed, but no cover
arrived. Its plan held `cover_url` = the album folder — what an adopted album's address is (slice
101) — no picture in any of its flacs, and `cover_fetched` empty. Every pass said
`could not fetch any cover`, because a folder address is truthy and nothing after it was reached.

And the archive's **release group** front is whichever edition it picked for the group: for that
pin it redirects to `a1b9ddb1…`, a different edition's artwork for an album somebody named by hand.
"""

from __future__ import annotations

from pathlib import Path

from noaap.download import CAA, _cover_addresses, caa_release_front, is_caa_group
from noaap.models import AlbumPlan, Kind, PlanTrack

RELEASE = "73fcbc7e-4945-4b33-bdc0-671a0aeffdc4"
GROUP = "025381e4-0fd8-393f-b282-0f271f247486"


def a_plan(**kw) -> AlbumPlan:
    base = dict(source_url="/music/Apocalyptica/Cult", source_id="/music/Apocalyptica/Cult",
                kind=Kind.OFFICIAL_ALBUM, album="Cult", albumartist="Apocalyptica", year=2001,
                cover_url=None, folder="Apocalyptica/Cult", provider="folder",
                tracks=[PlanTrack(video_id="/music/Apocalyptica/Cult/01.flac", number=1,
                                  artist="Apocalyptica", title="Path Vol. II",
                                  filename="01.flac", provenance={}, state="done")])
    return AlbumPlan(**{**base, **kw})


# -- the addresses ------------------------------------------------------------------------------


def test_the_release_front_is_an_address_of_its_own():
    assert caa_release_front(RELEASE) == f"{CAA}/release/{RELEASE}/front-500"
    assert caa_release_front(None) is None


def test_a_group_address_is_told_from_a_release_one():
    assert is_caa_group(f"{CAA}/release-group/{GROUP}/front-500") is True
    assert is_caa_group(f"{CAA}/release/{RELEASE}/front-500") is False
    assert is_caa_group("/music/Apocalyptica/Cult") is False
    assert is_caa_group(None) is False


def test_the_users_own_shape_reaches_the_archive(tmp_path):
    """`Apocalyptica/Cult`: a pinned release, the album folder for an address, no picture inside."""
    album = tmp_path / "Apocalyptica" / "Cult"
    plan = a_plan(mbid=RELEASE, cover_url=str(album), adopted={"folder": "Apocalyptica/Cult"})

    addresses = _cover_addresses(plan, album)

    assert str(album) in addresses, "its own files are still asked first"
    assert caa_release_front(RELEASE) in addresses, addresses
    assert addresses.index(str(album)) < addresses.index(caa_release_front(RELEASE))


def test_the_release_comes_before_its_group(tmp_path):
    """After an enrich the plan may hold the group's front. The release's own art is this edition's,
    so it is tried first."""
    plan = a_plan(mbid=RELEASE, cover_url=f"{CAA}/release-group/{GROUP}/front-500")

    addresses = _cover_addresses(plan, tmp_path)

    assert addresses[0] == caa_release_front(RELEASE), addresses
    assert addresses[1] == f"{CAA}/release-group/{GROUP}/front-500"


def test_an_album_with_no_release_is_unchanged(tmp_path):
    album = tmp_path / "Apocalyptica" / "Cult"
    plan = a_plan(cover_url=None, adopted={"folder": "Apocalyptica/Cult"})

    assert _cover_addresses(plan, album) == [str(album)], "slice 101's own-folder address, alone"


def test_a_published_cover_url_still_comes_first(tmp_path):
    """A fetched album's own cover address is what its source published; the archive is a fallback
    behind it, never in front of it."""
    plan = a_plan(mbid=RELEASE, provider="youtube",
                  cover_url="https://i.ytimg.com/vi/abc/maxresdefault.jpg")

    addresses = _cover_addresses(plan, tmp_path)

    assert addresses[0] == "https://i.ytimg.com/vi/abc/maxresdefault.jpg"
    assert caa_release_front(RELEASE) in addresses


def test_a_file_the_user_put_there_is_the_address_and_is_not_displaced(tmp_path):
    album = tmp_path / "Apocalyptica" / "Cult"
    album.mkdir(parents=True)
    mine = album / "cover.jpg"
    mine.write_bytes(b"\xff\xd8\xff\xd9")
    plan = a_plan(mbid=RELEASE, cover_url=str(mine))

    addresses = _cover_addresses(plan, album)

    assert addresses[0] == str(mine), addresses


# -- what the enrich writes ----------------------------------------------------------------------


def test_an_enriched_album_holds_the_release_front_and_the_group_behind_it():
    from noaap.enrich import enrich_release

    credit = [{"name": "Apocalyptica", "artist": {"name": "Apocalyptica"}}]
    release = {"id": RELEASE, "title": "Cult", "date": "2001-11-06", "artist-credit": credit,
               "release-group": {"id": GROUP, "first-release-date": "2000-01-01"},
               "media": [{"position": 1, "tracks": [
                   {"position": 1, "title": "Path Vol. II", "artist-credit": credit,
                    "recording": {"id": "rec-1"}}]}]}

    class MB:
        def release(self, mbid):
            return release

        def search_releases(self, artist, album):
            return []

        def search_recordings(self, artist, title):
            return []

        def artist(self, name):
            return None

        def artist_albums(self, artist):
            return []

    plan = a_plan(mbid=RELEASE)
    plan.provenance["mbid"] = "user"

    assert enrich_release(plan, MB()) is True
    assert plan.cover_url == caa_release_front(RELEASE)
    # the group only fills a slot that would otherwise be empty: this album had nothing
    assert plan.cover_fallback_url == f"{CAA}/release-group/{GROUP}/front-500"


def test_what_the_album_already_had_stays_the_fallback():
    """A source's own art is *this* album's picture, which is worth more as a second try than
    another edition's (§9, slice 141)."""
    from noaap.enrich import enrich_release

    credit = [{"name": "Apocalyptica", "artist": {"name": "Apocalyptica"}}]
    release = {"id": RELEASE, "title": "Cult", "date": "2001-11-06", "artist-credit": credit,
               "release-group": {"id": GROUP, "first-release-date": "2000-01-01"},
               "media": [{"position": 1, "tracks": [
                   {"position": 1, "title": "Path Vol. II", "artist-credit": credit,
                    "recording": {"id": "rec-1"}}]}]}

    class MB:
        def release(self, mbid):
            return release

        def search_releases(self, artist, album):
            return []

        def search_recordings(self, artist, title):
            return []

        def artist(self, name):
            return None

        def artist_albums(self, artist):
            return []

    plan = a_plan(mbid=RELEASE, cover_url="https://i.ytimg.com/s_p/OLAK5uy_abc/sddefault.jpg")
    plan.provenance["mbid"] = "user"

    assert enrich_release(plan, MB()) is True
    assert plan.cover_url == caa_release_front(RELEASE)
    assert plan.cover_fallback_url == "https://i.ytimg.com/s_p/OLAK5uy_abc/sddefault.jpg"


# -- the archive is nobody's provider ------------------------------------------------------------


def test_the_archive_is_fetched_here_and_not_by_the_provider(monkeypatch):
    """`source.art` is the provider's own way of getting a picture: the folder provider reads a path
    and raises `no cover in …` for anything else, so handing it a `coverartarchive.org` address could
    only ever fail — which is the other half of why the user's pin never got a cover."""
    import noaap.download as dl

    asked = []

    class Folder:
        name = "folder"

        def art(self, address):
            asked.append(address)
            raise OSError(f"no cover in {address}")

    class Answer:
        status_code = 200
        content = b"\xff\xd8\xff\xd9"      # a tiny JPEG

    monkeypatch.setattr(dl, "image_mime", lambda data: "image/jpeg" if data[:2] == b"\xff\xd8" else None)
    monkeypatch.setattr("httpx.get", lambda url, **kw: Answer())

    got = dl._download_cover(caa_release_front(RELEASE), Folder())

    assert got is not None and got[1] == Answer.content
    assert asked == [], "the provider was never troubled with it"


def test_only_the_archive_and_its_redirect_target_are_fetched(monkeypatch):
    import noaap.download as dl

    monkeypatch.setattr("httpx.get", lambda url, **kw: (_ for _ in ()).throw(
        AssertionError(f"should not have been fetched: {url}")))

    assert dl._archive_cover("https://example.com/cover.jpg") is None
    assert dl._archive_cover("http://coverartarchive.org/release/x/front-500") is None, "https only"
    assert dl._archive_cover("/music/Apocalyptica/Cult") is None


def test_an_archive_that_says_no_is_a_reason_not_a_silence(monkeypatch):
    import noaap.download as dl

    class Missing:
        status_code = 404
        content = b""

    monkeypatch.setattr("httpx.get", lambda url, **kw: Missing())
    why: list[str] = []

    assert dl._archive_cover(caa_release_front(RELEASE), why) is None
    assert why == ["coverartarchive.org answered 404"], why


# -- what the check says, and what the skip counts (§9, slice 141) --------------------------------


def test_the_check_names_the_archive_without_holding_the_album_open(tmp_path, one_second_of_sound):
    """A hedged cover line promises nothing (slice 124), so it may not keep an album out of the skip
    for ever — an archive with no art for this release would leave every pass looking at it again.
    But a reader asking "will this album get a cover?" must see that it will be tried: the user's
    `Apocalyptica/Cult` had a pinned release and a check that said nothing at all about a cover."""
    from test_intake import QUIET
    from test_repointing import a_library, an_album

    from noaap import intake
    from noaap.download import load_plan, save_plan

    root = tmp_path / "collection"
    an_album(root / "Apocalyptica" / "Cult", ["Path Vol. II"], one_second_of_sound,
             artist="Apocalyptica", album="Cult")
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    album = root / "Apocalyptica" / "Cult"
    for picture in album.glob("cover.*"):
        picture.unlink()
    plan = load_plan(album)
    plan.mbid, plan.provenance["mbid"] = RELEASE, "user"
    plan.cover_url = str(album)
    save_plan(plan, album)

    said = []
    service = a_library(root, retag_adopted=True)
    service.log = said.append
    service.repair(dry_run=True)

    cover_lines = [line for line in said if "a cover would be saved" in line]
    assert cover_lines, said
    assert caa_release_front(RELEASE) in cover_lines[0], cover_lines
    assert "if a picture can be found in its files" in cover_lines[0]


def test_a_hedged_cover_line_alone_does_not_un_skip_an_album(tmp_path, one_second_of_sound):
    """With nothing else to do and no release to ask about, the album is skipped as before."""
    from test_intake import QUIET
    from test_repointing import a_library, an_album

    from noaap import intake

    root = tmp_path / "collection"
    an_album(root / "A Band" / "An Album", ["One"], one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    for picture in (root / "A Band" / "An Album").glob("cover.*"):
        picture.unlink()

    said = []
    service = a_library(root, retag_adopted=True)
    service.log = said.append
    service.repair(dry_run=True)

    assert not [line for line in said if line.startswith("=== ")], said


# -- the release group, for a release with no front of its own (§9, slice 144; R-512 item 1) ------

AMPLIFIED = "31a80627-5b74-4456-bf0d-770472566220"
AMPLIFIED_GROUP = "ff01a595-ff51-395a-9ed1-b2c93f54f659"


def test_the_group_is_tried_behind_the_release(tmp_path):
    """`Apocalyptica/Amplified — A Decade of Reinventing the Cello`, pinned by the user to
    `31a80627`: that release's front is a **404** and its group's is a **307**. Measured against the
    archive. The group was never tried, because the plan did not hold its id — only MusicBrainz knows
    it, and the address list is offline."""
    from noaap.download import caa_group_front

    album = tmp_path / "Apocalyptica" / "Amplified"
    plan = a_plan(mbid=AMPLIFIED, release_group=AMPLIFIED_GROUP, cover_url=str(album),
                  adopted={"folder": "Apocalyptica/Amplified"})

    addresses = _cover_addresses(plan, album)

    assert addresses == [str(album), caa_release_front(AMPLIFIED), caa_group_front(AMPLIFIED_GROUP)]


def test_without_a_group_nothing_is_invented(tmp_path):
    album = tmp_path / "Apocalyptica" / "Amplified"
    plan = a_plan(mbid=AMPLIFIED, cover_url=str(album), adopted={"folder": "x"})

    assert _cover_addresses(plan, album) == [str(album), caa_release_front(AMPLIFIED)]


def test_the_group_front_is_an_address_of_its_own():
    from noaap.download import caa_group_front

    assert caa_group_front(AMPLIFIED_GROUP) == f"{CAA}/release-group/{AMPLIFIED_GROUP}/front-500"
    assert caa_group_front(None) is None


def test_a_lookup_writes_the_group_down():
    """Only MusicBrainz knows it, so the pass that is told remembers it — and every later pass can
    then reach the group offline."""
    from noaap.enrich import enrich_release

    credit = [{"name": "Apocalyptica", "artist": {"name": "Apocalyptica"}}]
    release = {"id": AMPLIFIED, "title": "Cult", "date": "2008", "artist-credit": credit,
               "release-group": {"id": AMPLIFIED_GROUP, "first-release-date": "2006"},
               "media": [{"position": 1, "tracks": [
                   {"position": 1, "title": "Path Vol. II", "artist-credit": credit,
                    "recording": {"id": "rec-1"}}]}]}

    class MB:
        def release(self, mbid):
            return release

        def search_releases(self, artist, album):
            return []

        def search_recordings(self, artist, title):
            return []

        def artist(self, name):
            return None

        def artist_albums(self, artist):
            return []

    plan = a_plan(mbid=AMPLIFIED)
    plan.provenance["mbid"] = "user"
    assert plan.release_group is None

    assert enrich_release(plan, MB()) is True
    assert plan.release_group == AMPLIFIED_GROUP


def test_a_group_already_in_the_cover_address_is_not_listed_twice(tmp_path):
    plan = a_plan(mbid=AMPLIFIED, release_group=AMPLIFIED_GROUP,
                  cover_url=f"{CAA}/release-group/{AMPLIFIED_GROUP}/front-500")

    addresses = _cover_addresses(plan, tmp_path)

    assert addresses.count(f"{CAA}/release-group/{AMPLIFIED_GROUP}/front-500") == 1, addresses
    assert addresses[0] == caa_release_front(AMPLIFIED), "the release still comes first"
