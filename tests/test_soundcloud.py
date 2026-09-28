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
from noaap.soundcloud import SoundCloud, is_address, one_ref, owner_url, track_url
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
    assert got.title == "Carmina Gloria (symphonic crusader power metal)"
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
