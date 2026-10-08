"""A transcriber stuck in a loop (DESIGN §9, slice 160; R-540).

Measured on five of the user's tracks, scored against `/usr/share/dict/ngerman` and by line
repetition. Without voice detection the local model fills every stretch that holds no voice:
`Lacrimosa/Mondfeuer`, fifteen minutes and mostly instrumental, came back as **186 lines of
"Thank you." and nothing else** — 100% of its words not German. A live track repeated one invented
line 88 times, `Die Braut` one 23 times, the Latin track one 63 times.

`vad_filter` is what fixes it (the provider is handed only the parts with a voice) and
`condition_on_previous_text=False` stops a repetition that has begun from feeding itself. After
both: Mondfeuer is 31 lines of its own words with **no line twice**, the live track's worst repeat
is 6, and `Heavysaurus/Dinos spielen` says **"Dinos" ten times** where it had said "Tinos" eight.
This guard is the net under that, for whatever still gets through.
"""

from __future__ import annotations

from noaap.timing import LOOP_TIMES, TimedLine, trim_loops


def a_draft(*texts: str) -> list[TimedLine]:
    return [TimedLine(text=t, start=float(n), end=float(n) + 1) for n, t in enumerate(texts)]


def test_a_runaway_repetition_is_trimmed():
    """`Mondfeuer`'s own failure: 186 lines, every one of them the same."""
    lines = a_draft(*["Thank you."] * 186)

    kept, dropped = trim_loops(lines)

    assert len(kept) == LOOP_TIMES
    assert dropped == 186 - LOOP_TIMES
    assert all(line.text == "Thank you." for line in kept), "and it is still visible what it said"


def test_a_chorus_is_not_a_loop():
    """The worst honest repeat measured is 8 — `Die Braut`'s refrain. The bar sits above it."""
    lines = a_draft(*["Meine Braut sollst du sein,"] * 8)

    kept, dropped = trim_loops(lines)

    assert dropped == 0
    assert kept == lines


def test_the_first_ones_are_the_ones_kept():
    """So a draft reads as a song that repeats, not as a hole where something was removed."""
    lines = a_draft(*[f"line {n}" for n in range(3)], *["over and over"] * 30)

    kept, _ = trim_loops(lines)

    assert [line.text for line in kept[:3]] == ["line 0", "line 1", "line 2"]
    assert [line.start for line in kept[3:]] == [float(n) for n in range(3, 3 + LOOP_TIMES)]


def test_only_the_line_that_loops_is_touched():
    lines = a_draft("one", *["loop"] * 40, "two", *["loop"] * 5, "three")

    kept, dropped = trim_loops(lines)

    assert [line.text for line in kept if line.text != "loop"] == ["one", "two", "three"]
    assert sum(1 for line in kept if line.text == "loop") == LOOP_TIMES
    assert dropped == 45 - LOOP_TIMES


def test_two_different_loops_are_both_trimmed():
    lines = a_draft(*["first"] * 20, *["second"] * 20)

    kept, dropped = trim_loops(lines)

    assert sum(1 for line in kept if line.text == "first") == LOOP_TIMES
    assert sum(1 for line in kept if line.text == "second") == LOOP_TIMES
    assert dropped == 2 * (20 - LOOP_TIMES)


def test_whitespace_does_not_hide_a_loop():
    lines = a_draft(*[" Thank you. ", "Thank you."] * 20)
    kept, dropped = trim_loops(lines)
    assert dropped == 40 - LOOP_TIMES


def test_a_draft_with_nothing_repeated_comes_back_as_it_was():
    lines = a_draft("a", "b", "c")
    kept, dropped = trim_loops(lines)
    assert (kept, dropped) == (lines, 0)


def test_an_empty_draft_is_no_trouble():
    assert trim_loops([]) == ([], 0)
