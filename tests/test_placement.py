"""A line is placed only when something supports it (DESIGN §9, slice 81).

The case, from the user's library: four lines of an LRCLIB entry that this cut does not sing were
pinned to 0.0, 53.2, 53.7 and 55.8 s — the first three where nobody sings at all — the whole song
after them came out ~4.8 s late, and the result said *placed 49 of 49*. Forced alignment places
everything, because that is what forced alignment is; the evidence has to come from somewhere else.

Synthetic scores and synthetic sung stretches throughout: no audio in the repository.
"""
from __future__ import annotations

import pytest

from noaap.timing import TOO_FAST, VOICED_ENOUGH, Timed, TimedLine, unplace_unsupported, voiced_share


def timed(*lines: tuple[str, float | None, float | None]) -> Timed:
    return Timed(lines=[TimedLine(text=t, start=s, end=e) for t, s, e in lines],
                 provider="local", model="wav2vec2")


SUNG = [(54.0, 60.0), (65.0, 80.0)]


def test_a_line_where_nobody_is_singing_is_not_placed():
    found = timed(("words this cut does not sing", 0.0, 0.34),
                  ("nor these", 0.46, 0.72),
                  ("but this one is sung", 55.0, 57.0))

    said = unplace_unsupported(found, SUNG)

    assert found.unplaced == [0, 1]
    assert found.lines[2].start == 55.0, "and the sung one keeps its stamps"
    assert said == ["line 1: nobody is singing there (0% of it)",
                    "line 2: nobody is singing there (0% of it)"]


def test_a_line_keeps_its_words_and_its_place_in_the_order():
    found = timed(("first", 0.0, 0.3), ("second", 55.0, 56.0), ("third", 57.0, 58.0))

    unplace_unsupported(found, SUNG)

    assert [line.text for line in found.lines] == ["first", "second", "third"]
    assert found.lines[0].start is None and found.lines[0].end is None


def test_a_line_followed_by_a_long_break_is_judged_on_its_own_second():
    """The last line before an instrumental claims a huge span; what is asked is whether anybody was
    singing when it *started*, not whether they sang through the break."""
    found = timed(("the last line before the solo", 55.0, 75.0), ("after the solo", 79.0, 79.5))

    unplace_unsupported(found, SUNG)

    assert found.unplaced == [], "both were sung when they started"


def test_the_rate_is_a_backstop_and_only_where_the_first_test_said_nothing():
    """Characters per second from one line's start to the next, measured over 128 263 line gaps of a
    real library: median 8.3, p99 25.7. A scat or a patter verse sits at 50 and below; above 60 is
    not singing."""
    assert TOO_FAST == 60.0

    found = timed(("x" * 120, 55.0, 55.4), ("the next one", 56.0, 57.0))
    said = unplace_unsupported(found, SUNG)

    assert found.unplaced == [0] and "is not singing" in said[0]

    # …and a fast line that is really sung is left alone
    fast = timed(("skibbadee bop bop", 55.0, 55.6), ("and on we go", 56.0, 57.0))
    assert unplace_unsupported(fast, SUNG) == [] and fast.unplaced == []


def test_without_a_vocal_stem_nothing_is_taken_back():
    """No measurement, no verdict: a provider that cannot say where the singing is gets the benefit."""
    found = timed(("anywhere at all", 0.0, 0.3), ("and here", 1.0, 1.2))

    assert unplace_unsupported(found, []) == []
    assert found.unplaced == []


@pytest.mark.parametrize("start,end,expected", [
    (54.0, 60.0, 1.0), (0.0, 54.0, 0.0), (52.0, 56.0, 0.5), (61.0, 64.0, 0.0),
])
def test_how_much_of_a_stretch_is_sung(start, end, expected):
    assert voiced_share(start, end, SUNG) == pytest.approx(expected, abs=0.01)


def test_the_threshold_is_the_one_the_measurement_supports():
    """Measured on the reported track: the four lines that are not in the recording scored 0.00, 0.00,
    0.09 and 0.64 of their first second sung; the 45 lines that are scored 0.41 at worst and 1.00 in
    44 of 45. A quarter separates three of the four from every genuine line — the fourth is placed
    where the singing really is, and no per-line evidence tells it from a real line."""
    assert VOICED_ENOUGH == 0.25


def test_the_scores_are_recorded_even_though_they_decide_nothing():
    """Measured on the same track: the four lines that are not sung scored 0.001 to 0.006, and four
    genuine lines scored at or below that. A gate on this number would throw real lines away, so it
    is written down beside the result and not used to judge."""
    found = timed(("sung", 55.0, 56.0), ("also sung", 57.0, 58.0))

    unplace_unsupported(found, SUNG, scores={0: 0.004, 1: 0.812})

    assert found.parameters["line_scores"] == "1:0.004,2:0.812"
    assert found.unplaced == [], "and nothing was taken back for a low score"


# -- the reported case itself, only when asked for and only with the audio in hand -------------------


@pytest.mark.skipif(not __import__("noaap.config", fromlist=["env"]).env("PLACEMENT_CASE"),
                    reason="set NOAAP_PLACEMENT_CASE=<folder with track.opus and lrclib.json> to run "
                           "the reported case against the real models")
def test_the_reported_case(tmp_path):
    """The user's own track and the entry they aligned: four lines the recording does not sing.

    **No audio and no entry live in this repository.** The folder is named by an environment variable
    and holds the copy: `track.opus` (from a copy of the library) and `lrclib.json` (the entry). What
    is asserted is what the user could hear: the opening lines the cut does not contain keep no stamp,
    every line that *is* sung keeps one, and the song starts where the singing starts.
    """
    import json
    from pathlib import Path

    from noaap import config
    from noaap.timing_local import LocalTiming

    case = Path(config.env("PLACEMENT_CASE"))
    entry = json.loads((case / "lrclib.json").read_text(encoding="utf-8"))
    lines = [line for line in (entry["plainLyrics"] or "").splitlines() if line.strip()]
    assert len(lines) >= 45

    engine = LocalTiming(device=config.env("PLACEMENT_DEVICE") or "auto", verify=False)
    timed = engine.align(case / "track.opus", lines)

    opening = [i for i in timed.unplaced if i < 4]
    assert len(opening) >= 3, f"the opening lines the cut does not sing: {timed.unplaced}"
    later = [i for i in timed.unplaced if i >= 4]
    assert not later, f"lines that are sung kept no stamp: {later}"
    first = next(line.start for line in timed.lines if line.start is not None)
    assert 50.0 <= first <= 60.0, f"the first placed line is at {first}"
    assert "unsupported" in timed.parameters and "nobody is singing" in timed.parameters["unsupported"]
