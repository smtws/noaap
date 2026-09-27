"""A permanent corpus of what the measurements decided, so that a later change has to face them.

Every case here asserts a **semantic outcome** — "the second method is judged lost and the first is
not" — and never a number, because the numbers are already recorded and a test that repeats them
only says the recording was copied correctly. Where a threshold decides, the case also asserts that
the threshold could **move within the gap the measurement left** and give the same answers, so a
retune inside the gap passes and one outside it fails for a stated reason.

The other half is the counterexamples. Four heuristics were proposed during P27, P33 and P35 and
disproved by measurement; each has a case here that must keep failing it, so that proposing it again
means meeting the numbers that killed it first. That is the rule of this file: **a new signal has to
beat every counterexample before it can decide anything.**

No model runs here and no audio is read. The fixtures are the recordings (`docs/regression.md`).
The end-to-end companion is `tests/test_corpus_audio.py`, which is opt-in and needs real tracks.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ytalbum.lyrics import FIT_SPAN, FIT_WIDE, NOFIT_UNPLACED, fit_verdict, nominated
from ytalbum.timing import (
    LOST_PILED,
    LOST_SPAN,
    Signals,
    Timed,
    TimedLine,
    lost,
    verified,
    which_lost,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["rows"]


SPANS = load("verify_spans")            # P33: both methods' coverage of the singing, 18 tracks
TRACKS = load("verify_tracks")          # P27: what the check did, 16 tracks
SIDECAR = load("verify_sidecar")        # P27: each method against a human's own stamps, 7 tracks
ARMS = load("separation_arms")          # the spike: each aligner on the mix and on the voice
SIGNALS = load("verify_signals")        # P33: every candidate signal, 16 tracks x 3 methods
NEAR = json.loads((FIXTURES / "lrclib_near_misses.json").read_text(encoding="utf-8"))["candidates"]


def by_title(rows: list[dict[str, Any]], needle: str) -> dict[str, Any]:
    """The row whose track name contains `needle`.

    A title may appear more than once — the library holds several tracks on two albums each, and the
    same recording was measured under both. Duplicates are allowed only while they agree about
    everything, because a case that quietly picked one of two different measurements would be
    reporting a coin toss.
    """
    hits = [r for r in rows if needle in (r.get("track") or r.get("title") or "")]
    assert hits, f"{needle!r} matched nothing"
    first = hits[0]
    for other in hits[1:]:
        for key, value in first.items():
            got = other.get(key)
            if isinstance(value, float) and isinstance(got, float):
                # the aligner is not bit-deterministic: Blackbeard was measured twice and its span
                # came back 0.968 and 0.966. Jitter of that size is allowed and must not reach a
                # verdict; anything larger means two different measurements wearing one name.
                assert abs(value - got) <= 0.01, f"{needle!r}: {key} differs, {value} vs {got}"
            else:
                assert value == got, f"{needle!r}: {key} differs, {value!r} vs {got!r}"
    return first


def signals_for(row: dict[str, Any], method: str, placed: int = 50) -> Signals:
    """A `Signals` as the recorded run produced for that method, from the recorded shares."""
    share = row["whisper_piled"] if method == "whisper" else 0.0
    return Signals(placed=placed, total=placed, span=row[f"{method}_span"],
                   piled=round(share * placed))


# ── the whole-track cases: one method is elsewhere, and the stamps are still worth keeping ──

def test_the_five_whole_track_cases_keep_every_stamp() -> None:
    """P27 measured the original rule backwards: it discarded a correct alignment five times."""
    flagged = [t for t in TRACKS if "kept" in t["verdict"]]
    assert {t["track"].split("—")[1].strip().rsplit(" (", 1)[0] for t in flagged} == {
        "Argent", "A Love That Never Dies", "Armata Strigoi", "A Lifetime of War", "Azrael"}

    for track in flagged:
        lines = track["lines"]
        # the shape the recording describes: the two methods are a whole song apart on most lines
        primary = Timed([TimedLine(f"line {i}", float(i) * 2) for i in range(lines)], "local", "ctc")
        second = Timed([TimedLine(f"line {i}", float(i) * 2 + track["median_apart"])
                        for i in range(lines)], "local", "large-v3")
        out = verified(primary, second)
        placed = [line for line in out.lines if line.start is not None]
        assert len(placed) == lines, f"{track['track']}: {lines - len(placed)} stamps were discarded"


def test_on_every_whole_track_case_the_primary_was_the_accurate_one() -> None:
    """Which is why the rule keeps stamps instead of dropping them (`docs/qa-catalog.md`, Y)."""
    flagged = [s for s in SIDECAR if "kept" in s["what_9_38_did"]]
    assert len(flagged) == 5
    for row in flagged:
        assert row["primary_vs_sidecar"] < row["second_vs_sidecar"], row["track"]
        # and not marginally: the primary is within jitter, the second is a song away
        assert row["primary_vs_sidecar"] < 1.0 < 20.0 < row["second_vs_sidecar"], row["track"]


# ── which method lost the song ───────────────────────────────────────────────────────────

def test_on_argent_the_second_method_is_judged_lost_and_the_first_is_not() -> None:
    row = by_title(SPANS, "Argent")
    loser, why = which_lost(signals_for(row, "ctc"), signals_for(row, "whisper"))
    assert loser == "second"
    assert "cover only" in why
    assert not lost(signals_for(row, "ctc"))


def test_on_bastard_of_asgard_neither_is_judged_lost_from_span_alone() -> None:
    """The one track where the CTC pass was the worse of the two. The signal says so by saying
    nothing, which is the discipline: it never guesses when it cannot tell."""
    row = by_title(SPANS, "Bastard of Asgard")
    first, second = signals_for(row, "ctc"), signals_for(row, "whisper")
    assert not lost(first) and not lost(second)
    assert which_lost(first, second) == ("", "")


def test_the_five_lost_methods_are_named_and_the_thirteen_others_are_not() -> None:
    named = {row["track"] for row in SPANS
             if which_lost(signals_for(row, "ctc"), signals_for(row, "whisper"))[0]}
    assert len(named) == 5, sorted(named)
    assert all("lost" in by_title(SPANS, t.split("—")[1].strip())["verdict"] for t in named)
    # including the two tracks held out until after the thresholds were fixed
    for row in [r for r in SPANS if r["held_out"]]:
        assert which_lost(signals_for(row, "ctc"), signals_for(row, "whisper")) == ("", "")


@pytest.mark.parametrize("floor", [0.70, 0.75, 0.80, 0.85])
def test_any_span_floor_inside_the_measured_gap_gives_the_same_verdicts(floor: float,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """The measurement left a gap: lost methods span 0.41 to 0.70, the ones that followed 0.86 to 1.12.
    So the floor is not load-bearing anywhere inside it, and this case says by how much. A floor
    outside the gap changes answers, which is what makes the range worth asserting."""
    monkeypatch.setattr("ytalbum.timing.LOST_SPAN", floor)
    named = {row["track"] for row in SPANS
             if which_lost(signals_for(row, "ctc"), signals_for(row, "whisper"))[0]}
    assert len(named) == 5, f"floor {floor} changed the verdicts: {sorted(named)}"


def test_the_shipped_floor_sits_inside_that_gap() -> None:
    worst_good = max(min(r["ctc_span"], r["whisper_span"]) for r in SPANS
                     if not which_lost(signals_for(r, "ctc"), signals_for(r, "whisper"))[0])
    best_lost = max(r["whisper_span"] for r in SPANS if "lost" in r["verdict"])
    assert best_lost < LOST_SPAN < worst_good, (best_lost, LOST_SPAN, worst_good)


# ── an entry that is nearly this recording ───────────────────────────────────────────────

def decide(row: dict[str, Any]) -> str:
    if not nominated(row["ours"], row["theirs"]):
        return "shown"
    if row.get("span") is None:
        return "shown"
    return fit_verdict(row["span"], row["unplaced"])


def test_gangnam_style_is_rejected_as_another_song() -> None:
    """One title collision in two hundred: the words are placeable nowhere in this audio."""
    row = by_title(NEAR, "Gangnam Style")
    assert decide(row) == "reject"
    assert row["unplaced"] > NOFIT_UNPLACED


def test_kalte_spuren_live_keeps_the_words_and_loses_the_stamps_as_another_cut() -> None:
    row = by_title(NEAR, "Kalte Spuren")
    assert decide(row) == "words"
    assert row["unplaced"] == 0.0          # every word is in there…
    assert row["span"] < FIT_SPAN          # …but the entry's clock is not this cut's


def test_ringelpietz_is_shown_as_a_clip_and_never_aligned() -> None:
    """83 s of file against a 222 s lyric: the guard refuses to spend an alignment on it."""
    row = by_title(NEAR, "Ringelpietz")
    assert not nominated(row["ours"], row["theirs"])
    assert decide(row) == "shown"


def test_blackbeard_is_taken_whole_although_it_is_outside_the_three_second_rule() -> None:
    """The track that started P35: refused for 4.4 s, and the words were the song's all along."""
    row = by_title(NEAR, "Blackbeard")
    assert row["apart"] > 3.0
    assert decide(row) == "words+stamps"


def test_the_six_feuerschwanz_inconclusives_are_decided_by_nobody() -> None:
    unclear = [r for r in NEAR if decide(r) == "unclear"]
    feuerschwanz = [r for r in unclear if r["artist"] == "Feuerschwanz"]
    assert len(feuerschwanz) == 6
    assert {r["title"] for r in feuerschwanz} == {"Metnotstand im Märchenland", "Methämmer"}
    for row in feuerschwanz:
        assert FIT_SPAN - 0.06 < row["span"] < FIT_SPAN   # just under the band, nowhere near a reject
        assert row["unplaced"] == 0.0
    assert len(unclear) == 29
    assert len({r["artist"] for r in unclear}) == 16


def test_the_five_population_counts_come_out_of_the_recordings() -> None:
    counts: dict[str, int] = {}
    for row in NEAR:
        counts[decide(row)] = counts.get(decide(row), 0) + 1
    assert counts == {"words+stamps": 143, "words": 16, "reject": 1, "unclear": 29, "shown": 14}
    assert sum(counts.values()) == len(NEAR) == 203


@pytest.mark.parametrize("ceiling", [1.15, 1.20, 1.25])
def test_the_span_ceiling_is_what_stops_a_lyric_wider_than_the_song(ceiling: float) -> None:
    """Declared as a hole in the criterion before the design was written: without a ceiling, four
    candidates passed with the lyric wider than the file. The ceiling's exact value is not
    load-bearing between 1.15 and 1.25; removing it entirely is."""
    wider = [r for r in NEAR if r.get("span") and r["span"] > ceiling and r["unplaced"] == 0.0]
    assert wider, "no candidate is wider than the song any more — the corpus has lost its evidence"
    for row in wider:
        assert fit_verdict(row["span"], row["unplaced"]) != "words+stamps", row["title"]
    assert FIT_WIDE <= ceiling


# ── the counterexamples: four heuristics that must keep failing ──────────────────────────

def test_stamps_in_silence_must_not_decide() -> None:
    """The first idea, and the obvious one. These tracks are 55 to 86% singing, so a method that is a
    whole song out still lands inside singing nearly every time."""
    argent = [s for s in SIGNALS if s["title"] == "Argent"]
    whisper = next(s for s in argent if s["method"] == "whisper")
    assert whisper["in_silence"] == 0, "Argent's second method is 120 s out and silent nowhere"

    # and the signal does not separate the populations at all: the worst offender is a method
    # that was RIGHT, and several that were wrong score zero
    wrong = [s for s in SIGNALS if s["method"] == "whisper" and s["expect"] == "disagree"]
    right = [s for s in SIGNALS if s["method"] == "ctc" and s["expect"] == "agree"]
    assert min(s["in_silence"] for s in wrong) <= max(s["in_silence"] for s in right), (
        "if this ever separates cleanly, the silence signal deserves another look — until then it "
        "is recorded and never judged")

    # the sharper form, "is a phrase starting here", fails on a human's own stamps
    sidecars = [s for s in SIGNALS if s["method"] == "sidecar"]
    assert max(s["off_onset"] for s in sidecars) > 20, (
        "a human's own sidecar has most of its stamps away from any detected onset, because lines "
        "begin inside sung stretches, not at their edges")


def test_a_methods_own_confidence_must_not_decide() -> None:
    """It looked decisive for an hour. The two methods do not report the same quantity, and the
    per-method reading fails on its own terms."""
    # Truth, from the spans: the decoder lost these five and the CTC pass lost none of the sixteen.
    LOST_TRACKS = {"Argent", "A Love That Never Dies", "Armata Strigoi", "A Lifetime of War", "Azrael"}
    judged = [(s["confidence"], s["method"] == "whisper" and s["title"] in LOST_TRACKS)
              for s in SIGNALS if s["method"] in ("ctc", "whisper")]

    # No threshold on confidence separates them — not one, at any value the data offers.
    for cut in sorted({c for c, _ in judged}):
        wrong = sum(1 for conf, is_lost in judged if (conf < cut) != is_lost)
        assert wrong, f"a cut at {cut} would have worked, which the measurement says it does not"
    best = min(sum(1 for conf, is_lost in judged if (conf < cut) != is_lost)
               for cut in sorted({c for c, _ in judged}))
    assert best == 3, f"the kindest threshold misclassifies {best} of {len(judged)}, not 3"

    lost_at = next(s for s in SIGNALS if s["title"] == "A Love That Never Dies" and s["method"] == "whisper")
    right_at = next(s for s in SIGNALS if s["title"] == "Argent" and s["method"] == "ctc")
    assert lost_at["confidence"] > right_at["confidence"], (
        "the method that lost the song is more confident than the one that found it, so confidence "
        "cannot name the loser")

    both = [s for s in SIGNALS if s["title"] == "Armata Strigoi" and s["method"] in ("ctc", "whisper")]
    assert all(s["confidence"] < 0.2 for s in both), "and on Armata Strigoi both collapse, naming nobody"

    # the production signal records it and never thresholds it
    assert "confidence" not in lost(Signals(placed=40, total=40, span=0.99, confidence=0.01))


@pytest.mark.parametrize("apart", [1.0, 2.0, 5.0])
def test_raw_versus_stem_agreement_must_not_be_a_check(apart: float) -> None:
    """Requiring the aligner to agree with itself on the mixed track would reject the work it does
    best. The threshold does not rescue it at any width."""
    rejected = [a for a in ARMS if a["ctc_vocals"] <= 1.0 and abs(a["ctc_raw"] - a["ctc_vocals"]) > apart]
    assert len(rejected) >= 8, f"at {apart} s it would still reject {len(rejected)} correct alignments"

    # and the converse, which is the sharper half: agreement is not correctness
    agreeing_and_wrong = [a for a in ARMS
                          if abs(a["ctc_raw"] - a["ctc_vocals"]) <= apart and a["ctc_vocals"] > 5.0]
    assert {a["track"].split("—")[1].strip() for a in agreeing_and_wrong} >= {"Berzerkermode", "Clocks"}, (
        "both arms agree on these two and both are six seconds wrong, because LRCLIB's own entry is "
        "early: two methods agreeing says they share a cause, not that they are right")


def test_the_eleven_good_tracks_a_raw_versus_stem_check_would_reject() -> None:
    rejected = [a for a in ARMS if a["ctc_vocals"] <= 1.0 and abs(a["ctc_raw"] - a["ctc_vocals"]) > 2.0]
    assert len(rejected) == 11
    assert all(a["ctc_vocals"] <= 1.0 for a in rejected)
    # exactly one track is one it would rightly flag, and it is the known-bad one
    flagged = [a for a in ARMS if a["ctc_vocals"] > 1.0 and abs(a["ctc_raw"] - a["ctc_vocals"]) > 2.0]
    assert [a["track"].split("—")[1].strip() for a in flagged] == ["Bastard of Asgard"]


def test_nothing_placed_on_whole_track_disagreement_must_not_return() -> None:
    """The rule P27 shipped with, measured backwards by its own package: five for five it threw
    away the accurate method's work."""
    would_discard = [s for s in SIDECAR if "kept" in s["what_9_38_did"]]
    assert len(would_discard) == 5
    assert sum(1 for s in would_discard if s["primary_vs_sidecar"] > s["second_vs_sidecar"]) == 0, (
        "it caught a bad primary nought times out of five")
    lines = sum(by_title(TRACKS, s["track"].split("—")[1].strip())["lines"] for s in would_discard)
    assert lines == 244, f"and it would have discarded {lines} correct stamps"


# ── the piling rule, which is kept because it catches a different shape ──────────────────

def test_piling_alone_names_four_of_the_five_and_is_kept_for_the_shape_it_catches() -> None:
    piled_only = [r for r in SPANS
                  if lost(Signals(placed=50, total=50, span=None,
                                  piled=round(r["whisper_piled"] * 50)))]
    assert len(piled_only) == 4
    assert LOST_PILED < min(r["whisper_piled"] for r in SPANS if "lost" in r["verdict"]
                            and r["whisper_piled"] > LOST_PILED)
