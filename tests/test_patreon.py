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
from noaap.patreon import (
    NO_RENDITION,
    NOT_IN_TIER,
    PROTECTED,
    VIDEO_ONLY,
    Patreon,
    campaign_of,
    is_address,
    media_id,
    one_ref,
    post_id,
    ref_for,
)
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
    """A client whose one connection to the internet is replaced by the fixtures.

    It is built the way the adapter builds it — with the other providers handed in — because that is
    now where they come from: a provider does not construct its neighbours (R-241, ruling 1).
    """
    pt = PatreonSource(with_session()).pt
    by_url = {
        "https://www.patreon.com/posts/100001": recorded("post_one_audio"),
        "https://www.patreon.com/posts/100002": recorded("post_three_attachments"),
        "https://www.patreon.com/posts/100003": recorded("post_locked"),
        "https://www.patreon.com/posts/100004": recorded("post_video"),
        "https://www.patreon.com/posts/100005": recorded("post_embed_youtube"),
        "https://www.patreon.com/posts/100006": recorded("post_embed_unknown"),
        "https://www.patreon.com/posts/100007": recorded("post_video_drm"),
        "https://www.patreon.com/posts/100008": recorded("post_video_silent"),
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

    assert got == frozenset({sources.LISTING, sources.CHANGES, sources.CLEAN, sources.PRIVATE})
    assert sources.SEARCH not in got, "Patreon has no public search of posts by name"
    assert sources.DETAILS not in got, "a track count needs the post read in full"
    assert sources.private("patreon"), "and what it hands over is nobody else's (§9, slice 72)"


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
    """Named rather than guessed at: there is no provider for it, so there is no track.

    And **not turned into a Patreon ref either**: an embed nobody claimed once fell through and became
    `patreon:media:dQw4w9WgXcQ`, a ref this provider cannot read back and `audio` could never fetch.
    A media id is digits; anything else is not ours.
    """
    with pytest.raises(sources.NoAudio):
        client.fetch("https://www.patreon.com/posts/100006")


def test_a_media_id_that_is_not_patreons_shape_is_not_a_track(client):
    from noaap.patreon import media_id

    entry = client._entry({"id": "dQw4w9WgXcQ", "ext": "mp3", "acodec": "mp3", "vcodec": "none"},
                          1, {"title": "a post"})

    assert entry is None
    assert media_id("patreon:media:dQw4w9WgXcQ") is None, "and it could never have been read back"


def test_handling_an_embed_constructs_nothing(monkeypatch):
    """R-241, ruling 1. A provider does not build a configuration, its own or another's — so this holds
    that recognising an embed builds no provider and no `Config` at all."""
    from noaap import config as config_mod
    from noaap.patreon import _other_provider

    class Never:
        name = "never"

        def handles(self, url: str) -> bool:
            return False

    monkeypatch.setattr(config_mod, "Config", _refuse("a Config"))
    monkeypatch.setattr(sources, "get", _refuse("a provider"))

    assert _other_provider({"url": "https://example.invalid/x"}, [Never()]) is None


def _refuse(what: str):
    def no(*args: Any, **kwargs: Any):
        raise AssertionError(f"built {what}, which this provider may not do")
    return no


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
    ("Unable to download JSON metadata: HTTP Error 403: Forbidden", sources.Blocked, "sign in again"),
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
    # the invented creator is `acreator`, and a real listing gives its posts under the vanity
    # (`/acreator/posts/<slug>-<id>`) as well as bare (`/posts/<id>`) — both are allowed, any
    # other patreon.com page is somebody real (§9, slice 71)
    real_host = re.compile(r"https?://(?:www\.)?patreon\.com/"
                           r"(?!posts/\d|c/acreator|m/\d|acreator(?:/|\"|$))", re.I)

    for path in sorted(FIXTURES.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        assert not banned.search(text), f"{path.name} mentions something that could be a session"
        assert "@" not in text, f"{path.name} carries what could be an address"
        assert not real_host.search(text), f"{path.name} names a Patreon page that is not invented"
        # every media address here is either the invented usercontent path, a host in the reserved
        # `.invalid` domain (RFC 2606: it can never resolve, which is the property this wants), or a
        # public embed. A listing has no media address at all, hence the file-name case.
        assert "patreonusercontent.com/invented/" in text or ".invalid" in text or \
            "youtube.com" in text or "campaign" in path.name, \
            f"{path.name} has no invented marker"


# -- what a campaign publishes (R-239, ruling 5) ----------------------------------------------------


@pytest.fixture
def campaigns(monkeypatch) -> Patreon:
    """A client whose campaign reads are the two recorded listings."""
    pt = Patreon(with_session())
    feeds = {
        "https://www.patreon.com/c/acreator/posts": recorded("campaign_own_posts"),
        "https://www.patreon.com/m/70001/posts": recorded("campaign_with_a_foreign_post"),
    }

    def read(url: str, **extra: Any) -> dict[str, Any]:
        read.asked.append((url, extra))
        try:
            return feeds[url]
        except KeyError:
            raise sources.SourceError(f"nothing recorded for {url}") from None

    read.asked = []
    monkeypatch.setattr(pt, "_read", read)
    return pt


def test_a_campaigns_posts_are_listed_as_posts(campaigns):
    refs = campaigns.list_owner("https://www.patreon.com/acreator")

    assert [(r.source_id, r.title, r.tab) for r in refs] == [
        ("https://www.patreon.com/posts/100002", "Three takes of Winter Light", "posts"),
        ("https://www.patreon.com/posts/100001", "Winter Light (studio)", "posts")]
    assert all(r.artist == "A Creator" for r in refs)


def test_a_post_of_another_campaign_is_dropped(campaigns):
    """yt-dlp [#10013](https://github.com/yt-dlp/yt-dlp/issues/10013): asking for one campaign's posts
    returned **every membership the account had**. The extractor filters now and looks right; this
    checks anyway, because the cost of checking is one comparison and the cost of trusting it is
    somebody's whole membership on their disk."""
    refs = campaigns.list_owner("https://www.patreon.com/m/70001/posts")

    assert [r.source_id for r in refs] == ["https://www.patreon.com/posts/100000"]
    assert all("200000" not in r.source_id for r in refs), "the other campaign's post is not ours"


def test_the_cap_is_asked_for_and_not_applied_afterwards(campaigns):
    """Reading two thousand posts and throwing away 1800 is not restraint. `playlistend` stops yt-dlp."""
    campaigns.cfg.patreon_post_cap = 25

    campaigns.list_owner("https://www.patreon.com/acreator")

    url, extra = campaigns._read.asked[-1]
    assert extra["playlistend"] == 25 and extra["extract_flat"] is True


def test_the_cap_stops_the_taking_too():
    """And the accounting stops at it as well, so a listing cannot grow past what was asked for."""
    from noaap.patreon import _Listing

    page = {"id": "70001", "entries": [
        {"id": str(100 + n), "channel_id": "70001", "url": f"https://www.patreon.com/posts/{100 + n}",
         "title": f"post {n}"} for n in range(10)]}
    taking = _Listing("70001", cap=3)

    assert taking.take(page) is False
    assert len(taking.refs) == 3 and taking.capped is True


def test_a_campaign_that_is_not_one_is_refused(campaigns):
    with pytest.raises(sources.SourceError):
        campaigns.list_owner("https://www.patreon.com/posts/100001")


# -- and whether anything changed -------------------------------------------------------------------


def test_what_update_compares_for_a_campaign(campaigns):
    got = campaigns.source_state("https://www.patreon.com/acreator")

    assert got == {"ids": ["100002", "100001"], "modified": "20250916"}


def test_what_update_compares_for_a_post(client):
    got = client.source_state("https://www.patreon.com/posts/100002")

    assert got["ids"] == ["900011", "900012", "900013"]
    assert got["modified"] == "20250916"


def test_a_campaign_that_cannot_be_read_says_nothing_rather_than_guessing(campaigns):
    assert campaigns.source_state("https://www.patreon.com/nobody") is None


# -- its titles, and the artist it will not invent (R-238 e) ----------------------------------------


@pytest.mark.parametrize("title,creator,expected", [
    # yt-dlp's own first Patreon test case: the number is the *post's*, not a track's
    ("Episode 166: David Smalley of Dogma Debate", "Cognitive Dissonance Podcast",
     (None, "David Smalley of Dogma Debate")),
    ("[Patron-only] Spring Demo", "A Creator", (None, "Spring Demo")),
    ("Winter Light (early access)", "A Creator", (None, "Winter Light")),
    ("winter-light-take-1.mp3", "A Creator", (None, "winter-light-take-1")),
    # a deliberate dash is a separator; a hyphen in a sentence is not
    ("Another Band — Winter Light", "A Creator", ("Another Band", "Winter Light")),
    ("Winter Light - studio", "A Creator", (None, "Winter Light - studio")),
    # and the creator's own name on the left is not an artist credit, it is how they write
    ("A Creator — Winter Light", "A Creator", (None, "A Creator — Winter Light")),
])
def test_what_a_post_title_is_read_as(title, creator, expected):
    from noaap.titles_patreon import clean_title

    assert clean_title(title, creator) == expected


@pytest.mark.parametrize("creator,artist", [
    ("A Creator", "A Creator"),
    ("Wind Rose", "Wind Rose"),
    ("Cognitive Dissonance Podcast", None),
    ("A Creator's Music Corner", None),
    ("Some Records", None),
    ("", None),
])
def test_when_a_creators_name_stands_for_an_artist(creator, artist):
    """Narrow on purpose: a campaign is a person's page, not a release's credit."""
    from noaap.titles_patreon import creator_is_artist

    assert creator_is_artist(creator) == artist


def test_the_provider_asks_its_own_title_rules(client):
    got = PatreonSource(with_session()).clean_entry(
        client.fetch("https://www.patreon.com/posts/100001").entries[0])

    assert got == (None, "winter-light")


# -- a refusal is an answer, not a stack trace (§9, slice 71, P58 live run) --------------------------


def test_a_provider_refusal_is_one_sentence_and_an_exit_code(monkeypatch, capsys, tmp_path):
    """The first live fetch of a real Patreon post ended in
    `noaap.sources.NoAudio: this post holds video, not audio` — a traceback, because the CLI caught
    only `NotSupported`. Every failure a provider may raise is an answer somebody asked for."""
    from noaap import cli, sources

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    for failure, expected in ((sources.NoAudio("this post holds video, not audio"), 1),
                              (sources.Blocked("Patreon is refusing requests for now"), 1),
                              (sources.SourceError("could not read that post"), 1),
                              (sources.Cancelled("stopped"), 130)):
        def boom(*a, _f=failure, **k):
            raise _f

        monkeypatch.setattr(cli, "_fetch", boom)
        code = cli.main(["fetch", "--dry-run", "https://www.patreon.com/posts/1"])
        said = capsys.readouterr().err

        assert code == expected, f"{failure!r} exited {code}"
        assert "Traceback" not in said
        assert (str(failure) if expected == 1 else "stopped") in said


# -- what a live campaign listing actually returns (§9, slice 71, P58) ------------------------------


def test_a_flat_listing_carries_only_a_url(monkeypatch):
    """The measured answer, and it was not what the written fixtures said. A flat read of a real
    campaign returns entries of exactly `{"_type": "url", "ie_key": "Patreon", "url": …}` — no id, no
    title, no date, no campaign id. So the post id comes out of the address, the title out of the
    creator's own slug, and the picker that printed five bare URLs has words in it again."""
    pt = Patreon(with_session())
    monkeypatch.setattr(pt, "_read", lambda url, **extra: recorded("campaign_flat_bare"))

    refs = pt.list_owner("https://www.patreon.com/cw/acreator")

    assert [r.source_id for r in refs] == ["https://www.patreon.com/posts/100002",
                                           "https://www.patreon.com/posts/100001",
                                           "https://www.patreon.com/posts/100000"]
    assert [r.title for r in refs] == ["three-takes-of-winter-light", "winter-light-studio",
                                       "https://www.patreon.com/posts/100000"]
    assert all(r.tab == "posts" and r.artist == "acreator" for r in refs)


def test_a_video_post_is_refused_in_the_shape_patreon_really_sends(client):
    """Patreon's own video is a Mux HLS manifest: four `avc1`+`mp4a` formats, no audio-only format,
    no duration, no filesize, and an `id` that is the **post's** id. Every post of the campaign the
    live run was pointed at had exactly this shape, so this is what the refusal has to recognise."""
    with pytest.raises(sources.NoAudio) as refusal:
        client.fetch("https://www.patreon.com/posts/100004")

    assert str(refusal.value) == VIDEO_ONLY


# -- the audio inside a video post (§9, slice 72, R-249) --------------------------------------------


def taking_video(**extra: Any) -> Config:
    """A configuration whose owner has turned the setting on."""
    return with_session(patreon_audio_from_video=True, **extra)


def reading(cfg: Config, monkeypatch, **by_url: dict[str, Any]) -> Patreon:
    pt = PatreonSource(cfg).pt
    urls = {f"https://www.patreon.com/posts/{k.lstrip('p')}": v for k, v in by_url.items()}
    monkeypatch.setattr(pt, "_read", lambda url, **extra: urls[url])
    return pt


def test_off_a_video_post_is_refused_and_the_sentence_names_the_setting(client):
    """Off is the default and stays it: nothing about a creator's video is taken without being asked."""
    with pytest.raises(sources.NoAudio) as refusal:
        client.fetch("https://www.patreon.com/posts/100004")

    assert "patreon_audio_from_video" in str(refusal.value)
    assert not client.cfg.patreon_audio_from_video


def test_on_a_video_post_is_one_track_whose_copy_says_where_it_came_from(monkeypatch):
    pt = reading(taking_video(), monkeypatch, p100004=recorded("post_video"))

    collection = pt.fetch("https://www.patreon.com/posts/100004")

    assert len(collection.entries) == 1
    track = collection.entries[0]
    assert track.title == "Studio video"
    assert track.video_id == "patreon:video:100004", "the ref says which of the two shapes it is"
    assert track.ext == "m4a", "an AAC stream copied out of an mp4 lands in an m4a"
    copy = track.copies[0]
    assert copy.provider == "patreon" and copy.from_video is True
    assert "copied out of this post's video" in copy.why
    # and the post is the collection, exactly as before: no number, no year, no series
    assert collection.is_playlist and not PatreonSource(taking_video()).is_release(collection)


def test_the_rendition_is_chosen_by_its_audio_and_then_by_the_smallest_picture(monkeypatch):
    """Patreon's ladder carries the same AAC in every rung, so the 270p rung is the whole saving —
    measured live: 21.9 MB of 270p video for the same audio a 1080p rung would have cost 139 MB."""
    from noaap.patreon import rendition

    ladder = {"formats": [
        {"format_id": "1865", "acodec": "mp4a.40.2", "vcodec": "avc1", "tbr": 1865.6, "height": 1080},
        {"format_id": "294", "acodec": "mp4a.40.2", "vcodec": "avc1", "tbr": 294.8, "height": 270},
        {"format_id": "972", "acodec": "mp4a.40.2", "vcodec": "avc1", "tbr": 972.4, "height": 720}]}
    assert rendition(ladder)["format_id"] == "294"

    # better audio wins even when it comes with more picture — the choice is never about the picture
    mixed = {"formats": [
        {"format_id": "small", "acodec": "mp4a.40.2", "abr": 64, "vcodec": "avc1", "tbr": 200, "height": 270},
        {"format_id": "good", "acodec": "mp4a.40.2", "abr": 128, "vcodec": "avc1", "tbr": 1800, "height": 1080}]}
    assert rendition(mixed)["format_id"] == "good"

    # and an audio-only format has no picture at all, so it wins on its own terms
    both = {"formats": [
        {"format_id": "audio", "acodec": "mp4a.40.2", "abr": 128, "vcodec": "none"},
        {"format_id": "video", "acodec": "mp4a.40.2", "abr": 128, "vcodec": "avc1", "tbr": 1800}]}
    assert rendition(both)["format_id"] == "audio"


def test_a_protected_post_is_refused_by_name_and_nothing_is_attempted(monkeypatch, tmp_path):
    """`has_drm` on the post or on a rendition. Nothing is downloaded, nothing is opened, nothing is
    tried once to see (R-249, item 3)."""
    pt = reading(taking_video(), monkeypatch, p100007=recorded("post_video_drm"))
    monkeypatch.setattr(pt, "_options", lambda **extra: pytest.fail("a protected post was touched"))

    with pytest.raises(sources.NoAudio) as refusal:
        pt.fetch("https://www.patreon.com/posts/100007")
    assert str(refusal.value) == PROTECTED

    with pytest.raises(sources.NoAudio) as again:
        pt.download_audio("patreon:video:100007", tmp_path)
    assert str(again.value) == PROTECTED


def test_a_video_with_no_audio_stream_says_so(monkeypatch):
    pt = reading(taking_video(), monkeypatch, p100008=recorded("post_video_silent"))

    with pytest.raises(sources.NoAudio) as refusal:
        pt.fetch("https://www.patreon.com/posts/100008")

    assert str(refusal.value) == NO_RENDITION


def test_the_video_is_gone_when_the_track_is_done(monkeypatch, tmp_path):
    """The picture is paid for once and never kept: not in the library, not in a temp directory, not
    after a failure. The scratch directory is remembered here and asserted gone afterwards."""
    from noaap import patreon as mod

    pt = reading(taking_video(), monkeypatch, p100004=recorded("post_video"))
    seen: dict[str, Path] = {}

    class FakeYDL:
        def __init__(self, options): self.options = options
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def download(self, urls):
            out = Path(self.options["outtmpl"]["default"] if isinstance(self.options["outtmpl"], dict)
                       else self.options["outtmpl"])
            seen["scratch"] = out.parent
            (out.parent / "100004.mp4").write_bytes(b"a pretend rendition")

    monkeypatch.setattr(mod, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(mod, "_copy_audio",
                        lambda video, out: out.write_bytes(b"the audio, copied"))

    got = pt.download_audio("patreon:video:100004", tmp_path / "parts")

    assert got == tmp_path / "parts" / "100004.m4a" and got.read_bytes() == b"the audio, copied"
    assert not seen["scratch"].exists(), "the scratch directory is gone"
    assert [f.name for f in (tmp_path / "parts").iterdir()] == ["100004.m4a"], "no video in the library"


def test_the_video_is_gone_when_the_copy_fails(monkeypatch, tmp_path):
    from noaap import patreon as mod

    pt = reading(taking_video(), monkeypatch, p100004=recorded("post_video"))
    seen: dict[str, Path] = {}

    class FakeYDL:
        def __init__(self, options): self.options = options
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def download(self, urls):
            out = Path(self.options["outtmpl"]["default"] if isinstance(self.options["outtmpl"], dict)
                       else self.options["outtmpl"])
            seen["scratch"] = out.parent
            (out.parent / "100004.mp4").write_bytes(b"a pretend rendition")

    def refuses(video, out):
        raise sources.SourceError("could not copy the audio out of the video: ffmpeg refused")

    monkeypatch.setattr(mod, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(mod, "_copy_audio", refuses)

    with pytest.raises(sources.SourceError):
        pt.download_audio("patreon:video:100004", tmp_path / "parts")

    assert not seen["scratch"].exists(), "a failure leaves no video behind either"
    assert list((tmp_path / "parts").iterdir()) == []


def test_a_copy_is_a_copy_and_never_an_encode():
    """The one thing that must not drift: `-c:a copy`, and no encoder named anywhere near it."""
    from noaap import patreon as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    line = next(one for one in source.splitlines() if '"-c:a", "copy"' in one)
    assert '"-vn"' in source and "0:a:0" in source
    assert "-b:a" not in source and "libmp3lame" not in source and "-acodec" not in line


def test_the_post_of_a_video_ref_is_a_link_a_person_can_open():
    """A media ref carries no post and gets no link; this one carries its post, so it gets the post."""
    source = PatreonSource(taking_video())

    assert source.url_for("patreon:video:100004") == "https://www.patreon.com/posts/100004"
    assert source.url_for("patreon:media:900041") is None
    assert source.one_ref("patreon:video:100004") == "patreon:video:100004"


def test_the_refusal_tells_somebody_what_to_do_rather_than_what_it_guesses(client, monkeypatch):
    """It used to say *open patreon.com in the browser*, because a thirty-minute bot cookie was the
    only thing that made a read work. That turned out to be the handshake (§9, slice 80), so the
    remaining reason for a 403 is a login that has ended — and that is what it says."""
    from noaap.patreon import LAPSED

    with pytest.raises(sources.Blocked) as refused:
        client._failure(Exception("ERROR: unable to download webpage: HTTP Error 403: Forbidden"),
                        "https://www.patreon.com/posts/100001")

    assert str(refused.value) == LAPSED
    assert "sign in again" in LAPSED and "browser noaap reads" in LAPSED
    assert "bot check" not in LAPSED, "that is not what a 403 means any more"


# -- the cover, and what a download cost (§9, slice 73, R-254) --------------------------------------


def test_an_image_address_is_fetched_as_an_image_and_not_read_as_a_post(monkeypatch):
    """The live run said *could not fetch any cover* for an address whose signature was good for
    another two weeks. The address came from the post read seconds earlier and was handed back to the
    provider, which — finding it was neither a post nor a campaign address — asked yt-dlp to extract
    it *as a page*. A JPEG is not a page. It is fetched now, with the session and a referer."""
    from noaap import patreon as mod

    pt = Patreon(with_session())
    monkeypatch.setattr(pt, "_read", lambda *a, **k: pytest.fail("an image address was read as a post"))
    asked: dict[str, Any] = {}

    class FakeYDL:
        def __init__(self, options): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def urlopen(self, request):
            asked["url"], asked["headers"] = request.url, dict(request.headers)
            return type("R", (), {"read": staticmethod(lambda: b"\xff\xd8\xff the bytes")})()

    monkeypatch.setattr(mod, "YoutubeDL", FakeYDL)

    data = pt.fetch_bytes("https://c10.patreonusercontent.com/4/x/1.jpeg?token-hash=abc&token-time=1")

    assert data == b"\xff\xd8\xff the bytes"
    assert asked["url"].endswith("token-time=1"), "fetched exactly the address it was given"
    assert any(k.lower() == "referer" for k in asked["headers"]), "with the referer its host expects"


def test_a_post_address_still_goes_through_the_post(client):
    """The other two cases are unchanged: a post address is read as a post, and its image taken."""
    from noaap import patreon as mod

    seen: dict[str, str] = {}

    class FakeYDL:
        def __init__(self, options): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def urlopen(self, request):
            seen["url"] = request.url
            return type("R", (), {"read": staticmethod(lambda: b"\xff\xd8\xff")})()

    monkeypatch_target = mod.YoutubeDL
    mod.YoutubeDL = FakeYDL
    try:
        client.fetch_bytes("https://www.patreon.com/posts/100004")
    finally:
        mod.YoutubeDL = monkeypatch_target

    assert "invented/post.jpg" in seen["url"], "the post's own image, from the post"


def test_a_download_that_throws_most_of_itself_away_says_so(monkeypatch, tmp_path):
    """The first live fetch was asked for bytes downloaded beside bytes kept and could not answer:
    both numbers existed, in a log that was not turned on. Now the provider keeps them and the run
    says the line."""
    from noaap import download as dl
    from noaap import patreon as mod

    pt = reading(taking_video(), monkeypatch, p100004=recorded("post_video"))

    class FakeYDL:
        def __init__(self, options): self.options = options
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def download(self, urls):
            out = Path(self.options["outtmpl"]["default"] if isinstance(self.options["outtmpl"], dict)
                       else self.options["outtmpl"])
            (out.parent / "100004.mp4").write_bytes(b"x" * 52_000_000)

    monkeypatch.setattr(mod, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(mod, "_copy_audio", lambda video, out: out.write_bytes(b"a" * 17_000_000))

    assert pt.last_transfer is None, "nothing is claimed before a download"
    pt.download_audio("patreon:video:100004", tmp_path / "parts")

    assert pt.last_transfer == {"downloaded": 52_000_000, "kept": 17_000_000, "thrown_away": "video"}
    line = dl.transfer_line(PatreonSource(taking_video()))  # nothing downloaded yet: nothing said
    assert line is None

    class Moved:
        def __init__(self):
            self.last_transfer = {"downloaded": 52_000_000, "kept": 17_000_000, "thrown_away": "video"}

    said = dl.transfer_line(Moved())
    assert "52.0 MB fetched" in said and "17.0 MB kept" in said and "video was thrown away" in said

    # a provider that keeps no such number, and one where nothing was thrown away, say nothing
    assert dl.transfer_line(object()) is None
    assert dl.transfer_line(type("S", (), {"last_transfer": {"downloaded": 5, "kept": 5}})()) is None


# -- the handshake, which is what was being refused (§9, slice 80, R-277) ---------------------------


def test_this_provider_does_not_let_yt_dlp_pin_a_cipher_list():
    """**Measured, not guessed**: same cookies, same query, same headers, 37 seconds apart — Python's
    default ciphers answered `200`, and the cipher string yt-dlp sets answered `403` in 0.1 s. The
    option takes the other branch of yt-dlp's own `make_ssl_context`, which uses OpenSSL's `DEFAULT`
    list. It is *less* shaping of the connection, not more: nothing is made to look like a browser."""
    from noaap.patreon import PLAIN_TLS

    options = Patreon(with_session())._options()

    assert options["legacyserverconnect"] is True
    assert PLAIN_TLS == {"legacyserverconnect": True}


def test_and_no_other_provider_is_changed_by_it():
    """It is this provider's own answer to this provider's own refusal. The shared client, and every
    other source built on it, keeps yt-dlp's defaults."""
    from noaap import ytdlp

    assert "legacyserverconnect" not in ytdlp.params()
    assert "legacyserverconnect" not in ytdlp.params(cookies_from_browser="firefox")


def test_the_refusal_no_longer_sends_anybody_to_refresh_a_cookie():
    """The advice was to open the browser, because a thirty-minute bot cookie was the only thing that
    made a read work. With the handshake accepted, what is left to say is: sign in."""
    from noaap.patreon import LAPSED

    assert "sign in again" in LAPSED
    assert "bot check" not in LAPSED and "renews" not in LAPSED
