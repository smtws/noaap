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
    assert "not a WebVTT file" in decides(monkeypatch, a_post(), body=b"1\n00:00:01,0 --> 00:00:02,0\nsrt\n")["refused"]


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
