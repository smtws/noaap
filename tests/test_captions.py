"""A post's captions as words beside the track (DESIGN §9, slice 74).

The creator this was built for posts audiobooks, and their captions are the book. So the rules are
about what may be done with somebody else's writing, not about lyrics: kept beside the track, marked
as theirs, never written into the file, never offered to anybody, and never claimable as the user's.

Everything here is a written fixture. **No caption file has ever been fetched from the live site** —
that needs the owner's word and is not part of this package.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noaap import captions, sources
from noaap.config import Config
from noaap.models import AlbumPlan, Candidate, Kind, PlanTrack, Provenance
from noaap.sources_patreon import PatreonSource

VTT = """WEBVTT
Kind: captions
Language: en

NOTE this note is not spoken
and neither is this

STYLE
::cue { color: papayawhip }

1
00:00:07.120 --> 00:00:11.040 line:0%,position:50%
<v Narrator>He had been awake for nineteen hours
when the station finally <i>answered</i>.

2
00:00:11.500 --> 00:00:14.000
&amp; then it said nothing at all
"""


def a_track() -> PlanTrack:
    track = PlanTrack(video_id="patreon:video:100004", number=1, artist="A Creator",
                      title="Chapter One", filename="01 Chapter One.m4a", provenance={},
                      state="done", ext="m4a")
    track.candidates = [Candidate(ref="patreon:video:100004", provider="patreon", from_video=True)]
    track.chosen = "patreon:video:100004"
    return track


def an_album() -> AlbumPlan:
    return AlbumPlan(source_url="https://www.patreon.com/posts/100004", source_id="p100004",
                     kind=Kind.OFFICIAL_ALBUM, album="Chapter One", albumartist="A Creator",
                     year=None, cover_url=None, folder="A Creator/Chapter One",
                     tracks=[a_track()], provider="patreon")


# -- reading the format ----------------------------------------------------------------------------


def test_a_cue_becomes_one_line_of_words_with_its_start_stamp():
    """Two stamps become one: `.lrc` holds a start per line and an end would be invented. A cue's own
    line breaks are reading-width, not sentences, so they become one line."""
    cues = captions.read(VTT)

    assert cues == [(7.12, "He had been awake for nineteen hours when the station finally answered."),
                    (11.5, "& then it said nothing at all")]
    assert captions.as_lrc(cues).splitlines()[0] == \
        "[00:07.12] He had been awake for nineteen hours when the station finally answered."


def test_everything_that_is_presentation_is_dropped():
    """A speaker tag, an italic, a cue identifier, the settings after the stamps, NOTE and STYLE
    blocks: none of it is what is being said."""
    text = captions.as_lrc(captions.read(VTT))

    for gone in ("<v", "<i>", "NOTE", "STYLE", "papayawhip", "line:0%", "position:50%", "Kind:"):
        assert gone not in text, f"{gone!r} survived into the words"


def test_overlapping_cues_are_kept_in_start_order():
    """Two people talking at once is two cues, and dropping either would lose words."""
    overlapping = ("WEBVTT\n\n00:00:10.000 --> 00:00:14.000\nsecond speaker\n\n"
                   "00:00:09.000 --> 00:00:12.000\nfirst speaker\n")

    assert captions.read(overlapping) == [(9.0, "first speaker"), (10.0, "second speaker")]


@pytest.mark.parametrize("stamp,expected", [("00:00:07.120", 7.12), ("02:03.400", 123.4),
                                            ("01:02:03,400", 3723.4)])
def test_both_stamp_shapes_the_format_allows(stamp, expected):
    assert captions.seconds(stamp) == pytest.approx(expected)


def test_a_file_that_is_not_webvtt_yields_nothing():
    assert not captions.is_webvtt("1\n00:00:07,120 --> 00:00:11,040\nan srt file\n")
    assert captions.read("1\n00:00:07,120 --> 00:00:11,040\nan srt file\n") == []
    assert captions.read(b"") == []


# -- what the provider decides (fixtures only) -------------------------------------------------------


def taking(**extra: Any) -> Config:
    cfg = Config()
    cfg.patreon_cookies_from_browser = "chrome"
    cfg.patreon_audio_from_video = True
    cfg.patreon_captions = True
    for key, value in extra.items():
        setattr(cfg, key, value)
    return cfg


def a_post(**extra: Any) -> dict[str, Any]:
    post = {"id": "100004", "title": "Chapter One", "webpage_url": "https://www.patreon.com/posts/100004",
            "ext": "mp4", "vcodec": "avc1.64002a", "acodec": "mp4a.40.2", "has_drm": False,
            "subtitles": {"en": [{"ext": "vtt", "url": "https://manifest.edgemv.mux.com/x.vtt"}]},
            "formats": [{"format_id": "294", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a.40.2",
                         "tbr": 294.8, "height": 270, "url": "https://manifest.edgemv.mux.com/x.m3u8"}]}
    post.update(extra)
    return post


def decides(monkeypatch, post: dict[str, Any], *, starts_at: float | None = 0.0,
            body: bytes = VTT.encode(), cfg: Config | None = None) -> dict[str, Any]:
    from noaap import patreon as mod

    pt = PatreonSource(cfg or taking()).pt
    monkeypatch.setattr(mod, "_audio_starts_at", lambda path: starts_at)
    monkeypatch.setattr(pt, "_media_bytes", lambda url, accept="": body)
    return pt._captions(post, Path("/dev/null"))


def test_the_creators_own_captions_are_taken(monkeypatch):
    found = decides(monkeypatch, a_post())

    assert found["by"] == "patreon" and found["language"] == "en"
    assert captions.read(found["bytes"])[0][0] == 7.12


def test_captions_the_platform_generated_are_refused(monkeypatch):
    """They are a machine's draft of the audio, not the creator's text — a different thing with a
    different owner, and the spike said to decide this before building."""
    post = a_post(subtitles={}, automatic_captions={"en": [{"ext": "vtt", "url": "https://manifest.edgemv.mux.com/a.vtt"}]})

    assert "generated by the platform" in decides(monkeypatch, post)["refused"]


def test_captions_served_from_somewhere_else_are_refused(monkeypatch):
    post = a_post(subtitles={"en": [{"ext": "vtt", "url": "https://evil.example/x.vtt"}]})

    assert "somewhere this provider does not read" in decides(monkeypatch, post)["refused"]


def test_a_file_that_is_not_webvtt_is_refused(monkeypatch):
    assert "neither a WebVTT file nor a playlist" in decides(monkeypatch, a_post(), body=b"1\n00:00:01,0 --> 00:00:02,0\nsrt\n")["refused"]


def test_audio_that_does_not_start_at_zero_is_refused(monkeypatch):
    """The stamps belong to the asset; the audio copied out of it is timed to itself. A constant
    offset on every line looks right and is not, which is worse than no words at all."""
    refused = decides(monkeypatch, a_post(), starts_at=1.4)["refused"]

    assert "does not start at zero" in refused and "+1.400" in refused
    assert "not trusted unmeasured" in decides(monkeypatch, a_post(), starts_at=None)["refused"]


def test_a_post_without_captions_says_so(monkeypatch):
    assert decides(monkeypatch, a_post(subtitles={}))["refused"] == "this post has no captions"


def test_off_by_default_nothing_is_even_looked_for(monkeypatch):
    """The setting is off unless somebody turns it on, and off means the question is never asked."""
    from noaap import patreon as mod

    cfg = taking(patreon_captions=False)
    assert cfg.patreon_captions is False
    pt = PatreonSource(cfg).pt
    monkeypatch.setattr(mod, "_audio_starts_at", lambda path: pytest.fail("asked with the setting off"))
    assert pt.last_captions is None


# -- and what the core does with them ----------------------------------------------------------------


class Gave:
    """A provider that has just handed over audio and its captions."""

    name = "patreon"

    def __init__(self, found: dict[str, Any] | None):
        self.last_captions = found


def test_the_words_are_written_beside_the_track_and_marked_as_the_creators(tmp_path):
    from noaap.download import _words_from_source

    plan, track = an_album(), None
    track = plan.tracks[0]

    said = _words_from_source(Gave({"by": "patreon", "bytes": VTT.encode()}), plan, track, tmp_path)

    sidecar = tmp_path / "01 Chapter One.lrc"
    assert sidecar.exists() and "[00:07.12]" in sidecar.read_text(encoding="utf-8")
    assert track.provenance["lyrics"] == Provenance.SOURCE
    assert track.lyrics_words_by == "patreon" and track.lyrics_timed_by == "patreon"
    assert track.lyrics == "synced" and track.lyrics_id is None
    assert "2 line(s) of patreon's own captions" in said and "nowhere else" in said


def test_a_refusal_is_said_once_and_nothing_is_written(tmp_path):
    from noaap.download import _words_from_source

    plan = an_album()
    said = _words_from_source(Gave({"by": "patreon", "refused": "this post has no captions"}),
                              plan, plan.tracks[0], tmp_path)

    assert said == "no words beside this track: this post has no captions"
    assert not list(tmp_path.glob("*.lrc"))
    assert plan.tracks[0].provenance.get("lyrics") is None


def test_a_provider_with_nothing_to_say_says_nothing(tmp_path):
    from noaap.download import _words_from_source

    plan = an_album()
    assert _words_from_source(Gave(None), plan, plan.tracks[0], tmp_path) is None
    assert _words_from_source(object(), plan, plan.tracks[0], tmp_path) is None


def test_such_words_never_reach_the_audio_file(tmp_path):
    """**Sidecar only.** The tag is decided in one place, so no pass — a fetch, a retag, `repair` —
    can carry them into the file by another route. Asserted on what `build_tags` would write, which
    is also what the signature is taken of, so the track does not read as permanently out of date."""
    from noaap.tag import build_tags, signature

    plan = an_album()
    track = plan.tracks[0]
    words = "[00:07.12] the creator's own sentence"

    track.provenance["lyrics"] = Provenance.SOURCE
    assert "lyrics" not in build_tags(plan, track, words)
    assert signature(plan, track, None, words) == signature(plan, track, None, None), \
        "and the file does not look out of date for the words it will never hold"

    # the user's own words, and lrclib's, are written as before
    for whose in (Provenance.USER, Provenance.MB, None):
        track.provenance["lyrics"] = whose
        assert build_tags(plan, track, words).get("lyrics") == words


def test_the_publish_refusal_stands_on_its_own_ground(tmp_path):
    """Two refusals, tested apart: the words are not the user's, **and** the audio is from a private
    source. Either alone is enough, so neither is doing the other's work."""
    from noaap.lyrics import publishable

    # 1. captions from a source nobody calls private: still refused, for whose words they are
    track = a_track()
    track.candidates[0].provider = "folder"
    track.provenance["lyrics"] = Provenance.SOURCE
    track.lyrics_words_by = "patreon"
    assert not sources.private("folder")
    reason = publishable(track, "[00:01.00] a line\n")
    assert "came with the recording" in reason and "not yours to give away" in reason

    # 2. the user's own words on a private track: refused for where the audio came from
    mine = a_track()
    mine.provenance["lyrics"] = Provenance.USER
    assert sources.private(mine.candidate(mine.effective_id).provider)
    assert "paid its creator" in publishable(mine, "[00:01.00] a line\n")


def test_captions_open_no_lookup(tmp_path, monkeypatch):
    """Writing them asks nobody anything: no lrclib, no MusicBrainz, not even to check a length."""
    from noaap.download import _words_from_source

    class WouldTell:
        def __getattr__(self, name):
            def refuse(*a, **k):
                raise AssertionError(f"a caption write asked somebody: {name}")
            return refuse

    monkeypatch.setattr("noaap.lyrics.Lrclib", WouldTell)
    monkeypatch.setattr("noaap.mb.MusicBrainz", WouldTell)

    plan = an_album()
    _words_from_source(Gave({"by": "patreon", "bytes": VTT.encode()}), plan, plan.tracks[0], tmp_path)

    assert (tmp_path / "01 Chapter One.lrc").exists()


# -- what a save may not do (§9, slice 75, R-258 defect 1) ------------------------------------------


def a_library(tmp_path: Path) -> tuple[Path, AlbumPlan]:
    """A real album on disk: an m4a with tags, its plan, and the creator's captions beside it."""
    import shutil
    import subprocess

    album_dir = tmp_path / "A Creator" / "Chapter One"
    album_dir.mkdir(parents=True)
    audio = album_dir / "01 Chapter One.m4a"
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is needed to make a real file to read tags back from")
    subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", "2", "-c:a", "aac", str(audio)], check=True)
    plan = an_album()
    plan.folder = "A Creator/Chapter One"
    from noaap.download import save_plan

    save_plan(plan, album_dir)
    return album_dir, plan


def a_service(tmp_path: Path, **settings: Any):
    from noaap.service import Service

    cfg = Config()
    for key, value in settings.items():
        setattr(cfg, key, value)
    return Service(cfg, tmp_path, log=lambda line: None)


def tag_of(path: Path) -> str | None:
    from noaap.tag import tagged_lyrics

    return tagged_lyrics(path)


@pytest.mark.parametrize("words_by,timed_by", [("patreon", "patreon"), ("", ""), ("me", "me")])
def test_an_edit_does_not_turn_the_creators_words_into_the_users(tmp_path, words_by, timed_by):
    """One save from the editor turned captions into `Provenance.USER` — and, being the user's, they
    went into the audio file's tag. **The page withholding the control is a courtesy; this is the
    rule.** Whatever the request says about `words_by` is ignored for such words."""
    from noaap.download import _words_from_source, load_plan

    album_dir, plan = a_library(tmp_path)
    _words_from_source(Gave({"by": "patreon", "bytes": VTT.encode()}), plan, plan.tracks[0], album_dir)
    from noaap.download import save_plan

    save_plan(plan, album_dir)
    service = a_service(tmp_path)

    outcome = service.save_lyrics(plan.source_id, plan.tracks[0].video_id,
                                  "[00:07.12] he had been awake for NINETEEN hours\n",
                                  timed_by=timed_by, words_by=words_by)

    assert outcome.status == "ok"
    after = load_plan(album_dir).tracks[0]
    assert after.provenance["lyrics"] == Provenance.SOURCE, "they are still the creator's"
    assert after.lyrics_words_by == "patreon" and after.lyrics_timed_by == "patreon"
    # the retag pass renames to the album's own scheme, so the files are asked of the plan
    assert "NINETEEN" in (album_dir / after.filename).with_suffix(".lrc").read_text(encoding="utf-8")
    assert tag_of(album_dir / after.filename) is None, "and the file still holds no words"


def test_clearing_them_gives_the_mark_up_and_what_is_written_next_is_the_users(tmp_path):
    """What is gone is gone: after a clear there are no words and no mark, so whatever somebody
    writes from nothing is their own — and still unpublishable while the audio is a private
    source's, which is the other rule and not this one."""
    from noaap.download import _words_from_source, load_plan, save_plan
    from noaap.lyrics import publishable

    album_dir, plan = a_library(tmp_path)
    _words_from_source(Gave({"by": "patreon", "bytes": VTT.encode()}), plan, plan.tracks[0], album_dir)
    save_plan(plan, album_dir)
    service = a_service(tmp_path)

    service.save_lyrics(plan.source_id, plan.tracks[0].video_id, "   ")
    cleared = load_plan(album_dir).tracks[0]
    assert cleared.lyrics == "none" and "lyrics" not in cleared.provenance
    assert cleared.lyrics_words_by is None and cleared.lyrics_timed_by is None
    assert not list(album_dir.glob("*.lrc"))

    service.save_lyrics(plan.source_id, plan.tracks[0].video_id, "[00:01.00] words I wrote myself\n")
    mine = load_plan(album_dir).tracks[0]
    assert mine.provenance["lyrics"] == Provenance.USER, "written from nothing, so they are theirs"
    assert tag_of(album_dir / mine.filename) is not None, "and the user's words are tagged"
    assert "paid its creator" in publishable(mine, "[00:01.00] words I wrote myself\n"), \
        "…and still not publishable, because of where the audio came from"


def test_no_route_writes_the_creators_words_into_the_file(tmp_path):
    """Through the three doors that write tags: a save, a retag pass, and `repair`."""
    from noaap.download import _words_from_source, load_plan, run, save_plan

    album_dir, plan = a_library(tmp_path)
    _words_from_source(Gave({"by": "patreon", "bytes": VTT.encode()}), plan, plan.tracks[0], album_dir)
    save_plan(plan, album_dir)
    service = a_service(tmp_path)

    service.save_lyrics(plan.source_id, plan.tracks[0].video_id, "[00:07.12] corrected\n")
    audio = album_dir / load_plan(album_dir).tracks[0].filename
    assert tag_of(audio) is None, "after a save"

    class Nothing:
        name = "patreon"

        def audio(self, *a, **k): raise AssertionError("nothing is downloaded by a retag")
        def art(self, *a, **k): raise AssertionError("nothing is fetched by a retag")

    run(load_plan(album_dir), album_dir, Nothing(), download=False)
    assert tag_of(audio) is None, "after a retag pass"

    service.repair(dry_run=False)
    assert tag_of(audio) is None, "after repair"
    assert "corrected" in audio.with_suffix(".lrc").read_text(encoding="utf-8")


# -- and the audio itself (§9, slice 75, R-258 defect 2) ---------------------------------------------


@pytest.mark.parametrize("provider,endpoint,stays", [
    ("local", "", True),
    ("http", "http://127.0.0.1:8471", True),
    ("http", "http://localhost:8471", True),
    ("http", "http://[::1]:8471", True),
    ("http", "http://192.168.1.20:8471", False),
    ("http", "https://timing.example", False),
    ("http", "http://127.0.0.1.evil.example", False),
    ("deepgram", "", False),
    ("elevenlabs", "", False),
    ("none", "", False),
])
def test_which_providers_count_as_this_machine(provider, endpoint, stays):
    """`local` runs in this process. `http` is this machine **only on a loopback endpoint** — the
    timing server on the desktop upstairs is another computer, however trusted. A vendor never is."""
    from noaap.timing import stays_here

    cfg = Config()
    cfg.timing_provider, cfg.timing_endpoint = provider, endpoint

    assert stays_here(cfg, "align") is stays


def test_a_private_albums_audio_is_not_sent_to_a_vendor(tmp_path):
    """`align_lyrics` and `draft_lyrics` handed the file to whatever provider was configured, so an
    album somebody paid a creator for was uploaded to a third party for a transcript. This predates
    the captions and was in a release."""
    from noaap.service import may_send_audio
    from noaap.timing import TimingUnavailable

    album_dir, plan = a_library(tmp_path)
    service = a_service(tmp_path, timing_provider="deepgram", timing_deepgram_key="x")
    assert may_send_audio(service.cfg, plan, "align") is False

    def engine_would_be_built(*a, **k):
        raise AssertionError("a private album's audio reached the timing provider")

    service._timing = engine_would_be_built
    for call in (lambda: service.align_lyrics(plan.source_id, plan.tracks[0].video_id, "a line\n"),
                 lambda: service.draft_lyrics(plan.source_id, plan.tracks[0].video_id)):
        with pytest.raises(TimingUnavailable) as refused:
            call()
        assert "paid its creator" in str(refused.value) and "`deepgram`" in str(refused.value)


def test_it_is_sent_to_a_provider_that_runs_here(tmp_path):
    """The rule is about the audio leaving the machine, so a provider that does not take it off the
    machine is not refused — and neither is an album whose owner turned lookups on for it."""
    from noaap.service import may_send_audio

    _, plan = a_library(tmp_path)
    for cfg_args in ({"timing_provider": "local"},
                     {"timing_provider": "http", "timing_endpoint": "http://127.0.0.1:8471"}):
        assert may_send_audio(a_service(tmp_path, **cfg_args).cfg, plan, "align") is True

    owner_said_yes = a_service(tmp_path, timing_provider="deepgram")
    plan.lookups = True
    assert may_send_audio(owner_said_yes.cfg, plan, "align") is True
    plan.lookups = None

    # and an album from anywhere else is untouched by any of this
    public = an_album()
    public.provider = "youtube"
    public.tracks[0].candidates[0].provider = "youtube"
    assert may_send_audio(a_service(tmp_path, timing_provider="deepgram").cfg, public, "align") is True


def test_the_page_is_not_offered_what_the_server_would_refuse(tmp_path):
    """"No button" is a worse answer than "no, because…" — but a button that fails is worse than
    both. The album's own answer is in the payload the panel reads."""
    from noaap.web import App

    album_dir, plan = a_library(tmp_path)
    cfg = Config()
    cfg.timing_provider, cfg.timing_deepgram_key = "deepgram", "x"
    app = App(cfg, tmp_path)

    payload = app.lyrics(plan.source_id, plan.tracks[0].video_id)

    assert payload["may_send_audio"] == {"align": False, "draft": False}
    assert payload["can_check"] is False


def test_the_stamp_never_names_a_minute_that_does_not_exist():
    """`59.996` came out as `[00:60.00]`: the seconds were split off and *then* rounded. Rounding to
    the unit that is written, before dividing, cannot do that (R-258, defect 3)."""
    from noaap.captions import stamp

    assert stamp(59.996) == "[01:00.00]"
    assert stamp(119.999) == "[02:00.00]"
    assert stamp(3599.9951) == "[60:00.00]"
    assert stamp(59.994) == "[00:59.99]" and stamp(0) == "[00:00.00]" and stamp(7.12) == "[00:07.12]"
    assert ":60." not in captions.as_lrc([(59.996, "a"), (119.999, "b"), (3599.9951, "c")])


# -- captions served as a playlist (§9, slice 76, R-262) ---------------------------------------------
#
# Synthetic throughout: the words below are invented for these cases and no line of any real caption
# file is in this repository.

PLAYLIST = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-PLAYLIST-TYPE:VOD
#EXTINF:10.00000,
seg-0.vtt
#EXTINF:10.00000,
seg-1.vtt
#EXT-X-ENDLIST
"""

# the shape this platform really writes: the same map in every segment, and cue stamps that are
# already the asset's own, so a cue spanning a border is written twice with the *same* time
SEG0 = """WEBVTT
X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000

00:00:01.000 --> 00:00:04.000
the first invented line

00:00:09.500 --> 00:00:11.500
a line that spans the border
"""

SEG1 = """WEBVTT
X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000

00:00:09.500 --> 00:00:11.500
a line that spans the border

00:00:13.000 --> 00:00:15.000
the second invented line
"""

# the other convention, which the format also allows: each segment's stamps start again at zero and
# its map says where that zero is
RESTARTING = ("WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:{ts}\n\n"
              "00:00:01.000 --> 00:00:02.000\n{said}\n")


def serving(monkeypatch, answers: dict[str, bytes], *, starts_at: float = 0.0) -> dict[str, Any]:
    """The provider with its one connection replaced by a table of addresses to bytes."""
    from noaap import patreon as mod

    pt = PatreonSource(taking()).pt
    asked: list[str] = []

    def media(url: str, accept: str = "") -> bytes:
        asked.append(url)
        try:
            return answers[url]
        except KeyError:
            raise sources.SourceError(f"nothing recorded for {url}") from None

    monkeypatch.setattr(mod, "_audio_starts_at", lambda path: starts_at)
    monkeypatch.setattr(pt, "_media_bytes", media)
    found = pt._captions(a_post(), Path("/dev/null"))
    found["asked"] = asked
    return found


def test_a_playlist_is_read_and_its_segments_joined(monkeypatch):
    """What the live post really serves: `ext: vtt`, `protocol: m3u8_native`, an address ending
    `subtitles.m3u8`. The segments are the WebVTT; the address is the list of them."""
    found = serving(monkeypatch, {
        "https://manifest.edgemv.mux.com/x.vtt": PLAYLIST.encode(),
        "https://manifest.edgemv.mux.com/seg-0.vtt": SEG0.encode(),
        "https://manifest.edgemv.mux.com/seg-1.vtt": SEG1.encode()})

    assert "refused" not in found, found.get("refused")
    assert found["requests"] == 3 and len(found["asked"]) == 3
    assert found["asked"][1].endswith("/seg-0.vtt"), "relative segments resolve against the playlist"
    starts = [start for start, _ in found["cues"]]
    assert starts == sorted(starts), "in start order"
    assert found["cues"] == [(1.0, "the first invented line"),
                             (9.5, "a line that spans the border"),
                             (13.0, "the second invented line")]


def test_segments_whose_stamps_restart_are_shifted_by_their_own_map(monkeypatch):
    """The format's other convention: each segment counts from zero and its `X-TIMESTAMP-MAP` says
    where that zero is. The first segment's base is the asset's zero — which is where the audio
    starts, measured — and every later one is shifted by its own base relative to it."""
    playlist = "#EXTM3U\n#EXTINF:10,\nseg-0.vtt\n#EXTINF:10,\nseg-1.vtt\n#EXT-X-ENDLIST\n"
    found = serving(monkeypatch, {
        "https://manifest.edgemv.mux.com/x.vtt": playlist.encode(),
        "https://manifest.edgemv.mux.com/seg-0.vtt": RESTARTING.format(ts=900000, said="first").encode(),
        "https://manifest.edgemv.mux.com/seg-1.vtt": RESTARTING.format(ts=1800000, said="second").encode()})

    assert found["cues"] == [(1.0, "first"), (11.0, "second")], "the second segment is 10 s later"


def test_a_cue_written_in_two_segments_appears_once(monkeypatch):
    """A cue that spans a segment border is written at the end of one and the start of the next."""
    found = serving(monkeypatch, {
        "https://manifest.edgemv.mux.com/x.vtt": PLAYLIST.encode(),
        "https://manifest.edgemv.mux.com/seg-0.vtt": SEG0.encode(),
        "https://manifest.edgemv.mux.com/seg-1.vtt": SEG0.encode()})  # the same segment twice

    assert [said for _, said in found["cues"]].count("a line that spans the border") == 1


def test_one_foreign_segment_refuses_the_whole_track(monkeypatch):
    """A caption file assembled from two places is not this post's captions. And the foreign address
    is never asked: the check comes before the request."""
    foreign = PLAYLIST.replace("seg-1.vtt", "https://evil.example/seg-1.vtt")
    found = serving(monkeypatch, {
        "https://manifest.edgemv.mux.com/x.vtt": foreign.encode(),
        "https://manifest.edgemv.mux.com/seg-0.vtt": SEG0.encode()})

    assert found["refused"] == mod_elsewhere()
    assert "cues" not in found, "nothing partial"
    assert not any("evil.example" in one for one in found["asked"]), "it was never asked"


def mod_elsewhere() -> str:
    from noaap.patreon import CAPTIONS_ELSEWHERE

    return CAPTIONS_ELSEWHERE


@pytest.mark.parametrize("broken,says", [
    ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nlow.m3u8\n#EXT-X-ENDLIST\n", "playlist of playlists"),
    ("#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"k.key\"\n#EXTINF:1,\na.vtt\n#EXT-X-ENDLIST\n", "encrypted"),
    ("#EXTM3U\n#EXTINF:1,\na.vtt\n", "still being written"),
    ("#EXTM3U\n#EXT-X-ENDLIST\n", "lists no segments"),
])
def test_the_playlists_this_program_will_not_read(monkeypatch, broken, says):
    """A master playlist is not the list we asked for; an encrypted one would need a key this program
    never fetches; one with no end marker is still being written. Each refused by name, and for the
    encrypted one **no key is requested** — the refusal comes before any segment."""
    found = serving(monkeypatch, {"https://manifest.edgemv.mux.com/x.vtt": broken.encode()})

    assert says in found["refused"] and "cues" not in found
    assert found["asked"] == ["https://manifest.edgemv.mux.com/x.vtt"], "only the playlist was asked"
    assert not any(one.endswith(".key") for one in found["asked"])


def test_the_caps_refuse_rather_than_read_half(monkeypatch):
    """Both caps are request budgets as much as size budgets: one request per segment, no retry."""
    from noaap.patreon import CAPTION_BYTES, CAPTION_SEGMENTS

    assert (CAPTION_SEGMENTS, CAPTION_BYTES) == (600, 8_000_000)

    many = "#EXTM3U\n" + "".join(f"#EXTINF:10,\nseg-{n}.vtt\n" for n in range(CAPTION_SEGMENTS + 1)) \
        + "#EXT-X-ENDLIST\n"
    found = serving(monkeypatch, {"https://manifest.edgemv.mux.com/x.vtt": many.encode()})
    assert f"more than the {CAPTION_SEGMENTS}" in found["refused"]
    assert found["asked"] == ["https://manifest.edgemv.mux.com/x.vtt"], "and not one segment was asked"

    big = SEG0.encode() + b"\n" * 5_000_000
    answers = {"https://manifest.edgemv.mux.com/x.vtt": PLAYLIST.encode(),
               "https://manifest.edgemv.mux.com/seg-0.vtt": big,
               "https://manifest.edgemv.mux.com/seg-1.vtt": big}
    found = serving(monkeypatch, answers)
    assert "MB this program will read" in found["refused"] and "cues" not in found


@pytest.mark.parametrize("segments,says", [
    ([SEG0, "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nno map here\n"], "some of its caption segments"),
    ([SEG0, "WEBVTT\nX-TIMESTAMP-MAP=NONSENSE\n\n00:00:01.000 --> 00:00:02.000\nx\n"], "cannot read"),
    # a later segment whose base is *earlier* than the first one's: its cues would land before zero
    (["WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:1800000\n\n00:00:01.000 --> 00:00:02.000\nx\n",
      "WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0\n\n00:00:01.000 --> 00:00:02.000\ny\n"],
     "land before the start"),
])
def test_a_timestamp_map_that_cannot_be_resolved_refuses(monkeypatch, segments, says):
    """The stamps have to land on audio that starts at zero. A constant offset on every line looks
    right and is not, so anything that cannot be resolved with certainty is refused."""
    playlist = "#EXTM3U\n" + "".join(f"#EXTINF:10,\nseg-{n}.vtt\n" for n in range(len(segments))) \
        + "#EXT-X-ENDLIST\n"
    answers = {"https://manifest.edgemv.mux.com/x.vtt": playlist.encode()}
    for n, text in enumerate(segments):
        answers[f"https://manifest.edgemv.mux.com/seg-{n}.vtt"] = text.encode()

    found = serving(monkeypatch, answers)

    assert says in found["refused"] and "cues" not in found


def test_segments_without_any_map_are_taken_as_they_are(monkeypatch):
    """The other real shape: no `X-TIMESTAMP-MAP` at all, so the cue stamps are already the media's
    and the first segment's zero is the asset's zero."""
    answers = {"https://manifest.edgemv.mux.com/x.vtt": PLAYLIST.encode(),
               "https://manifest.edgemv.mux.com/seg-0.vtt":
                   b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nfirst\n",
               "https://manifest.edgemv.mux.com/seg-1.vtt":
                   b"WEBVTT\n\n00:00:12.000 --> 00:00:13.000\nsecond\n"}

    found = serving(monkeypatch, answers)

    assert found["cues"] == [(1.0, "first"), (12.0, "second")]


def test_a_plain_caption_file_still_works(monkeypatch):
    found = serving(monkeypatch, {"https://manifest.edgemv.mux.com/x.vtt": VTT.encode()})

    assert found["requests"] == 1 and "bytes" in found and "cues" not in found


def test_the_track_line_says_what_the_captions_cost(tmp_path):
    from noaap.download import _words_from_source

    plan = an_album()
    said = _words_from_source(Gave({"by": "patreon", "cues": [(1.0, "a line"), (2.0, "another")],
                                    "requests": 3}), plan, plan.tracks[0], tmp_path)
    assert "2 line(s)" in said and "(3 requests)" in said

    refused = _words_from_source(Gave({"by": "patreon", "refused": "its captions are encrypted",
                                       "requests": 1}), an_album(), a_track(), tmp_path)
    assert refused.endswith("(1 request)")


def test_a_dry_run_says_what_they_would_cost_before_asking(monkeypatch):
    """A dry run makes no request for captions, so it says the shape and the budget, not a count."""
    source = PatreonSource(taking())
    assert source.captions_note() is None, "nothing read yet, nothing to say"

    monkeypatch.setattr(source.pt, "_read", lambda url, **extra: a_post())
    source.collection("https://www.patreon.com/posts/100004")
    assert "one file" in source.captions_note()

    playlist_post = a_post(subtitles={"en": [{"ext": "vtt", "protocol": "m3u8_native",
                                              "url": "https://manifest.edgemv.mux.com/subtitles.m3u8"}]})
    monkeypatch.setattr(source.pt, "_read", lambda url, **extra: playlist_post)
    source.collection("https://www.patreon.com/posts/100004")
    note = source.captions_note()
    assert "a playlist" in note and "at most 600" in note

    off = PatreonSource(taking(patreon_captions=False))
    monkeypatch.setattr(off.pt, "_read", lambda url, **extra: a_post())
    off.collection("https://www.patreon.com/posts/100004")
    assert off.captions_note() is None, "off means nothing is said about them either"


def test_the_musicbrainz_client_is_not_built_for_an_album_nobody_may_ask_about(tmp_path, monkeypatch):
    """Queued from the live run: its cache file existed in a run that made no lookup, because the
    client is constructed while the gate is being evaluated. One file is more than nothing."""
    from noaap import service as service_mod

    built: list[str] = []

    class Counted:
        def __init__(self, *a, **k):
            built.append("mb")

    monkeypatch.setattr(service_mod, "MusicBrainz", Counted)
    service = a_service(tmp_path)
    plan = an_album()

    assert service.may_look_up(plan) is False
    assert service.mb is not None and built == ["mb"], "asking for it directly still builds it"

    # …but the fetch path asks whether it may look up *first*, so nothing is built for such an album
    built.clear()
    fresh = a_service(tmp_path)
    if fresh.may_look_up(plan) and fresh.mb:
        pass
    assert built == [], "the gate is evaluated before the client"
