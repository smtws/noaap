"""Turning a transcript into lines somebody can sing along to (DESIGN.md §9, slice 45).

From the user's own test: Deepgram drafted eleven lines for a 3:25 song, one of them a whole verse,
with a minute of silence in the middle that nothing on the screen mentioned. These are the rules
that came out of it, and the shapes they have to survive.
"""

from pathlib import Path

import pytest
from test_incremental import FakeYouTube, opus_template, vol1

from ytalbum.config import Config
from ytalbum.download import load_plan, run, save_plan
from ytalbum.plan import build_plan
from ytalbum.service import Service
from ytalbum.timing import TimedLine, coverage, lines_from_words, with_gaps


@pytest.fixture
def album(tmp_path, opus_template):
    yt = FakeYouTube(opus_template)
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    return album_dir, load_plan(album_dir), yt


def words(*spec: tuple[str, float, float]) -> list[dict[str, object]]:
    return [{"word": w, "start": a, "end": b} for w, a, b in spec]


# -- where a line ends ---------------------------------------------------------------------------


def test_a_pause_ends_a_line_even_in_the_middle_of_a_sentence():
    got = lines_from_words(words(("Im", 10.0, 10.3), ("Jahr", 10.35, 10.8),
                                 ("siebzehn", 12.0, 12.6), ("achtzehn", 12.65, 13.2)))
    # 1.2 s between "Jahr" and "siebzehn" is a line break in a song, whatever the punctuation says
    assert [line.text for line in got] == ["Im Jahr", "siebzehn achtzehn"]


def test_punctuation_still_ends_a_line_where_there_is_no_pause():
    got = lines_from_words(words(("Ein", 1.0, 1.2), ("Schuss.", 1.25, 1.6), ("Die", 1.7, 1.9)))
    assert [line.text for line in got] == ["Ein Schuss.", "Die"]


def test_a_line_may_not_run_longer_than_a_breath():
    # a singer holding a phrase with no gap over it: the cap is what stops a verse becoming a line
    long = [("word", 10.0 + i * 0.5, 10.4 + i * 0.5) for i in range(40)]
    got = lines_from_words(words(*long), seconds=8.0, most=99)
    assert len(got) > 1
    assert all((line.end or 0) - (line.start or 0) <= 9.0 for line in got)


def test_a_line_may_not_hold_more_words_than_a_line_holds():
    many = [("word", 10.0 + i * 0.05, 10.04 + i * 0.05) for i in range(30)]
    got = lines_from_words(words(*many), most=12)
    assert max(len(line.text.split()) for line in got) == 12


def test_the_users_verse_becomes_lines_again():
    """The actual failure: one 'line' holding three sung lines because of one full stop."""
    verse = words(
        ("Im", 122.9, 123.2), ("Jahr", 123.3, 123.8), ("siebzehn", 123.9, 124.4),
        ("achtzehn", 125.8, 126.4), ("ein", 126.5, 126.7), ("grauer", 126.8, 127.3),
        ("November", 128.9, 129.6), ("lag", 129.7, 130.0), ("Ocracoke", 130.1, 130.9),
        ("Island", 132.5, 133.1), ("auf", 133.2, 133.4), ("Leh.", 133.5, 134.0))
    got = lines_from_words(verse)
    assert len(got) == 4                       # four phrases, four pauses, four lines
    assert got[0].text == "Im Jahr siebzehn"
    assert got[-1].text == "Island auf Leh."


def test_words_without_times_do_not_break_the_walk():
    got = lines_from_words([{"word": "one"}, {"word": "two"}, {"word": "three."}])
    assert [line.text for line in got] == ["one two three."]
    assert got[0].start is None


# -- where the machine heard nothing ---------------------------------------------------------------


def test_a_long_silence_becomes_a_line_that_says_so():
    lines = [TimedLine("first line", 10.0, 14.0), TimedLine("much later", 76.0, 80.0)]
    got = with_gaps(lines, length=205.0)
    assert [line.text for line in got] == [
        "first line", "… (62 s without words)", "much later", "… (125 s without words)"]
    assert got[1].start == 14.0                # stamped where the silence starts, not where it ends


def test_short_gaps_are_just_music():
    lines = [TimedLine("a", 10.0, 14.0), TimedLine("b", 18.0, 20.0)]
    assert [line.text for line in with_gaps(lines)] == ["a", "b"]


def test_the_end_of_the_track_counts_too_and_only_with_a_length():
    lines = [TimedLine("a", 10.0, 14.0)]
    assert len(with_gaps(lines)) == 1                       # nobody said how long the song is
    assert len(with_gaps(lines, length=200.0)) == 2


# -- what the notice may claim ----------------------------------------------------------------------


def test_coverage_counts_the_seconds_with_words_and_the_holes():
    lines = with_gaps([TimedLine("a", 10.0, 24.0), TimedLine("b", 76.0, 91.0)], length=205.0)
    got = coverage(lines, 205.0)
    assert got["covered"] == "29.0"            # 14 s + 15 s of words, not 205
    assert got["gaps"] == "2" and got["length"] == "205.0"


def test_the_markers_are_not_counted_as_words():
    with_marker = [TimedLine("a", 10.0, 24.0), TimedLine("… (52 s without words)", 24.0)]
    assert coverage(with_marker, 205.0)["covered"] == "14.0"


def test_a_draft_that_heard_nothing_says_nothing():
    assert coverage([], 205.0) == {"covered": "0.0", "gaps": "0", "gap_longer_than": "6", "length": "205.0"}


@pytest.mark.parametrize("gap", [0.3, 0.6, 1.2])
def test_the_pause_that_breaks_a_line_is_a_setting(gap):
    spec = words(("one", 1.0, 1.2), ("two", 1.2 + gap, 1.4 + gap))
    assert len(lines_from_words(spec, gap=0.5)) == (1 if gap < 0.5 else 2)


# -- what the transcriber listens to (§9, slice 45) ------------------------------------------------------


def test_a_draft_listens_to_the_separated_voice_when_there_is_one(album, tmp_path, monkeypatch):
    """Measured on the user's own song: the voice doubles what a vendor hears (catalog AF)."""
    import ytalbum.service as service_mod
    from ytalbum.timing import Timed, TimedLine

    album_dir, plan, yt = album
    heard: list[str] = []

    class Ears:
        name, capabilities = "fake", lambda self: frozenset({"transcribe"})

        def transcribe(self, audio, **_kw):
            heard.append(audio.name)
            return Timed(lines=[TimedLine("a line", 1.0, 3.0)], provider="fake", model="ears")

    def separate(audio, into, log=None, check=None):
        into.write_bytes(b"a voice")
        return into

    monkeypatch.setattr(service_mod, "timing_provider", lambda _cfg, _what="": Ears())
    monkeypatch.setattr("ytalbum.timing_local.separated_voice", separate)
    service = Service(Config(), tmp_path, yt=yt, log=lambda s: None)
    track = plan.tracks[0]
    track.lyrics = "none"
    save_plan(plan, album_dir)

    got = service.draft_lyrics(plan.source_id, track.video_id)
    assert heard == ["voice.wav"]                       # not the mixed file
    assert got["timed"]["parameters"]["heard"] == "the separated voice"


def test_and_falls_back_to_the_mixed_track_when_there_is_no_separator(album, tmp_path, monkeypatch):
    import ytalbum.service as service_mod
    from ytalbum.timing import Timed, TimedLine

    album_dir, plan, yt = album
    heard: list[str] = []

    class Ears:
        name, capabilities = "fake", lambda self: frozenset({"transcribe"})

        def transcribe(self, audio, **_kw):
            heard.append(audio.name)
            return Timed(lines=[TimedLine("a line", 1.0, 3.0)], provider="fake", model="ears")

    monkeypatch.setattr(service_mod, "timing_provider", lambda _cfg, _what="": Ears())
    monkeypatch.setattr("ytalbum.timing_local.separated_voice", lambda *a, **k: None)
    service = Service(Config(), tmp_path, yt=yt, log=lambda s: None)
    track = plan.tracks[0]
    track.lyrics = "none"
    save_plan(plan, album_dir)

    got = service.draft_lyrics(plan.source_id, track.video_id)
    assert heard[0].endswith(".opus")                   # the track itself, as before
    assert got["timed"]["parameters"]["heard"] == "the mixed track"


def test_the_real_response_that_started_this(audio_length=218.061):
    """The shape that came back for the user's own track, trimmed to the hole in the middle.

    Eleven lines for a 3:25 song, one of them a whole verse, and a minute of silence nothing
    mentioned — that was the report. This is what the same words give now.
    """
    import json

    body = json.loads((Path(__file__).parent / "fixtures" / "deepgram_listen_song.json").read_text())
    words = body["results"]["channels"][0]["alternatives"][0]["words"]
    lines = with_gaps(lines_from_words(words), audio_length)
    assert [line.text for line in lines] == [
        "zwanzig Hiebe, die brachten ihn schließlich zu Fall.",
        "… (57 s without words)",
        "Mehr Teufel als Mann.",
        "… (12 s without words)",
    ]
    assert lines[1].start == 147.72                   # where the singing stopped, not where it resumed
    got = coverage(lines, audio_length)
    assert got["covered"] == "5.4" and got["gaps"] == "2"   # 5.4 s of words in a 3:38 track
