"""Placing words by listening first (DESIGN §9, slice 83).

Forced alignment places every line it is given, because that is what it is. Listening asks the other
question — *were these words said at all?* — and a line whose words are not in the transcript stays
unplaced with that as its reason. No audio and no model here: the matching is string work in the core,
and these are its cases. The measured numbers behind the threshold are in `docs/qa-catalog.md`, BV.
"""
from __future__ import annotations

import pytest

from noaap.timing import (
    HEARD_ENOUGH,
    LISTEN,
    Heard,
    HeardWord,
    heard_from,
    place_by_listening,
)


def heard(*words: tuple[str, float]) -> Heard:
    """A transcript: each word and the second it was heard at (a tenth of a second long)."""
    return Heard(words=[HeardWord(text=t, start=s, end=round(s + 0.1, 2)) for t, s in words],
                 provider="fake", model="ears")


SONG = heard(("Hoch", 10.0), ("in", 10.5), ("den", 11.0), ("Bergen", 11.5),
             ("bei", 12.0), ("Elfen", 12.5), ("und", 13.0), ("auch", 13.5), ("Zwergen", 14.0))


# -- a line that was heard, and one that was not ----------------------------------------------------


def test_a_line_is_placed_where_its_words_were_heard():
    timed = place_by_listening(["Hoch in den Bergen", "bei Elfen und auch Zwergen"], SONG)

    assert [line.start for line in timed.lines] == [10.0, 12.0]
    assert timed.lines[0].end == 11.6 and timed.unplaced == []


def test_a_line_that_was_never_heard_stays_unplaced_and_says_so():
    """The reported case in miniature: words that are not in the recording at all."""
    timed = place_by_listening(["Liebe Leute da draußen", "Hoch in den Bergen"], SONG)

    assert timed.unplaced == [0]
    assert timed.lines[1].start == 10.0, "and the line after it is placed where it belongs"
    assert timed.parameters["not_heard"] == "line 1: not heard (0 of 4 words)"


def test_the_lines_after_a_missing_one_do_not_shift():
    """An aligner glues an absent line to the first singing it finds and drags the rest along."""
    timed = place_by_listening(["not in this song at all", "nor is this one",
                                "Hoch in den Bergen", "bei Elfen und auch Zwergen"], SONG)

    assert timed.unplaced == [0, 1]
    assert [timed.lines[2].start, timed.lines[3].start] == [10.0, 12.0]


def test_a_line_half_of_which_was_heard_is_placed_and_less_is_not():
    two_of_four = place_by_listening(["Hoch in other words"], SONG)
    one_of_four = place_by_listening(["Hoch some other words"], SONG)

    assert two_of_four.unplaced == [] and two_of_four.lines[0].start == 10.0
    assert one_of_four.unplaced == [0]
    assert one_of_four.parameters["not_heard"] == "line 1: not heard (1 of 4 words)"


def test_a_word_the_transcriber_dropped_inside_a_line_does_not_lose_it():
    """`skip` is for this: a transcriber mishears a word in the middle of a line it otherwise has."""
    timed = place_by_listening(["Hoch in den XXXX Bergen bei"], SONG)

    assert timed.unplaced == [] and timed.lines[0].start == 10.0


def test_words_are_matched_without_case_or_punctuation():
    timed = place_by_listening(["HOCH, in! den... Bergen?"], SONG)

    assert timed.lines[0].start == 10.0


# -- repeated lines, in order -----------------------------------------------------------------------


def test_a_chorus_sung_three_times_is_matched_in_order():
    song = heard(("chorus", 10.0), ("line", 10.5),
                 ("a", 20.0), ("verse", 20.5),
                 ("chorus", 30.0), ("line", 30.5),
                 ("another", 40.0), ("verse", 40.5),
                 ("chorus", 50.0), ("line", 50.5))

    timed = place_by_listening(["chorus line", "a verse", "chorus line", "another verse",
                                "chorus line"], song)

    assert [line.start for line in timed.lines] == [10.0, 20.0, 30.0, 40.0, 50.0]


def test_a_repeated_line_the_singer_left_out_does_not_take_the_next_one_s_place():
    """Three in the words, twice in the recording: one of them has to come back unplaced."""
    song = heard(("chorus", 10.0), ("line", 10.5), ("a", 20.0), ("verse", 20.5),
                 ("chorus", 30.0), ("line", 30.5))

    timed = place_by_listening(["chorus line", "a verse", "chorus line", "chorus line"], song)

    assert len(timed.unplaced) == 1
    assert [line.start for line in timed.lines][:3] == [10.0, 20.0, 30.0]


# -- what cannot be matched at all ------------------------------------------------------------------


def test_a_transcript_with_no_times_places_nothing():
    """A provider that transcribes without word times cannot place anything, and says which lines."""
    wordless = Heard(words=[HeardWord(text="Hoch"), HeardWord(text="in")], provider="fake", model="ears")

    timed = place_by_listening(["Hoch in den Bergen"], wordless)

    assert timed.unplaced == [0] and timed.parameters["heard_words"] == "0"


def test_an_empty_transcript_leaves_every_line_alone():
    timed = place_by_listening(["Hoch in den Bergen"], heard())

    assert timed.unplaced == [0]


def test_a_line_with_no_words_to_listen_for():
    timed = place_by_listening(["…", "Hoch in den Bergen"], SONG)

    assert timed.unplaced == [0]
    assert "no words to listen for" in timed.parameters["not_heard"]


def test_no_lines_at_all_is_not_an_error():
    timed = place_by_listening([], SONG)

    assert timed.lines == [] and timed.unplaced == []


# -- what the answer says about itself (item 3) ------------------------------------------------------


def test_the_answer_names_the_provider_and_the_method():
    timed = place_by_listening(["Hoch in den Bergen"], SONG)

    assert timed.method == LISTEN
    assert timed.by == "fake/ears (listen)", "the same provider aligns and listens: which one is it"
    assert timed.parameters["heard_words"] == "9"
    assert timed.parameters["enough"] == f"{HEARD_ENOUGH:g}"


def test_the_threshold_can_be_moved_for_a_measurement():
    strict = place_by_listening(["Hoch in other words"], SONG, enough=0.9)

    assert strict.unplaced == [0] and strict.parameters["enough"] == "0.9"


# -- the words say what language they are in, or say nothing --------------------------------------


def test_the_words_name_their_language_when_they_are_sure():
    from noaap.timing import language_hint

    assert language_hint(["Hoch in den Bergen", "bei Elfen und auch Zwergen, die nicht mehr da sind"]) == "de"
    assert language_hint(["We remember, no surrender", "and they held out for long with all of it"]) == "en"


def test_a_lyric_in_a_third_language_is_told_nothing():
    """A transcriber told the wrong language writes down nonsense; told nothing, it detects."""
    from noaap.timing import language_hint

    assert language_hint(["Kalevalan kansa laulaa", "kaikki yhdessä"]) is None
    assert language_hint(["Cobras Fumantes", "eterna é sua vitória"]) is None
    assert language_hint([]) is None
    assert language_hint(["la la la"]) is None, "and neither does a line with no stopwords at all"


def test_a_mixed_lyric_needs_a_clear_winner():
    from noaap.timing import language_hint

    assert language_hint(["the and you your we they not is are was",
                          "der die das und ich du wir ihr sie nicht"]) is None


# -- a provider's word list, whatever it calls its fields --------------------------------------------


def test_every_shape_of_word_list_is_read():
    mine = heard_from([{"word": " Hoch ", "start": 1.0, "end": 1.5},
                       {"text": "in", "start": 1.567, "end": 2.0},
                       {"punctuated_word": "den,", "start": 2.0},
                       {"word": "  ", "start": 3.0},
                       {"word": "Bergen"}],
                      "deepgram", "nova-3", parameters={"language": "multi"})

    assert [w.text for w in mine.words] == ["Hoch", "in", "den,", "Bergen"]
    assert mine.words[1].start == 1.57, "rounded to the hundredth, like every other stamp"
    assert [w.text for w in mine.timed] == ["Hoch", "in", "den,"], "a word with no time places nothing"
    assert mine.parameters == {"language": "multi"} and mine.model == "nova-3"


def test_a_provider_that_answers_with_nothing():
    assert heard_from([], "deepgram", "nova-3").words == []


# -- the service and the door (items 1, 3 and 6) -----------------------------------------------------


def a_library(tmp_path):
    """The smallest library a service can be pointed at."""
    from noaap.download import save_plan
    from noaap.models import AlbumPlan, PlanTrack

    album_dir = tmp_path / "Somebody" / "An Album"
    album_dir.mkdir(parents=True)
    (album_dir / "01 A Song.opus").write_bytes(b"not really audio")
    plan = AlbumPlan(source_url="https://example.invalid/a", source_id="an-album", kind="album",
                     album="An Album", albumartist="Somebody", year=None, cover_url=None,
                     folder="Somebody/An Album",
                     tracks=[PlanTrack(video_id="v1", number=1, provenance={}, artist="Somebody",
                                       title="A Song", filename="01 A Song.opus", state="done",
                                       file_length=120.0)])
    save_plan(plan, album_dir)
    return album_dir, plan


def a_service(tmp_path):
    from noaap.config import Config
    from noaap.service import Service

    said: list[str] = []
    cfg = Config()
    cfg.timing_provider = "local"
    return Service(cfg, tmp_path, log=said.append), said


class Ears:
    """A provider that transcribes with word times, and refuses to align."""

    name = "ears"

    def capabilities(self):
        return frozenset({"transcribe", LISTEN})

    def heard(self, audio, *, language=None, check=None):
        self.audio = audio
        mine = heard(("Hoch", 10.0), ("in", 10.5), ("den", 11.0), ("Bergen", 11.5))
        mine.provider, mine.model = self.name, "big-model"
        return mine

    def align(self, audio, lines, *, language=None, check=None):
        raise AssertionError("listening must never call the aligner")


def test_the_service_places_by_listening_and_marks_it(monkeypatch, tmp_path):
    monkeypatch.setattr("noaap.timing.provider", lambda _cfg, _what="": Ears())
    album_dir, plan = a_library(tmp_path)
    service, said = a_service(tmp_path)

    out = service.align_lyrics(plan.source_id, plan.tracks[0].video_id,
                               "Hoch in den Bergen\nnot in this song", method=LISTEN)

    assert out["method"] == LISTEN and out["placed"] == 1 and out["lines"] == 2
    assert out["by"] == "ears/big-model (listen)", "the provider, the model and the method"
    assert out["timed"]["lines"][0]["start"] == 10.0 and out["timed"]["lines"][1]["start"] is None
    assert any("listening to" in line for line in said)
    assert any("not heard" in line for line in said), "the job log says which lines and why"


def test_the_service_refuses_a_method_it_does_not_have(monkeypatch, tmp_path):
    monkeypatch.setattr("noaap.timing.provider", lambda _cfg, _what="": Ears())
    album_dir, plan = a_library(tmp_path)
    service, _ = a_service(tmp_path)

    with pytest.raises(ValueError, match="unknown way of placing words"):
        service.align_lyrics(plan.source_id, plan.tracks[0].video_id, "Hoch", method="guess")


# -- the door (items 1 and 6) -----------------------------------------------------------------------


def an_app(tmp_path, **settings):
    from noaap.config import Config
    from noaap.web import App

    cfg = Config()
    for name, value in settings.items():
        setattr(cfg, name, value)
    return App(cfg, tmp_path)


def test_the_door_refuses_a_method_it_has_never_heard_of(tmp_path, monkeypatch):
    monkeypatch.setattr("noaap.timing.provider", lambda _cfg, _what="": Ears())
    album_dir, plan = a_library(tmp_path)
    app = an_app(tmp_path, timing_provider="local")

    with pytest.raises(ValueError, match="unknown way of placing words"):
        app.submit("align", {"id": plan.source_id, "video_id": "v1", "text": "a line",
                             "method": "vibes"})


def test_the_door_refuses_listening_where_no_provider_can(tmp_path, monkeypatch):
    """The aligner is installed and listening is not: the page is told which one is missing."""
    class OnlyAligns(Ears):
        def capabilities(self):
            return frozenset({"align"})

    monkeypatch.setattr("noaap.timing.provider", lambda _cfg, _what="": OnlyAligns())
    album_dir, plan = a_library(tmp_path)
    app = an_app(tmp_path, timing_provider="local")

    with pytest.raises(ValueError, match="listen for the words"):
        app.submit("align", {"id": plan.source_id, "video_id": "v1", "text": "a line",
                             "method": LISTEN})
    # and the one that is installed is still offered
    assert app.submit("align", {"id": plan.source_id, "video_id": "v1", "text": "a line"}).kind == "align"


def test_a_job_that_listens_says_so_in_its_label(tmp_path, monkeypatch):
    monkeypatch.setattr("noaap.timing.provider", lambda _cfg, _what="": Ears())
    album_dir, plan = a_library(tmp_path)
    app = an_app(tmp_path, timing_provider="local")

    job = app.submit("align", {"id": plan.source_id, "video_id": "v1", "text": "a line",
                               "method": LISTEN})

    assert job.label.startswith("Listen for the words of")


def test_a_private_album_s_audio_is_not_sent_off_the_machine_to_be_heard(tmp_path, monkeypatch):
    """Item 6: the audio switch of slice 75 applies to listening exactly as it does to aligning —
    and listening is asked of the *drafting* slot, which is where a vendor usually sits."""
    from noaap.web import PRIVATE_AUDIO

    album_dir, plan = a_library(tmp_path)
    plan.provider = "patreon"
    plan.send_audio = False
    from noaap.download import save_plan

    save_plan(plan, album_dir)
    app = an_app(tmp_path, timing_provider="local", timing_draft_provider="deepgram",
                 timing_deepgram_key="k")

    with pytest.raises(ValueError, match="paid its creator for"):
        app.submit("align", {"id": plan.source_id, "video_id": "v1", "text": "a line",
                             "method": LISTEN})
    assert "not sent to a timing provider" in PRIVATE_AUDIO


# -- the reported case, against the real model, only when asked for ---------------------------------


@pytest.mark.skipif(not __import__("noaap.config", fromlist=["env"]).env("LISTEN_CASE"),
                    reason="set NOAAP_LISTEN_CASE=<folder with track.opus and lrclib.json> to listen "
                           "to the reported case with the real model")
def test_the_reported_case_is_not_heard(tmp_path):
    """The four opening lines of the entry are not in this cut, and listening is what can say so.

    **No audio and no entry live in this repository.** The folder is named by an environment variable.
    What is asserted is the thing forced alignment cannot do (slice 81 takes back three of the four,
    and the fourth is glued to the first real singing): all four come back unplaced, because none of
    their words are in the transcript — and the lines that *are* sung are not thrown away wholesale.
    """
    import json
    from pathlib import Path

    from noaap import config
    from noaap.timing import language_hint
    from noaap.timing_local import LocalTiming

    case = Path(config.env("LISTEN_CASE"))
    entry = json.loads((case / "lrclib.json").read_text(encoding="utf-8"))
    lines = [line for line in (entry["plainLyrics"] or "").splitlines() if line.strip()]
    assert len(lines) >= 45

    engine = LocalTiming(device=config.env("LISTEN_DEVICE") or "auto", verify=False)
    mine = engine.heard(case / "track.opus", language=language_hint(lines))
    timed = place_by_listening(lines, mine)

    assert [i for i in timed.unplaced if i < 4] == [0, 1, 2, 3], (
        f"the four opening lines are not in this recording: {timed.unplaced}")
    placed = len(lines) - len(timed.unplaced)
    assert placed >= 25, f"only {placed} of {len(lines)} lines were heard at all"
    # and the stamps it does give run forwards, in the order the lines are written
    stamps = [line.start for line in timed.lines if line.start is not None]
    assert stamps == sorted(stamps)
    assert timed.parameters["not_heard"].startswith("line 1: not heard (0 of")
