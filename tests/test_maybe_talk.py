"""Where a draft stops being the song (DESIGN §9, slice 159; R-535).

The user: a draft is right for about the first half and *"the rest of it is bullshit"*. Measured on
three real Deepgram replies (R-532/R-534, kept in `tests/data/deepgram-words.json`): **nothing is
truncated** — the audio we send is the full track, Deepgram's own `metadata.duration` matches it,
and the word times are monotone and land where the singing is. On the **live** track the transcriber
is simply right: after the song it writes down the singer thanking the audience and introducing the
band, and the draft offers that as lyrics.

Two signals were measured and refused:
* a vocal RMS (`sung_stretches`) called 231.7 s of 301.3 s singing and put **all 51** stage-talk
  words inside a stretch — a voice track rises for speech too;
* a known length cannot bound the song: live versions in the user's library run from **0.57 to
  1.37** of their studio siblings, and these live albums are not matched at all, so there is no
  MusicBrainz length to use.

What separates is the silence in front of it: the longest gap between two words is 30.6 s and 53.6 s
on the two studio tracks, both mid-song, and **144.9 s** on the live one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noaap.timing import (
    TimedLine,
    lines_from_words,
    maybe_talk,
    said_after_silence,
)

WORDS = json.loads((Path(__file__).parent / "data" / "deepgram-words.json").read_text())


def drafted(tag: str) -> list[TimedLine]:
    return lines_from_words(WORDS[tag])


# -- the three real replies ----------------------------------------------------------------------


def test_the_live_track_marks_the_stage_talk():
    """`Schandmaul/Anderswelt (Live)`: the lyric stops at 109.9 s, and at 254.8 s the singer says
    "Dankeschön" and starts introducing the band."""
    lines = drafted("anderswelt-live")
    at = maybe_talk(lines)

    assert at is not None
    assert lines[at].start == pytest.approx(254.79, abs=0.01)
    assert lines[-1].end == pytest.approx(294.37, abs=0.01)
    assert len(lines) - at == 8, "the block runs to the end of the draft"
    assert lines[at].text.startswith("Dankeschön")


def test_the_studio_tracks_mark_nothing():
    """`Die Braut` has a 30.6 s instrumental in the middle and `Mondfeuer` a 53.6 s one — both are
    the song, and a draft that marked them would be crying wolf on every album."""
    assert maybe_talk(drafted("die-braut")) is None
    assert maybe_talk(drafted("mondfeuer")) is None


def test_the_marker_says_how_long_the_silence_was():
    lines = drafted("anderswelt-live")
    at = maybe_talk(lines)

    said = said_after_silence(lines, at)
    assert said == "… after 145 s of silence — maybe talk, not lyrics"


# -- the rule itself -----------------------------------------------------------------------------


def a_draft(*stamps: tuple[float, float]) -> list[TimedLine]:
    return [TimedLine(text=f"line {n}", start=a, end=b) for n, (a, b) in enumerate(stamps, 1)]


def test_a_minute_of_silence_is_not_enough_on_its_own():
    """Both bars have to be cleared. A song whose own pauses are half a minute long is a song with
    long instrumentals, not a song that ended."""
    sparse = a_draft((0, 5), (40, 45), (80, 85), (150, 155))
    assert maybe_talk(sparse) is None, "61 s against a 35 s median pause is 1.7×, not 3×"


def test_three_times_the_usual_pause_is_not_enough_either():
    """And a track of quick lines does not get marked at its first four-second rest."""
    quick = a_draft((0, 2), (3, 5), (6, 8), (20, 22))
    assert maybe_talk(quick) is None, "12 s clears 3× but is nowhere near a minute"


def test_both_bars_cleared_marks_the_block():
    lines = a_draft((0, 2), (3, 5), (6, 8), (120, 122), (123, 125))
    at = maybe_talk(lines)
    assert at == 3
    assert len(lines) - at == 2


def test_the_first_such_silence_starts_the_block():
    """Not the longest: once the song is over, everything after it is suspect. Enough short pauses
    here to keep the median where a song's is — two long ones alone would drag it up and then
    neither of them clears three times it, which is the rule working, not failing."""
    lines = a_draft((0, 2), (3, 5), (6, 8), (9, 11), (12, 14), (15, 17),
                    (100, 102), (103, 105), (300, 302))
    assert maybe_talk(lines) == 6
    assert len(lines) - 6 == 3


def test_a_draft_of_two_lines_is_never_marked():
    """With one pause there is no "usual pause" to compare against."""
    assert maybe_talk(a_draft((0, 2), (200, 202))) is None


def test_lines_with_no_times_are_ignored():
    lines = [TimedLine(text="no clock"), *a_draft((0, 2), (3, 5), (6, 8), (120, 122))]
    assert maybe_talk(lines) == 4


def test_the_gap_markers_of_slice_45_are_not_counted_as_lines():
    """`with_gaps` has already put "… (N s without words)" between the lines; those are not words
    anybody sang and must not become the block's first line or shift its count."""
    from noaap.timing import with_gaps

    lines = with_gaps(a_draft((0, 2), (3, 5), (6, 8), (120, 122), (123, 125)), 130)
    at = maybe_talk(lines)

    assert at is not None
    assert lines[at].start == 120, lines[at].text
    assert not lines[at].text.startswith("…")
