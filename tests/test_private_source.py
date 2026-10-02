"""Nothing that came from a private source is ever offered to anyone (DESIGN §9, slice 72).

The user, about the creator whose posts this was built for — audiobooks, whose captions are the whole
book: *"not publishable is the right instinct … i guess the authors wouldnt be thrilled to find those
on musicbrainz"*. So a track whose audio came from Patreon is not published to lrclib, its album is
not offered to MusicBrainz, and **nothing about it is sent to either of them even to ask a question**:
a lookup carries a title, a creator and a duration to somebody else's server, and for an album a
patron paid a creator for, nobody asked for that.

The rule is a **capability**, not a name. `sources.PRIVATE` is declared by the provider and enforced
by the core, so the next private source inherits it instead of being added to a list somewhere — and
these cases assert on the client that would have been called, never on a network that is not there.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from noaap import sources
from noaap.config import Config
from noaap.lyrics import publishable
from noaap.mb import seedable
from noaap.models import AlbumPlan, Candidate, Kind, PlanTrack, Provenance
from noaap.service import Service

TIMED = "[00:01.00] the first line\n[00:05.00] the second\n"


def a_track(provider: str) -> PlanTrack:
    track = PlanTrack(video_id="patreon:video:100004", number=1, artist="A Creator",
                      title="Chapter One", filename="01 Chapter One.m4a",
                      provenance={"lyrics": Provenance.USER}, state="done")
    track.candidates = [Candidate(ref="patreon:video:100004", provider=provider, from_video=True,
                                  added_by="source", length=1800.0)]
    track.chosen = "patreon:video:100004"
    return track


def an_album(provider: str = "patreon", **extra) -> AlbumPlan:
    plan = AlbumPlan(source_url="https://www.patreon.com/posts/100004", source_id="p100004",
                     kind=Kind.OFFICIAL_ALBUM, album="Chapter One", albumartist="A Creator",
                     year=None, cover_url=None, folder="A Creator/Chapter One",
                     tracks=[a_track(provider)], provider=provider)
    for key, value in extra.items():
        setattr(plan, key, value)
    return plan


# -- the two things that would leave the machine on purpose -----------------------------------------


def test_words_of_a_private_track_are_not_published():
    """Everything else about these words is right: timed, the user's own, a finished file beside them.
    That is the point — the refusal does not depend on the words at all."""
    track = a_track("patreon")

    reason = publishable(track, TIMED)

    assert reason, "a private track's words are never offered"
    assert "paid its creator" in reason
    # …and the same words on a track from anywhere else are publishable
    assert publishable(a_track("youtube"), TIMED) == ""


def test_an_album_from_a_private_source_is_not_offered_to_musicbrainz():
    assert "not offered to MusicBrainz" in seedable(an_album())
    assert seedable(an_album(provider="youtube")) == ""


def test_one_private_track_is_enough_to_stop_an_album_being_offered():
    """A folder album with one Patreon track in it is still somebody's purchase, in part — and an
    album is offered whole or not at all, so the one track decides for all of them."""
    plan = an_album(provider="folder")       # its own track is the folder's
    assert seedable(plan) == "", "a folder album is offered as before"

    theirs = a_track("patreon")
    theirs.number, theirs.video_id, theirs.chosen = 2, "patreon:video:100005", "patreon:video:100005"
    theirs.candidates[0].ref = "patreon:video:100005"
    plan.tracks.append(theirs)

    assert "not offered to MusicBrainz" in seedable(plan), "the private track decides"


# -- and the lookups, which nobody thinks of as sending anything ------------------------------------


class WouldTell:
    """A client that fails the case if it is asked anything at all."""

    def __getattr__(self, name):
        def refuse(*a, **k):
            raise AssertionError(f"a private album asked somebody: {name}({a!r})")
        return refuse


def a_service(plan_provider: str = "patreon") -> tuple[Service, AlbumPlan]:
    cfg = Config()
    service = Service(cfg, None, mb=WouldTell(), lrclib=WouldTell())
    return service, an_album(provider=plan_provider)


def test_nothing_about_a_private_album_is_looked_up():
    service, plan = a_service()

    assert service.may_look_up(plan) is False
    # the clients exist and are never used: proven by the fact that asking one raises
    with pytest.raises(AssertionError):
        service.mb.search_releases("A Creator", "Chapter One")


def test_an_album_from_anywhere_else_is_looked_up_as_before():
    service, plan = a_service("youtube")

    assert service.may_look_up(plan) is True


def test_the_owner_can_turn_lookups_on_for_one_album_and_nothing_else_can():
    service, plan = a_service()

    plan.lookups = True
    assert service.may_look_up(plan) is True, "the owner said so for this album"

    # and it is theirs alone: nothing in the program sets it, which is what this greps for
    from pathlib import Path

    root = Path(__file__).parent.parent / "src" / "noaap"
    writes = [f"{p.name}:{n}" for p in root.rglob("*.py")
              for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
              if ".lookups =" in line or "lookups=True" in line]
    assert not writes, f"something sets `lookups` for the user: {writes}"


def test_a_private_album_is_left_out_of_the_whole_library_lyrics_pass(tmp_path, monkeypatch):
    """The pass walks every album; this one is skipped and said out loud, so a user who asked for
    lyrics and got none is told why rather than left wondering."""
    from noaap import service as service_mod

    said: list[str] = []
    cfg = Config()
    svc = Service(cfg, tmp_path, log=said.append, lrclib=WouldTell())
    private, public = an_album(), an_album(provider="youtube")
    monkeypatch.setattr(service_mod, "iter_plans",
                        lambda root: [(tmp_path / "a", private), (tmp_path / "b", public)])
    monkeypatch.setattr(svc, "_lyrics_pass", lambda plan, album_dir, api: None)
    monkeypatch.setattr(svc, "_guarded", lambda fn: fn())

    svc.fetch_lyrics()

    assert any("not looked up anywhere" in line for line in said)


def test_the_rule_is_a_capability_and_not_a_name():
    """Nothing outside a provider may decide this. The core asks `sources.private(...)`, and the two
    refusals are written against that — so a second private source needs no edit here."""
    from pathlib import Path

    root = Path(__file__).parent.parent / "src" / "noaap"
    allowed = {"patreon.py", "sources_patreon.py"}
    offenders = [f"{p.name}:{n}" for p in root.rglob("*.py") if p.name not in allowed
                 for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                 if '"patreon"' in line and "register" not in line and "DEFAULT" not in line]
    assert not offenders, f"the core names a provider to decide privacy: {offenders}"
    assert sources.private("patreon") and not sources.private("youtube")


# -- and nothing of the session reaches the file (§9, slice 73, R-254) ------------------------------

SIGNED_COVER = ("https://c10.mediahost.invalid/4/media/p/post/100004/da364cac/eyJ3IjoxMDgwfQ%3D%3D/"
                "1.jpeg?token-hash=kqZv17OGDTNzuX7KzgTtWhul-fnROE7E&token-time=1791936000")


def test_a_private_albums_plan_never_holds_a_signed_address(tmp_path):
    """Found by the reviewer's acceptance run on the first real fetch: the plan had stored the signed
    address of a paid post's image — `token-hash` and `token-time` in the query. A signed address is
    a piece of the session, and the rule is that nothing of the session is stored."""
    from noaap.download import save_plan, written

    plan = an_album()
    plan.cover_url = SIGNED_COVER
    plan.cover_fallback_url = SIGNED_COVER + "&size=large"
    plan.cover_fetched = {"url": SIGNED_COVER, "sha1": "deadbeef"}

    out = written(plan, tmp_path)

    assert out["cover_url"] is None and out["cover_fallback_url"] is None
    assert out["cover_fetched"]["url"] is None
    assert out["cover_fetched"]["sha1"] == "deadbeef", "what is not an address is kept"

    # and the same plan from anywhere else is untouched: this is a rule about private sources
    public = an_album(provider="youtube")
    public.cover_url = SIGNED_COVER
    assert written(public, tmp_path)["cover_url"] == SIGNED_COVER

    # …and it holds through a real save
    save_plan(plan, tmp_path)
    assert "token-hash" not in (tmp_path / ".noaap.json").read_text(encoding="utf-8")


def test_a_plan_written_before_the_rule_is_cleaned_by_the_next_write(tmp_path):
    """The library already holds one such plan. Nothing rewrites plans for fun, so the cleaning has to
    happen wherever a save happens — which is the one place that decides what a save writes."""
    from noaap.download import PLAN_FILE, load_plan, save_plan

    plan = an_album()
    (tmp_path / PLAN_FILE).write_text(json.dumps({**plan.to_dict(), "cover_url": SIGNED_COVER}),
                                      encoding="utf-8")
    loaded = load_plan(tmp_path)
    assert loaded is not None and loaded.cover_url == SIGNED_COVER, "it is read as it stands"

    save_plan(loaded, tmp_path)

    assert "token-hash" not in (tmp_path / PLAN_FILE).read_text(encoding="utf-8")
    assert json.loads((tmp_path / PLAN_FILE).read_text(encoding="utf-8"))["cover_url"] is None


@pytest.mark.parametrize("marker", ["token", "Policy", "Signature", "Key-Pair-Id"])
def test_no_written_value_of_a_private_album_carries_a_signing_parameter(tmp_path, marker):
    """The rule as a grep, not as a list of fields somebody has to remember to extend. Every value of
    a written plan is searched, whatever field it sits in and however deep."""
    from noaap.download import written

    plan = an_album()
    plan.cover_url = f"https://c10.mediahost.invalid/1.jpeg?{marker}=abc123"
    plan.cover_fallback_url = f"https://d.cloudfront.invalid/2.jpg?Expires=1&{marker}=xyz"
    plan.tracks[0].candidates[0].why = f"taken from https://c10.mediahost.invalid/3?{marker}=nope"
    plan.source_state = {"art": f"https://c10.mediahost.invalid/4?{marker}=deep"}

    text = json.dumps(written(plan, tmp_path))

    assert marker.lower() not in text.lower(), f"a private plan carries {marker}: {text}"


# -- the host is parsed, and a private album can still get a cover (§9, slice 73, R-255) ------------


@pytest.mark.parametrize("address,ours", [
    ("https://c10.patreonusercontent.com/4/x.jpeg?token-hash=a&token-time=1", True),
    ("https://patreonusercontent.com/x.jpg", True),
    ("https://evil.example/x.jpg?patreonusercontent.com", False),
    ("https://patreonusercontent.com.evil.example/x.jpg", False),
    ("https://patreonusercontent.com@evil.example/x.jpg", False),
    ("https://evil.example/patreonusercontent.com/x.jpg", False),
    ("https://c10.patreonusercontent.com:8443/x.jpg", False),
    ("http://c10.patreonusercontent.com/x.jpg", False),
    ("", False),
])
def test_a_media_address_is_decided_by_its_parsed_host(address, ours):
    """It was a substring test, and a substring test said yes to three addresses that are not the
    media host at all — and a yes sends the browser's session and a referer to whoever owns them."""
    from noaap.patreon import _is_media_address

    assert _is_media_address(address) is ours


def test_a_cover_that_redirects_off_the_media_host_is_refused(monkeypatch):
    """The client follows redirects, so where the request landed decides too: bytes from anywhere
    else are not this post's image."""
    from noaap import patreon as mod
    from noaap.config import Config as Settings

    cfg = Settings()
    cfg.patreon_cookies_from_browser = "chrome"
    pt = mod.Patreon(cfg)

    class FakeYDL:
        def __init__(self, options): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def urlopen(self, request):
            return type("R", (), {"read": staticmethod(lambda: b"\xff\xd8\xff"),
                                  "url": "https://evil.example/x.jpg"})()

    monkeypatch.setattr(mod, "YoutubeDL", FakeYDL)

    with pytest.raises(sources.SourceError) as refused:
        pt.fetch_bytes("https://c10.patreonusercontent.com/4/x.jpeg?token-hash=a")

    assert "led somewhere else" in str(refused.value) and "evil.example" in str(refused.value)


def test_an_address_nobody_built_is_never_handed_to_the_client(monkeypatch):
    """`source_state` used to fall back to the address it was given — and a plan's `source_url` is a
    field a person can edit. Now it is a post address, a campaign address, or no request at all."""
    from noaap import patreon as mod
    from noaap.config import Config as Settings

    cfg = Settings()
    cfg.patreon_cookies_from_browser = "chrome"
    pt = mod.Patreon(cfg)
    asked: list[str] = []
    monkeypatch.setattr(pt, "_read", lambda url, **extra: asked.append(url) or {"id": "1"})

    assert pt.source_state("https://evil.example/anything") is None
    assert pt.source_state("file:///etc/passwd") is None
    assert asked == [], "nothing was asked of an address we did not build"

    pt.source_state("https://www.patreon.com/posts/100004")
    assert asked == ["https://www.patreon.com/posts/100004"]


def test_a_private_album_without_an_address_asks_its_source_for_a_cover(tmp_path):
    """The rule says a private plan holds no address; without this, such an album could never get a
    cover — the first fetch had one in memory and every later run had nothing to try. It is asked for
    only while a run for that album is happening anyway."""
    from noaap.download import _cover, _cover_addresses

    plan = an_album()
    plan.cover_url = plan.cover_fallback_url = None
    assert _cover_addresses(plan) == [plan.source_url], "the post itself is what is asked"

    asked: list[str] = []

    class Provider:
        name = "patreon"

        def art(self, address):
            asked.append(address)
            return b"\x89PNG\r\n\x1a\n" + b"0" * 64

    data = _cover(plan, tmp_path, Provider())

    assert asked == [plan.source_url] and data.startswith(b"\x89PNG")
    assert (tmp_path / "cover.png").exists()
    assert plan.cover_fetched["url"] == plan.source_url, "what is remembered is the post, not a token"
    assert "token" not in json.dumps(plan.cover_fetched)


def test_and_an_album_from_anywhere_else_is_not_asked_for_one(tmp_path):
    """For every other provider the plan's address is the address, and handing a collection's own page
    to `art()` would be a request nobody asked for."""
    from noaap.download import _cover_addresses

    public = an_album(provider="youtube")
    public.cover_url = public.cover_fallback_url = None

    assert _cover_addresses(public) == []


def test_a_provider_with_no_image_leaves_the_album_without_one_and_says_why(tmp_path, caplog):
    from noaap.download import _cover

    plan = an_album()
    plan.cover_url = plan.cover_fallback_url = None

    class Provider:
        name = "patreon"

        def art(self, address):
            raise sources.SourceError("no cover for this post")

    with caplog.at_level("WARNING"):
        assert _cover(plan, tmp_path, Provider()) is None

    assert not list(tmp_path.glob("cover.*")), "no file, and no empty one either"
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "could not fetch any cover" in said and "no cover for this post" in said


def test_every_address_this_provider_sends_to_is_one_it_built_or_checked(monkeypatch):
    """The audit R-255 asked for, as a case. Each entry point is given an address of somebody else's
    choosing; none of them may reach the client, and the one that may — a media address — is the one
    that passes the host test."""
    from noaap import patreon as mod
    from noaap.config import Config as Settings

    cfg = Settings()
    cfg.patreon_cookies_from_browser = "chrome"
    pt = mod.Patreon(cfg)
    asked: list[str] = []
    monkeypatch.setattr(pt, "_read", lambda url, **extra: asked.append(url) or {"id": "1", "entries": []})

    strange = "https://evil.example/whatever?patreon.com"
    for call in (lambda: pt.fetch(strange),
                 lambda: pt.list_owner(strange),
                 lambda: pt.fetch_bytes(strange),
                 lambda: pt.download_audio("patreon:media:not-digits", Path("/tmp")),
                 lambda: pt.probe("patreon:video:nope")):
        with pytest.raises(sources.SourceError):
            call()
    assert pt.source_state(strange) is None
    assert asked == [], f"an address nobody built reached the client: {asked}"

    # and the addresses it does build are its own host's, every one of them
    from urllib.parse import urlsplit

    for built in (mod.post_url("100004"), mod.campaign_of("https://www.patreon.com/cw/acreator"),
                  mod.BASE):
        assert urlsplit(built).hostname == "www.patreon.com"


# -- a switch of its own, and two parsers that disagree (§9, slice 77, R-271) -----------------------


def test_lookups_no_longer_lets_the_recording_leave():
    """A title and a recording are different things. `lookups` says a name and a length may be asked
    of a stranger; for one package it was read as *upload the audio too*, and an owner who had
    allowed a lookup would have had their paid recording sent to a vendor."""
    from noaap.service import may_look_up, may_send_audio

    cfg = Config()
    cfg.timing_provider = "deepgram"
    plan = an_album()

    plan.lookups = True
    assert may_look_up(cfg, plan) is True, "the title may still be asked about"
    assert may_send_audio(cfg, plan, "align") is False, "and the recording still may not go"

    plan.send_audio = True
    assert may_send_audio(cfg, plan, "align") is True, "only its own switch says yes"

    # …and an explicit no is a no even for a provider that keeps it here
    plan.lookups, plan.send_audio = None, False
    cfg.timing_provider = "local"
    assert may_send_audio(cfg, plan, "align") is False

    # nothing in the program sets it, which is what this greps for
    root = Path(__file__).parent.parent / "src" / "noaap"
    writes = [f"{p.name}:{n}" for p in root.rglob("*.py")
              for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
              if ".send_audio =" in line or "send_audio=True" in line]
    assert not writes, f"something sets `send_audio` for the user: {writes}"


@pytest.mark.parametrize("address", [
    "https://evil.example\\@c10.patreonusercontent.com/x.jpg",
    "https://patreonusercontent.com@evil.example/x.jpg",
    "https://user:pw@c10.patreonusercontent.com/x.jpg",
    "https://c10.patreonusercontent.com\\@evil.example/x.jpg",
])
def test_an_address_two_parsers_read_differently_is_refused(address):
    """A browser treats `\\` as a path separator and this parser does not, so one reads
    `evil.example` as the host and the other reads ours. Userinfo before the host is the older
    version of the same trick. An address that needs a ruling on whose parser is right is not one
    this program asks anything of."""
    from noaap.patreon import _is_media_address

    assert _is_media_address(address) is False


def test_a_provider_that_would_send_nothing_is_not_named_as_the_one_that_would(tmp_path):
    """With nothing configured there is nobody to send a recording to, and the refusal said
    "`none` would have to send the recording off this machine"."""
    from noaap.timing import TimingUnavailable

    album_dir, plan = a_library_for_audio(tmp_path)
    service = a_service_for_audio(tmp_path, timing_provider="none")

    with pytest.raises(TimingUnavailable) as refused:
        service.align_lyrics(plan.source_id, plan.tracks[0].video_id, "a line\n")

    said = str(refused.value)
    assert "`none`" not in said and "paid its creator" not in said
    assert "timing provider" in said.lower(), said


def a_library_for_audio(tmp_path: Path):
    from noaap.download import save_plan

    album_dir = tmp_path / "A Creator" / "Chapter One"
    album_dir.mkdir(parents=True)
    (album_dir / "01 Chapter One.m4a").write_bytes(b"\x00" * 64)
    plan = an_album()
    plan.folder = "A Creator/Chapter One"
    plan.tracks[0].filename = "01 Chapter One.m4a"
    plan.tracks[0].state = "done"
    save_plan(plan, album_dir)
    return album_dir, plan


def a_service_for_audio(tmp_path: Path, **settings):
    from noaap.service import Service

    cfg = Config()
    for key, value in settings.items():
        setattr(cfg, key, value)
    return Service(cfg, tmp_path, log=lambda line: None)
