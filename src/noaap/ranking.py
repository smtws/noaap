"""Which of two copies is the better one, and whether to say so (DESIGN §9, slice 54).

The order is fixed and the reasons are the point: **a verdict nobody can read is not a verdict.**
Every answer carries the sentence that produced it, and every number it was reached with is printed
beside it, because this is the one pass in the program that can throw a file away.

Four answers, and three of them are not "replace":

- `REPLACE` — measured evidence that the incoming file holds more audio.
- `FILL` — there is nothing here to compare with, and this is the same recording.
- `KEEP` — the incumbent stays, and the reason says whether that is evidence or a tie.
- `UNDECIDED` — shown with both files' numbers, settled by a person or not at all.

**An absent number never wins or loses a comparison.** It makes the answer `UNDECIDED`; it does not
make a file worse. And a length that is absent from a *plan* is not absent at all when the file is
on the disk — that mistake cost 17% of the verdicts in the first prototype, so `examine` measures
rather than reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .pairing import Pair, Side
from .spectrum import Spectrum
from .spectrum import better as better_band
from .spectrum import measure as measure_band
from .tag import audio_quality, measured_length

SAME = 3.0       # seconds: within this the two files are the same recording
DIFFERENT = 20.0  # …and beyond this they are not, whatever the names say
CLEARLY = 1.25   # a bitrate has to beat another by this much to mean anything


class Verdict(StrEnum):
    REPLACE = "replace"
    FILL = "fill"
    KEEP = "keep"
    UNDECIDED = "undecided"


@dataclass
class Facts:
    """Everything known about one file, measured from the file itself."""

    length: float | None = None
    band: Spectrum = None  # type: ignore[assignment]
    codec: str | None = None
    bitrate: int | None = None
    bytes: int | None = None
    lossless: bool = False

    def __post_init__(self) -> None:
        if self.band is None:
            self.band = Spectrum()

    def line(self) -> str:
        """The one-line description that goes next to every verdict."""
        parts = [self.codec or "?"]
        if self.bitrate:
            parts.append(f"{round(self.bitrate / 1000)} kbps")
        if self.band.known:
            parts.append(f"to {self.band.cutoff} kHz" + (" (all it can hold)" if self.band.full else ""))
        else:
            parts.append(self.band.why or "unmeasured")
        if self.length:
            parts.append(f"{self.length:.0f}s")
        if self.bytes:
            parts.append(f"{self.bytes / 1e6:.1f} MB")
        return ", ".join(parts)


@dataclass
class Judgement:
    verdict: Verdict
    why: str
    new: Facts
    old: Facts | None = None

    @property
    def acts(self) -> bool:
        return self.verdict in (Verdict.REPLACE, Verdict.FILL)


LOSSLESS = ("flac", "alac", "wav")


def examine(side: Side) -> Facts:
    """Measure the file. Not read the plan — the plan is a record, the file is the thing."""
    path = side.file
    if not path or not path.is_file():
        return Facts()
    quality = audio_quality(path)
    codec = quality.get("codec")
    return Facts(length=measured_length(path), band=measure_band(path), codec=codec,
                 bitrate=quality.get("bitrate"), bytes=path.stat().st_size,
                 lossless=codec in LOSSLESS)


def untouchable(old: Side) -> str | None:
    """Why this track must not be replaced by anything, whatever the measurements say.

    The user's own work on *this file*: a trim they marked, a source they chose, words they timed
    against it. A better recording is not better than their afternoon (§9, slice 54).
    """
    track = old.track
    if track.trim_start is not None or track.trim_end is not None:
        return "you trimmed this one"
    if track.source_override:
        return "you chose where this one comes from"
    if track.provenance.get("lyrics") == "user" and track.lyrics == "synced":
        return "your own words are timed to this file"
    return None


def same_recording(new: Facts, old: Facts, reference: float | None = None) -> tuple[bool | None, str]:
    """Are these the same performance? `None` means nobody can say from the lengths alone."""
    if new.length is None or old.length is None:
        return None, "one of them has no length"
    apart = abs(new.length - old.length)
    # **and the sentence says which number it is** (§9, slice 77). With a third opinion the distance
    # measured is each file's distance from *that*, not from each other — so two files of exactly the
    # same length could be shown as "3s apart", which reads as a contradiction of the two lengths
    # printed beside it. The number was right and the word was wrong.
    how = "apart"
    if reference is not None:
        # a third opinion beats comparing two files with each other: both may be padded
        apart = max(abs(new.length - reference), abs(old.length - reference))
        how = "from the length we know"
    if apart <= SAME:
        return True, f"{apart:.0f}s {how}"
    if apart > DIFFERENT:
        return False, f"{apart:.0f}s {how} — a different recording"
    return None, f"{apart:.0f}s {how}"


def judge(pair: Pair, new: Facts, old: Facts | None, reference: float | None = None) -> Judgement:
    """The whole rule, over measured values only. No files are touched here."""
    if (mine := untouchable(pair.old)) is not None:
        return Judgement(Verdict.UNDECIDED, mine, new, old)

    if old is None:
        # nothing is here to replace: this fills a gap, and only if the reference says it is the
        # same recording. Over the reference material this never happens — every library track is
        # present and done — but a download that failed is a real thing and this is its answer.
        if reference is None:
            return Judgement(Verdict.UNDECIDED, "nothing here, and no reference length to check against", new)
        agrees, why = same_recording(new, Facts(length=reference))
        if agrees:
            return Judgement(Verdict.FILL, f"nothing here; {why} from the known length", new)
        return Judgement(Verdict.UNDECIDED, f"nothing here, and {why} from the known length", new)

    agrees, why = same_recording(new, old, reference)
    if agrees is False:
        return Judgement(Verdict.KEEP, why, new, old)
    if agrees is None:
        return Judgement(Verdict.UNDECIDED, why, new, old)

    band = better_band(new.band, old.band)
    if band > 0:
        return Judgement(Verdict.REPLACE, f"holds more audio ({_band(new)} against {_band(old)})", new, old)
    if band < 0:
        return Judgement(Verdict.KEEP, f"holds more audio ({_band(old)} against {_band(new)})", new, old)

    if not new.band.known or not old.band.known:
        return Judgement(Verdict.UNDECIDED, "there is nothing up there to judge by in one of them", new, old)

    if new.lossless and not old.lossless and _not_narrower(new, old):
        # **the user's rule, and only in this direction** (§9, slice 77). A lossless copy that gives
        # up nothing in band is the one worth keeping: it can be re-encoded later without losing a
        # second time, and the lossy file it replaces cannot. The evidence is still measured — a
        # *narrower* lossless copy wins nothing, which is what keeps this from being "trust the
        # container" (R-164, whose refusal stands for every other direction).
        return Judgement(Verdict.REPLACE,
                         f"lossless, and gives up nothing: it holds as much audio as the one here "
                         f"({_band(new)} against {_band(old)})", new, old)
    if new.lossless != old.lossless:
        # R-164 still: a lossless container is not evidence *for the incumbent*, and a lossless
        # candidate that is narrower than what is here is not evidence either.
        keeper, other = ("the incoming file", "the one here") if new.lossless else ("the one here", "the incoming file")
        narrower = " and narrower" if new.lossless else ""
        return Judgement(Verdict.UNDECIDED,
                         f"{keeper} is lossless{narrower} and {other} is not, "
                         f"{'but neither holds more audio' if not narrower else 'and holds less audio'}",
                         new, old)

    if new.codec != old.codec:
        # A rate only means something against the same encoder. Opus at 125 kbps and MP3 at 320
        # are not ranked by their numbers, and that is exactly this material: an Opus library
        # meeting MP3s and FLACs (R-170).
        return Judgement(Verdict.KEEP, "same band, different codecs: the rates do not compare", new, old)
    if new.bitrate and old.bitrate and new.bitrate >= old.bitrate * CLEARLY:
        return Judgement(Verdict.REPLACE,
                         f"same band, clearly higher rate ({round(new.bitrate/1000)} against {round(old.bitrate/1000)} kbps)",
                         new, old)
    return Judgement(Verdict.KEEP, "nothing to choose between them", new, old)


def _not_narrower(new: Facts, old: Facts) -> bool:
    """Whether the incoming file's measured band is at least the incumbent's (§9, slice 77).

    Raw kilohertz, deliberately without the 2 kHz margin that `better_band` applies: the margin is
    there so that one encoder's spread cannot decide a replacement on its own, and here the decision
    is not being made by the band at all — the band only has to not be an argument *against*.
    """
    return (new.band.cutoff is not None and old.band.cutoff is not None
            and new.band.cutoff >= old.band.cutoff)


def _band(f: Facts) -> str:
    return f"{f.band.cutoff} kHz" + (" (all it can hold)" if f.band.full else "")


def reference_length(pair: Pair) -> float | None:
    """A third opinion on how long this recording is: MusicBrainz, else LRCLIB, else nobody."""
    for side in (pair.old, pair.new):
        for value in (side.track.mb_length, side.track.lyrics_length):
            if value:
                return float(value)
    return None


def consider(pair: Pair, examiner=examine) -> Judgement:
    """Measure both sides and judge. The one entry point that touches disk."""
    new, old = examiner(pair.new), examiner(pair.old)
    return judge(pair, new, old if pair.old.present else None, reference_length(pair))


__all__ = ["Facts", "Judgement", "Verdict", "consider", "examine", "judge", "reference_length",
           "same_recording", "untouchable"]
