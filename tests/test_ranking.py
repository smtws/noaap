"""Which of two copies is better, and whether to say so (DESIGN §9, slice 54).

Every case here is about a decision that can throw a file away, so the ones that matter most are
the ones where the answer is **not** "replace". The rule is a pure function of measured values;
nothing in this file touches a disk except the two cases that check the measuring itself.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from noaap.models import AlbumPlan, Kind, PlanTrack, Provenance
from noaap.pairing import How, Pair, Side
from noaap.ranking import Facts, Verdict, judge, reference_length, same_recording, untouchable
from noaap.spectrum import Spectrum


def track(**kw) -> PlanTrack:
    base = dict(video_id="x", number=1, artist="A Band", title="A Song", filename="a.opus",
                provenance={})
    return PlanTrack(**{**base, **kw})


def side(t: PlanTrack | None = None) -> Side:
    t = t or track()
    plan = AlbumPlan(source_url="x://a", source_id="a", kind=Kind.OFFICIAL_ALBUM, album="An Album",
                     albumartist="A Band", year=None, cover_url=None, folder="a", tracks=[t])
    return Side(plan, t, Path("/library"))


def pair_of(old: PlanTrack | None = None, new: PlanTrack | None = None) -> Pair:
    return Pair(new=side(new), old=side(old), how=How.NAME)


def facts(cutoff: int | None = 21, full: bool = False, length: float = 200.0,
          codec: str = "opus", bitrate: int = 128_000, lossless: bool = False,
          why: str = "band-limited") -> Facts:
    band = Spectrum(cutoff=cutoff, full=full, why=why if cutoff else "nothing up here to judge by")
    return Facts(length=length, band=band, codec=codec, bitrate=bitrate, bytes=5_000_000,
                 lossless=lossless)


FLAC = dict(codec="flac", bitrate=900_000, lossless=True)


# -- the user's own work comes first -----------------------------------------------------------------


@pytest.mark.parametrize("edit,expected", [
    (dict(trim_start=12.0), "you trimmed this one"),
    (dict(trim_end=190.0), "you trimmed this one"),
    (dict(source_override="another-ref"), "you chose where this one comes from"),
])
def test_a_track_the_user_worked_on_is_never_proposed(edit, expected):
    """A better recording is not better than their afternoon (R-164, ruling 5)."""
    found = judge(pair_of(old=track(**edit)), facts(cutoff=22, full=True, **FLAC), facts())

    assert found.verdict is Verdict.UNDECIDED and found.why == expected


def test_lyrics_the_user_timed_to_this_file_protect_it():
    timed = track(provenance={"lyrics": Provenance.USER}, lyrics="synced")

    found = judge(pair_of(old=timed), facts(cutoff=22, full=True, **FLAC), facts())

    assert found.verdict is Verdict.UNDECIDED
    assert found.why == "your own words are timed to this file"


def test_lyrics_that_are_not_timed_do_not_protect_it():
    """Plain words move with the track; it is the timings that belong to one file."""
    plain = track(provenance={"lyrics": Provenance.USER}, lyrics="plain")

    assert untouchable(side(plain)) is None


# -- is it even the same recording -------------------------------------------------------------------


@pytest.mark.parametrize("theirs,ours,expected", [(200.0, 201.0, True), (200.0, 203.0, True),
                                                  (200.0, 209.0, None), (200.0, 260.0, False)])
def test_the_bands_of_length(theirs, ours, expected):
    assert same_recording(facts(length=theirs), facts(length=ours))[0] is expected


def test_a_different_recording_is_kept_not_replaced():
    found = judge(pair_of(), facts(cutoff=22, full=True, length=260.0, **FLAC), facts(length=200.0))

    assert found.verdict is Verdict.KEEP and "different recording" in found.why


def test_the_middle_band_is_shown_and_not_decided():
    """53 of the real pairs sit here, and they are mostly live against studio."""
    found = judge(pair_of(), facts(cutoff=22, full=True, length=209.0, **FLAC), facts(length=200.0))

    assert found.verdict is Verdict.UNDECIDED and "9s apart" in found.why


def test_a_third_opinion_beats_comparing_two_files_with_each_other():
    """Both may be padded. MusicBrainz's length is measured against each of them, not between."""
    agrees, why = same_recording(facts(length=200.0), facts(length=202.0), reference=180.0)

    assert agrees is False, "both are 20s longer than the recording actually is"
    assert "different recording" in why


def test_the_reference_is_musicbrainz_then_lrclib_then_nobody():
    assert reference_length(pair_of(old=track(mb_length=181.0, lyrics_length=200.0))) == 181.0
    assert reference_length(pair_of(old=track(lyrics_length=200.0))) == 200.0
    assert reference_length(pair_of()) is None


# -- quality, on measured things only ------------------------------------------------------------------


def test_a_wider_band_replaces():
    found = judge(pair_of(), facts(cutoff=22, full=True, **FLAC), facts(cutoff=20))

    assert found.verdict is Verdict.REPLACE and "holds more audio" in found.why
    assert "22 kHz (all it can hold)" in found.why and "20 kHz" in found.why


def test_a_narrower_band_keeps_what_is_here():
    found = judge(pair_of(), facts(cutoff=17, **FLAC), facts(cutoff=21))

    assert found.verdict is Verdict.KEEP and "holds more audio" in found.why


@pytest.mark.parametrize("theirs,mine,verdict", [
    (21, 20, Verdict.REPLACE),    # wider, and inside the 2 kHz margin that decides nothing by itself
    (20, 20, Verdict.REPLACE),    # exactly as wide: it gives up nothing
    (22, 20, Verdict.REPLACE),
    (19, 20, Verdict.UNDECIDED),  # narrower: the container is not an argument
])
def test_a_lossless_copy_that_gives_up_nothing_takes_the_place_of_a_lossy_one(theirs, mine, verdict):
    """**The user's rule** (§9, slice 77): a lossless copy that is not narrower than the lossy file
    here is the one worth keeping — it can be re-encoded later without losing a second time, and the
    file it replaces cannot. The measurement still decides *against* it: a narrower lossless copy
    wins nothing, which is what keeps this from being "trust the container" (R-164)."""
    found = judge(pair_of(), facts(cutoff=theirs, **FLAC), facts(cutoff=mine))

    assert found.verdict is verdict
    if verdict is Verdict.UNDECIDED:
        assert "lossless" in found.why and "holds less audio" in found.why
    elif theirs - mine < 2:
        # inside the margin the band decides nothing, so this is the new rule speaking
        assert "lossless" in found.why and "gives up nothing" in found.why
    else:
        # beyond it the band was always enough, and says so in its own words
        assert "holds more audio" in found.why


def test_and_in_the_other_direction_a_lossless_container_is_still_not_evidence():
    """R-164, ruling 1, which stands: the FLAC this intake meets most often is a decode of the very
    Opus it would replace. A *lossy* candidate never displaces a lossless file on the container."""
    found = judge(pair_of(), facts(cutoff=20), facts(cutoff=20, **FLAC))

    assert found.verdict is Verdict.UNDECIDED
    assert "lossless" in found.why and "neither holds more audio" in found.why

    # …and a lossy candidate with a wider band is decided by the band, as before
    wider = judge(pair_of(), facts(cutoff=22), facts(cutoff=20, **FLAC))
    assert wider.verdict is Verdict.REPLACE and "holds more audio" in wider.why


def test_a_clearly_higher_bitrate_replaces_when_the_band_and_the_codec_agree():
    found = judge(pair_of(), facts(cutoff=20, codec="mp3", bitrate=320_000),
                  facts(cutoff=20, codec="mp3", bitrate=128_000))

    assert found.verdict is Verdict.REPLACE and "320 against 128" in found.why


def test_across_codecs_a_bitrate_says_nothing():
    """R-170: Opus at 125 kbps and MP3 at 320 are not ranked by their numbers, and that is this
    material exactly — an Opus library meeting MP3s and FLACs."""
    found = judge(pair_of(), facts(cutoff=20, codec="mp3", bitrate=320_000),
                  facts(cutoff=20, codec="opus", bitrate=125_000))

    assert found.verdict is Verdict.KEEP
    assert found.why == "same band, different codecs: the rates do not compare"


def test_a_slightly_higher_bitrate_is_not_a_reason():
    found = judge(pair_of(), facts(cutoff=20, codec="mp3", bitrate=140_000),
                  facts(cutoff=20, codec="mp3", bitrate=128_000))

    assert found.verdict is Verdict.KEEP and found.why == "nothing to choose between them"


def test_a_tie_goes_to_the_incumbent():
    found = judge(pair_of(), facts(), facts())

    assert found.verdict is Verdict.KEEP


# -- an absent number never wins or loses ----------------------------------------------------------------


def test_a_file_with_nothing_measurable_up_there_decides_nothing():
    found = judge(pair_of(), facts(cutoff=None), facts(cutoff=20))

    assert found.verdict is Verdict.UNDECIDED and "nothing up there" in found.why


def test_a_missing_length_decides_nothing():
    found = judge(pair_of(), facts(length=None), facts())

    assert found.verdict is Verdict.UNDECIDED and "no length" in found.why


def test_neither_side_is_punished_for_being_unmeasured():
    """Symmetry matters: an unmeasured incumbent must not be replaced *because* it is unmeasured."""
    unmeasured_incoming = judge(pair_of(), facts(cutoff=None), facts(cutoff=22, full=True))
    unmeasured_incumbent = judge(pair_of(), facts(cutoff=22, full=True, **FLAC), facts(cutoff=None))

    assert unmeasured_incoming.verdict is Verdict.UNDECIDED
    assert unmeasured_incumbent.verdict is Verdict.UNDECIDED


# -- filling a gap ------------------------------------------------------------------------------------


def test_nothing_here_and_the_right_length_is_a_fill():
    found = judge(pair_of(old=track(mb_length=200.0)), facts(length=201.0), None, reference=200.0)

    assert found.verdict is Verdict.FILL and found.acts


def test_nothing_here_and_the_wrong_length_is_not():
    found = judge(pair_of(old=track(mb_length=200.0)), facts(length=260.0), None, reference=200.0)

    assert found.verdict is Verdict.UNDECIDED


def test_nothing_here_and_no_reference_decides_nothing():
    found = judge(pair_of(), facts(length=201.0), None, reference=None)

    assert found.verdict is Verdict.UNDECIDED and "no reference length" in found.why


# -- what a verdict tells the person reading it ------------------------------------------------------------


def test_every_verdict_carries_both_files_numbers():
    found = judge(pair_of(), facts(cutoff=22, full=True, length=201.0, **FLAC), facts(cutoff=20))

    assert "flac" in found.new.line() and "900 kbps" in found.new.line()
    assert "to 22 kHz (all it can hold)" in found.new.line() and "201s" in found.new.line()
    assert "opus" in found.old.line() and "to 20 kHz" in found.old.line()


def test_only_replace_and_fill_act():
    assert judge(pair_of(), facts(cutoff=22, full=True, **FLAC), facts(cutoff=20)).acts is True
    assert judge(pair_of(), facts(), facts()).acts is False
    assert judge(pair_of(), facts(length=None), facts()).acts is False
