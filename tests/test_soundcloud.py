"""SoundCloud as a source (DESIGN §9, slice 57).

Driven from **recorded pages**, not from the network: `tests/fixtures/soundcloud/` holds the two
reads the provider makes of one real public set, thinned to the fields it uses. No audio is in this
repository and no case here opens a socket.

What the provider is for is worth repeating where the cases are: **music that is not on YouTube.**
Without an account SoundCloud gives 160 kbps AAC at best, and label uploads are DRM protected and
give nothing at all — measured on real pages (I-141). Nothing here touches DRM.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noaap import sources
from noaap.config import Config
from noaap.soundcloud import SoundCloud, art_candidates, is_address, one_ref, owner_url, track_url
from noaap.sources_soundcloud import SoundCloudSource
from noaap.titles_soundcloud import owner_is_artist, parse_track_title

FIXTURES = Path(__file__).parent / "fixtures" / "soundcloud"
SET_URL = "https://soundcloud.com/anttimartikainen/sets/carmina-gloria"


def recorded(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def client(monkeypatch) -> SoundCloud:
    """A client whose one connection to the internet is replaced by the recordings."""
    sc = SoundCloud(Config())
    pages = {(SET_URL, True): recorded("set_flat"), (SET_URL, False): recorded("set_full")}

    def read(url: str, **extra: Any) -> dict[str, Any]:
        try:
            return pages[(url, bool(extra.get("extract_flat")))]
        except KeyError:
            raise sources.SourceError(f"nothing recorded for {url}") from None

    monkeypatch.setattr(sc, "_read", read)
    return sc


# -- what it recognises, without a request ----------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("https://soundcloud.com/a/sets/b", True),
    ("https://m.soundcloud.com/a/b", True),
    ("https://api.soundcloud.com/tracks/123", True),
    ("https://www.youtube.com/watch?v=aaaaaaaaaaa", False),
    ("Feuerschwanz", False),
])
def test_it_knows_its_own_addresses(text, expected):
    assert is_address(text) is expected


def test_an_owner_is_a_page_and_a_set_is_not():
    assert owner_url("https://soundcloud.com/anttimartikainen") == "https://soundcloud.com/anttimartikainen"
    assert owner_url(SET_URL) is None, "a set is not an owner"
    assert owner_url("https://soundcloud.com/a/b") is None, "and neither is a track"


def test_a_ref_is_the_numeric_id():
    """A permalink can be renamed by its uploader, and an identity that moves loses the library's
    grip on what it already has."""
    assert one_ref("https://api.soundcloud.com/tracks/soundcloud%3Atracks%3A865897327") == "865897327"
    assert one_ref("865897327") == "865897327"
    assert one_ref(SET_URL) is None, "a set is not one track"
    assert track_url("865897327").endswith("865897327")


# -- a set, read twice ------------------------------------------------------------------------------


def test_a_set_becomes_a_collection(client):
    got = client.fetch(SET_URL)

    assert got.is_playlist and got.source_id == "1100349598"
    # the set's name is cleaned here, where SoundCloud's conventions live: the core's album-name
    # hygiene is YouTube's, and a set is titled for SoundCloud's search box
    assert got.title == "Carmina Gloria"
    assert got.channel == "Antti Martikainen"
    assert len(got.entries) == 11
    assert [e.position for e in got.entries] == list(range(1, 12))
    assert got.modified == "20211104", "the set's own last change, for the cheap check"


def test_the_album_and_the_year_come_from_the_set(client):
    """A SoundCloud **track** carries no album, no number and no year — measured on real pages.
    The set is the only place they exist, so that is where they come from."""
    got = client.fetch(SET_URL)

    assert all(e.music.album == got.title for e in got.entries)
    assert all(e.music.year == 2021 for e in got.entries)
    assert all(e.music.artist is None for e in got.entries), \
        "the artist may be in the title; reading that is clean_entry's job, as it is for YouTube"


def test_a_track_that_cannot_be_read_is_skipped_by_name_not_lost(client, monkeypatch):
    """Why the set is read twice. The flat list keeps the id and the place, so one DRM'd or removed
    track costs its own entry and not the album — and never vanishes silently."""
    full = recorded("set_full")
    full["entries"] = [e for e in full["entries"] if e["id"] != "1153662145"]
    monkeypatch.setattr(client, "_read",
                        lambda url, **kw: recorded("set_flat") if kw.get("extract_flat") else full)

    got = client.fetch(SET_URL)

    assert len(got.entries) == 11, "eleven went in and eleven came out"
    missing = next(e for e in got.entries if e.video_id == "1153662145")
    assert missing.position == 2 and missing.skipped == "could not be read"


def test_the_cheap_check_is_one_flat_read(client):
    state = client.source_state(SET_URL)

    assert state["modified"] == "20211104"
    assert len(state["ids"]) == 11 and state["ids"][0] == "865897327"


# -- how its titles read ------------------------------------------------------------------------------


@pytest.mark.parametrize("title,artist,expected", [
    ("Divine Alliance (epic heroic power metal)", None, "Divine Alliance"),
    ("Carmina Gloria (symphonic crusader power metal)", None, "Carmina Gloria"),
    ("Bloodywood - Gaddaar (Indian Folk Metal)", "Bloodywood", "Gaddaar"),
    ("Auf Wiederseh'n (snip)", None, "Auf Wiederseh'n"),
    ("Taivaantuli (Nordic folk metal)", None, "Taivaantuli"),
])
def test_the_conventions_it_actually_has(title, artist, expected):
    """Every one of these is a real title from a real page, and they are the whole ruleset."""
    assert parse_track_title(title) == (artist, expected)


@pytest.mark.parametrize("title", [
    "Kalevala (Live)", "Kalevala (Acoustic)", "Kalevala (Remix)", "Kalevala (feat. Somebody)",
])
def test_a_parenthesis_that_belongs_to_the_song_is_kept(title):
    """The mistake that would otherwise merge two different recordings."""
    assert parse_track_title(title)[1] == title


def test_the_uploader_is_not_returned_as_the_artist():
    """`owner_artist` is where an uploader becomes an artist. Answering it here as well would make
    an upload by a curator look as if the song itself said so."""
    assert parse_track_title("Some Song", "A Curator") == (None, "Some Song")


@pytest.mark.parametrize("owner,expected", [
    ("Antti Martikainen", "Antti Martikainen"),
    ("Feuerschwanz", "Feuerschwanz"),
    ("Ebunny. Music for your projects.", None),
    ("", None),
])
def test_an_uploader_is_usually_the_artist_but_a_shop_sign_is_not(owner, expected):
    assert owner_is_artist(owner) == expected


# -- the covers ----------------------------------------------------------------------------------------


def test_the_original_artwork_is_tried_first():
    sized = "https://i1.sndcdn.com/artworks-HjinOk0jsPMnHs0q-GodHHQ-t500x500.jpg"
    assert art_candidates(sized)[0].endswith("-original.jpg")
    assert art_candidates(sized)[1] == sized
    assert art_candidates("https://example.invalid/x.jpg") == ["https://example.invalid/x.jpg"]


# -- and what the provider declares ----------------------------------------------------------------------


def test_it_declares_listing_and_not_search():
    """Its own search finds tracks, never sets, so there is no honest way to answer "which albums
    is this artist's". A provider does not declare what it cannot do (R-183, ruling a)."""
    caps = SoundCloudSource(Config()).capabilities()

    assert sources.LISTING in caps and sources.CHANGES in caps and sources.CLEAN in caps
    assert sources.SEARCH not in caps
    assert not hasattr(SoundCloudSource(Config()), "find"), "and there is no find that raises"


def test_it_is_in_the_registry_under_its_own_name():
    assert "soundcloud" in sources.known()
    assert sources.get("soundcloud", Config()).name == "soundcloud"


def test_a_ref_has_no_page_and_says_so():
    """The ref is a numeric id; no address can be built from it that a person could open. The
    protocol would rather have nothing than a link that 404s."""
    assert SoundCloudSource(Config()).url_for("865897327") is None


def test_a_provider_that_cannot_search_by_name_says_who_can(tmp_path):
    """Ruling a's other half: a refusal that names the providers that *can* is the useful half."""
    from noaap.service import Service

    service = Service(Config(musicbrainz=False, lyrics=False), tmp_path,
                      yt=SoundCloudSource(Config()), log=lambda s: None)

    with pytest.raises(sources.NotSupported, match="cannot search by name"):
        service.search("Antti Martikainen")
    try:
        service.search("Antti Martikainen")
    except sources.NotSupported as e:
        assert "youtube" in str(e), "and it says who can"


def test_listing_is_asked_of_a_provider_that_has_it(tmp_path):
    """A folder publishes nothing an owner could list, and says so rather than raising from inside."""
    from noaap.service import Service
    from noaap.sources_folder import FolderSource

    service = Service(Config(musicbrainz=False, lyrics=False), tmp_path,
                      yt=FolderSource(Config()), log=lambda s: None)

    with pytest.raises(sources.NotSupported, match="cannot list what an owner publishes"):
        service.channel(str(tmp_path))


# -- what goes wrong, and which of the four kinds it is ------------------------------------------------


@pytest.mark.parametrize("said,kind,says", [
    ("[soundcloud] 1470964783: This video is DRM protected", sources.NoAudio, "DRM"),
    ("ERROR: [soundcloud] 42: HTTP Error 429: Too Many Requests", sources.Blocked, "refusing requests"),
    ("[soundcloud] 42: rate limit exceeded, try again later", sources.Blocked, "refusing requests"),
    ("[soundcloud] 42: This track is not available in your country", sources.NoAudio, "not available"),
    ("[soundcloud] 42: Unable to download JSON metadata: HTTP Error 404: Not Found", sources.SourceError, "404"),
])
def test_yt_dlps_complaint_becomes_one_of_the_four_kinds(said, kind, says):
    """The boundary's whole job: yt-dlp's vocabulary stops here (§9, slice 51)."""
    from yt_dlp.utils import DownloadError

    got = SoundCloud(Config())._failure(DownloadError(said))

    assert isinstance(got, kind), f"{said!r} became {type(got).__name__}"
    assert says in str(got)


def test_a_rate_limit_is_blocked_so_a_run_stops_instead_of_asking_again():
    """The same shape as YouTube's bot check. `update` reads `Blocked` and stops the whole pass —
    a library of 300 albums hammering a site that has just said no is how an address gets banned."""
    from yt_dlp.utils import DownloadError

    assert isinstance(SoundCloud(Config())._failure(DownloadError("HTTP Error 429")), sources.Blocked)


def test_drm_is_a_fact_about_the_track_not_an_error_of_ours():
    """Every label upload probed for this package is DRM protected. It costs that track and
    nothing else, and it is never worked around."""
    from yt_dlp.utils import DownloadError

    got = SoundCloud(Config())._failure(DownloadError("[soundcloud] 1: This video is DRM protected"))

    assert isinstance(got, sources.NoAudio)
    assert "DRM" in str(got) and "no stream" in str(got)


def test_audio_choice_is_the_aac_or_the_one_stream_that_needs_no_assembly():
    """R-183, ruling b. `best` is the 160 kbps AAC; the other value is the progressive MP3."""
    from noaap.soundcloud import FORMATS

    assert FORMATS["best"].startswith("bestaudio")
    assert "acodec=mp3" in FORMATS["combined"] and "protocol^=http" in FORMATS["combined"]
    assert FORMATS.get("nonsense") is None, "an unknown choice falls back in the caller, not here"


def test_a_download_that_leaves_no_file_is_no_audio(tmp_path, monkeypatch):
    """A provider that answers "fine" and writes nothing must not leave the pipeline to find out."""
    import noaap.soundcloud as sc

    class Nothing:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False): return {"id": "42"}

    monkeypatch.setattr(sc, "YoutubeDL", lambda params: Nothing())

    with pytest.raises(sources.NoAudio, match="no file arrived"):
        SoundCloud(Config()).download_audio("42", tmp_path)


# -- and the whole of it, from a recorded set to a plan ------------------------------------------------


@pytest.fixture
def provider(monkeypatch, client) -> SoundCloudSource:
    """The adapter over the recorded client, with the albums tab answered from memory."""
    source = SoundCloudSource(Config())
    source.sc = client
    monkeypatch.setattr(client, "released_by", lambda owner: {"1100349598"})
    return source


def test_a_set_on_the_albums_tab_is_a_release(provider):
    """yt-dlp does not surface SoundCloud's own `set_type`, so the tab it is published under is the
    site's own statement about it — the distinction YouTube draws with a Releases tab."""
    from noaap.plan import Kind, classify

    got = provider.collection(SET_URL)

    assert provider.is_release(got) is True
    assert classify(got, provider) is Kind.OFFICIAL_ALBUM


def test_a_set_that_is_not_on_it_is_somebody_s_playlist(provider, monkeypatch):
    from noaap.plan import Kind, classify

    monkeypatch.setattr(provider.sc, "released_by", lambda owner: set())
    got = provider.collection(SET_URL)

    assert provider.is_release(got) is False
    assert classify(got, provider) is not Kind.OFFICIAL_ALBUM


def test_a_recorded_set_becomes_the_plan_it_should(provider):
    """The end of the provider's job, over a real page: the album name and the year off the set,
    the numbers off the running order, the artist off the uploader, the genres out of the titles."""
    from noaap.plan import build_plan

    plan = build_plan(provider.collection(SET_URL), source=provider)

    assert plan.albumartist == "Antti Martikainen"
    assert plan.album == "Carmina Gloria", "the genre the uploader wrote for the search box is gone"
    assert plan.year == 2021
    assert [t.number for t in plan.tracks] == list(range(1, 12))
    assert plan.tracks[0].title == "Divine Alliance"


def test_the_plan_says_where_each_value_came_from(provider):
    """A SoundCloud plan names its own evidence, as a folder's does — a reader should see that the
    album name came off a set and not off YouTube Music (§9, slice 53)."""
    from noaap.plan import build_plan

    plan = build_plan(provider.collection(SET_URL), source=provider)

    assert plan.provenance["album"] == "sc_set"
    assert plan.provenance["year"] == "sc_set"
    assert plan.tracks[0].provenance["title"] == "sc_title"
