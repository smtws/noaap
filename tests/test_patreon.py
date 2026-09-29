"""Patreon as a source (DESIGN §9, slice 70).

Driven from **fixtures written by hand**, in the shape yt-dlp's extractor returns. Nothing here came
from a real account and nothing here opens a socket — and the reason is stronger than the usual one:
a real recording carries a patron's session in its URLs, so there is no safe way to record one.

What this provider is for is worth repeating where the cases are: **music a patron already pays for**.
It is not a source of better copies of anything public and it is not a way around anything. It cannot
work at all without the patron's own session — measured, not assumed: asked anonymously for the public
post in yt-dlp's own test list, Patreon answered `HTTP Error 403: Forbidden`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noaap import sources
from noaap.config import Config
from noaap.patreon import NOT_IN_TIER, Patreon, campaign_of, is_address, media_id, one_ref, post_id, ref_for
from noaap.sources_patreon import PatreonSource

FIXTURES = Path(__file__).parent / "fixtures" / "patreon"
POST = "https://www.patreon.com/posts/100001"


def recorded(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def with_session(**extra: Any) -> Config:
    """A configuration that *has* a session setting — the fixtures stand in for what it would read."""
    cfg = Config()
    cfg.patreon_cookies_from_browser = "firefox"
    for key, value in extra.items():
        setattr(cfg, key, value)
    return cfg


@pytest.fixture
def client(monkeypatch) -> Patreon:
    """A client whose one connection to the internet is replaced by the fixtures."""
    pt = Patreon(with_session())
    by_url = {
        "https://www.patreon.com/posts/100001": recorded("post_one_audio"),
        "https://www.patreon.com/posts/100002": recorded("post_three_attachments"),
        "https://www.patreon.com/posts/100003": recorded("post_locked"),
        "https://www.patreon.com/posts/100004": recorded("post_video"),
        "https://www.patreon.com/posts/100005": recorded("post_embed_youtube"),
        "https://www.patreon.com/posts/100006": recorded("post_embed_unknown"),
    }

    def read(url: str, **extra: Any) -> dict[str, Any]:
        try:
            return by_url[url]
        except KeyError:
            raise sources.SourceError(f"nothing recorded for {url}") from None

    monkeypatch.setattr(pt, "_read", read)
    return pt


# -- what it recognises, without a request ----------------------------------------------------------


@pytest.mark.parametrize("text,post,campaign", [
    ("https://www.patreon.com/posts/scottfalco-146966245", "146966245", None),
    ("https://www.patreon.com/posts/100001", "100001", None),
    ("https://patreon.com/creation?hid=743933", "743933", None),
    ("https://www.patreon.com/c/acreator/posts", None, "https://www.patreon.com/c/acreator/posts"),
    ("https://www.patreon.com/acreator", None, "https://www.patreon.com/c/acreator/posts"),
    ("https://www.patreon.com/m/4767637/posts", None, "https://www.patreon.com/m/4767637/posts"),
    ("https://soundcloud.com/a/b", None, None),
])
def test_which_addresses_are_ours(text, post, campaign):
    assert post_id(text) == post
    assert campaign_of(text) == campaign
    assert is_address(text) is ("patreon.com" in text)


def test_a_post_address_is_not_a_track():
    """A post can hold three files, so a post address is a **collection** and never one ref."""
    assert one_ref(POST) is None
    assert one_ref("patreon:media:900001") == "patreon:media:900001"
    assert media_id(ref_for(900001)) == "900001"


def test_the_provider_declares_only_what_it_can_do():
    got = PatreonSource(Config()).capabilities()

    assert got == frozenset({sources.LISTING, sources.CHANGES, sources.CLEAN})
    assert sources.SEARCH not in got, "Patreon has no public search of posts by name"
    assert sources.DETAILS not in got, "a track count needs the post read in full"


# -- and what it refuses to do at all ---------------------------------------------------------------


def test_without_a_session_every_call_refuses_and_names_the_setting():
    """Measured: even a *public* post answers 403 without a session, and this installation has no
    impersonation and will not get one (R-239, ruling 1). So there is nothing to fall back on."""
    p = PatreonSource(Config())

    for call, args in (("collection", (POST,)), ("probe", ("patreon:media:1",)), ("art", (POST,))):
        with pytest.raises(sources.NotSupported) as raised:
            getattr(p, call)(*args)
        assert "patreon_cookies_from_browser" in str(raised.value)
        assert "patreon_cookies_file" in str(raised.value)


def test_a_patron_session_is_never_another_providers(monkeypatch):
    """SoundCloud's cookies are SoundCloud's. A patron's session is read from this provider's own two
    settings or not at all — nothing is inherited silently (R-238 c)."""
    cfg = Config()
    cfg.soundcloud_cookies_from_browser = "firefox"

    with pytest.raises(sources.NotSupported):
        PatreonSource(cfg).collection(POST)


def test_what_yt_dlp_is_asked_for_carries_this_providers_cookies_and_nothing_else(monkeypatch):
    cfg = with_session(soundcloud_cookies_from_browser="chrome")

    options = Patreon(cfg)._options()

    assert options["cookiesfrombrowser"] == ("firefox", None, None, None)
    assert "cookiefile" not in options


def test_nothing_of_the_session_is_stored():
    """What noaap keeps is the *setting*. Not the cookie, not a copy, not a token."""
    settings = PatreonSource(with_session()).settings()

    assert settings == {"cookies_from_browser": "firefox", "cookies_file": None}
    assert not any("session" in str(v).lower() or "cookie=" in str(v) for v in settings.values())


# -- a post is a collection (R-239, ruling 3) --------------------------------------------------------


def test_one_post_with_one_file_is_one_track(client):
    got = client.fetch("https://www.patreon.com/posts/100001")

    assert got.source_id == POST and got.title == "Winter Light (studio)"
    assert got.channel == "A Creator", "the creator is the owner"
    assert got.is_playlist is True, "nothing on Patreon is a release"
    assert [(e.position, e.title, e.video_id) for e in got.entries] == [
        (1, "winter-light.mp3", "patreon:media:900001")]
    assert got.modified == "20250916", "the post's own date, which is not a release year"


def test_one_post_with_three_files_is_three_tracks_in_the_posts_order(client):
    got = client.fetch("https://www.patreon.com/posts/100002")

    assert [(e.position, e.title) for e in got.entries] == [
        (1, "winter-light-take-1.mp3"), (2, "winter-light-take-2.mp3"),
        (3, "winter-light-instrumental.wav")]
    assert [e.ext for e in got.entries] == ["mp3", "mp3", "wav"]
    assert [e.video_id for e in got.entries] == [
        "patreon:media:900011", "patreon:media:900012", "patreon:media:900013"]


def test_no_number_is_invented_for_them(client):
    """A post title carries an episode number at most, never a track number, and inventing one would
    be worse than leaving it (R-239, ruling 3)."""
    got = client.fetch("https://www.patreon.com/posts/100002")

    assert all(e.music.track is None for e in got.entries)
    assert all(e.music.album is None for e in got.entries)


def test_every_track_says_the_audio_is_patreons(client):
    got = client.fetch("https://www.patreon.com/posts/100001")

    copy = got.entries[0].copies[0]
    assert copy.provider == "patreon" and copy.ref == "patreon:media:900001"
    assert copy.bytes == 5_000_000 and copy.length == 212.0
    assert copy.added_by == "source"


# -- what a post may hold that this provider does not take ------------------------------------------


def test_a_post_the_tier_does_not_include_is_no_audio(client):
    """R-239, ruling 2: the post exists and there is no stream for this listener — the same answer a
    geo-blocked track gets. It is per track and the album goes on."""
    with pytest.raises(sources.NoAudio) as raised:
        client.fetch("https://www.patreon.com/posts/100003")

    assert str(raised.value) == NOT_IN_TIER


def test_a_video_post_is_named_and_not_stripped_to_audio(client):
    with pytest.raises(sources.NoAudio) as raised:
        client.fetch("https://www.patreon.com/posts/100004")

    assert "video, not audio" in str(raised.value)


def test_an_embedded_youtube_video_belongs_to_youtube(client):
    """R-239, ruling 4. A Patreon post is often a link with a note; the audio is that service's, and
    saying so is what makes the track fetchable at all."""
    got = client.fetch("https://www.patreon.com/posts/100005")

    entry = got.entries[0]
    assert entry.video_id == "dQw4w9WgXcQ", "YouTube's own id, not a Patreon ref"
    assert entry.copies[0].provider == "youtube"
    assert "embedded in a Patreon post" in entry.copies[0].why


def test_an_embed_of_something_unknown_is_left_out(client):
    """Named rather than guessed at: there is no provider for it, so there is no track."""
    with pytest.raises(sources.NoAudio):
        client.fetch("https://www.patreon.com/posts/100006")


# -- the failures, onto the four there are (R-239, ruling 2) ----------------------------------------


def test_trouble_nobody_has_seen_before_is_returned_not_guessed_at():
    """The last resort: a message no rule matches becomes a plain `SourceError` carrying what Patreon
    said, because inventing a kind for it would be worse than admitting we do not know."""
    from yt_dlp.utils import DownloadError

    failure = Patreon(with_session())._failure(
        DownloadError("ERROR: [patreon] 1: Something nobody has seen before. And more."), "1")

    assert type(failure) is sources.SourceError
    assert str(failure) == "Something nobody has seen before"


@pytest.mark.parametrize("said,kind,says", [
    ("HTTP Error 429: Too Many Requests", sources.Blocked, "refusing requests"),
    ("You do not have access to this post", sources.NoAudio, NOT_IN_TIER),
    ("Unable to download JSON metadata: HTTP Error 403: Forbidden", sources.Blocked, "gone stale"),
    ("HTTP Error 404: Not Found", sources.NoAudio, "no longer on Patreon"),
])
def test_and_the_decided_ones_are_raised_by_name(said, kind, says):
    from yt_dlp.utils import DownloadError

    with pytest.raises(kind) as raised:
        Patreon(with_session())._failure(DownloadError(f"ERROR: [patreon] 1: {said}"), "1")

    assert says in str(raised.value)


# -- and the guard that keeps this directory safe (R-239, ruling 6) ---------------------------------


def test_no_fixture_carries_a_session():
    """The only thing standing between a live run and this repository. A real recording carries a
    patron's session in its URLs, so **no fixture here may come from one** — and this is what says so.
    """
    import re

    banned = re.compile(r"session|cookie|csrf|bearer|authorization|patreon_device_id"
                        r"|[?&](token|key|sig|signature|policy)=", re.I)
    real_host = re.compile(r"https?://(?:www\.)?patreon\.com/(?!posts/\d|c/acreator|m/\d)", re.I)

    for path in sorted(FIXTURES.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        assert not banned.search(text), f"{path.name} mentions something that could be a session"
        assert "@" not in text, f"{path.name} carries what could be an address"
        assert not real_host.search(text), f"{path.name} names a Patreon page that is not invented"
        assert "patreonusercontent.com/invented/" in text or "patreon.invalid" in text or \
            "youtube.com" in text or "vimeo.invalid" in text or "campaign" in path.name, \
            f"{path.name} has no invented marker"
