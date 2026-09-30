"""Putting words on a clock — the boundary, not the machinery (DESIGN.md §9, slice 36).

Two capabilities, because they are two jobs with two different markets: **transcribe** derives words
from audio, **align** places words you already have. noaap wants the second far more often —
LRCLIB supplies the words for two tracks in three and the timings for fewer — and it is the one that
needs no large model.

Nothing here imports a model, and the core never imports one either. The default provider is
`none`: it can do neither, the page renders no action for it, and noaap is exactly what it was
before this file existed. A provider that *can* do something is an optional extra (`local`) or
another machine (`http`), and `docs/spikes/2026-09-alignment.md` is why it is shaped this way.
"""

from __future__ import annotations

import gc
import itertools
import logging
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)

ALIGN = "align"
TRANSCRIBE = "transcribe"
#: place words a user already has by **listening first** and matching them to what was heard
#: (§9, slice 83). Not a third market: it is asked of whoever transcribes, because that is the work
#: it does — so a provider offers it exactly when it transcribes *with word times*.
LISTEN = "listen"
PROVIDERS = ("none", "local", "http", "elevenlabs", "deepgram")
VENDORS = ("elevenlabs", "deepgram")  # the ones that need a key and send the audio away

# List prices from the vendors' own pricing pages, read on the date beside them. They are here so
# the settings panel can say what a pass would cost without asking anyone at runtime; nothing in
# noaap ever queries a price, and a stale number is better than a request nobody asked for.
# What a kind of provider can ever be asked for, which is not the same as what it can do today: the
# two local ones depend on what is installed or on the machine at the other end, and `capabilities()`
# answers that at runtime. This is for the settings panel, so that the slot for drafting words does
# not offer a provider that only aligns, and the slot for aligning does not offer Deepgram (§9, slice 40).
OFFERS = {"none": (), "local": (ALIGN, TRANSCRIBE, LISTEN), "http": (ALIGN, TRANSCRIBE, LISTEN),
          "elevenlabs": (ALIGN, TRANSCRIBE, LISTEN), "deepgram": (TRANSCRIBE, LISTEN)}

PRICES = {
    "elevenlabs": ("$0.22 per audio hour (alignment and transcription alike)", "2026-09-27"),
    "deepgram": ("$0.0043 per audio minute, i.e. $0.26 per hour (Nova-3, pay as you go)", "2026-09-27"),
}


class TimingUnavailable(RuntimeError):
    """Asked of a provider that cannot do it, or is not installed. Never a crash, always a message."""


@dataclass
class TimedLine:
    """One line of words and where it starts, in the **file's** clock (§9, slice 35).

    `start is None` means the provider would not place it. That is a first-class answer: every
    method measured in the spike fails on some tracks, and a guess would be indistinguishable from
    an answer.
    """

    text: str
    start: float | None = None
    end: float | None = None


@dataclass
class Timed:
    """What a provider answers, with enough about itself to be recorded beside the words."""

    lines: list[TimedLine]
    provider: str
    model: str
    version: str = ""
    parameters: dict[str, str] = field(default_factory=dict)
    #: how the words were placed: `align` (forced alignment) or `listen` (§9, slice 83). Empty means
    #: the answer came from before there was a choice, and is read as `align`.
    method: str = ""

    @property
    def unplaced(self) -> list[int]:
        return [i for i, line in enumerate(self.lines) if line.start is None]

    @property
    def by(self) -> str:
        """What goes into `lyrics_timed_by`: short, and enough to tell two runs apart.

        The method is part of it (§9, slice 83): the same provider and model answer differently
        depending on whether they were asked to align the words or to listen for them, so a stamp
        that says only *local/large-v3* does not say what was done.
        """
        return (f"{self.provider}/{self.model}" + (f" {self.version}" if self.version else "")
                + (f" ({self.method})" if self.method else ""))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Timed:
        return cls(lines=[TimedLine(**line) for line in d.get("lines", [])],
                   provider=str(d.get("provider", "")), model=str(d.get("model", "")),
                   version=str(d.get("version", "")), parameters=dict(d.get("parameters", {})),
                   method=str(d.get("method", "")))


@runtime_checkable
class Timing(Protocol):
    """Whoever can put words on a clock. `capabilities()` is what the UI asks before offering."""

    name: str

    def capabilities(self) -> frozenset[str]: ...

    def align(self, audio: Path, lines: list[str], *, language: str | None = None,
              check: Callable[[], None] | None = None) -> Timed: ...

    def transcribe(self, audio: Path, *, language: str | None = None,
                   check: Callable[[], None] | None = None) -> Timed: ...


class NoTiming:
    """The default. Says no to everything, and says it in words a user can act on."""

    name = "none"

    def capabilities(self) -> frozenset[str]:
        return frozenset()

    def align(self, audio: Path, lines: list[str], *, language: str | None = None,
              check: Callable[[], None] | None = None) -> Timed:
        raise TimingUnavailable(
            "No timing provider is configured. Set `timing_provider` to `local` (with the "
            "`noaap[timing]` extra installed) or to `http` with an endpoint — see the README.")

    def transcribe(self, audio: Path, *, language: str | None = None,
                   check: Callable[[], None] | None = None) -> Timed:
        return self.align(audio, [])


# -- the language of a set of lines, from the words themselves ------------------------------

_DE = frozenset("der die das und ich du wir ihr sie nicht ist sind war mit auf für von den dem ein "
                "eine kein mein dein sein wie wenn dann noch nur schon aus bei nach über durch ohne "
                "um zu im am es auch mehr immer wieder".split())
_EN = frozenset("the and you your we they not is are was with for from this that then just only out "
                "into over through without about our their have has will would can't don't i'm it's "
                "me my all like never".split())


def language_of(lines: list[str], default: str = "en") -> str:
    """Which aligner to use, decided the way the spike decided it: by counting stopwords.

    Deliberately not a language-detection dependency: two word lists settle German against English,
    which is what this library holds, and a wrong guess costs one re-run with the language given.
    """
    import re

    words = re.findall(r"[\w']+", " ".join(lines).lower())
    if not words:
        return default
    de, en = sum(w in _DE for w in words), sum(w in _EN for w in words)
    return "de" if de > en else "en" if en > de else default


#: how many stopword hits, and how clear a win, before the words are allowed to name the language
#: for a transcriber. Two lists settle German against English; a lyric in a third language must not
#: be told it is one of them.
LANGUAGE_SURE = 3


def language_hint(lines: list[str]) -> str | None:
    """The language the given words are clearly in, or None — *for a transcriber* (§9, slice 83).

    Not the same question as `language_of`, which must answer something because there are two
    aligners and one has to be chosen. Here a wrong answer is worse than none: a transcriber told the
    wrong language writes down nonsense, and a transcriber told nothing detects it itself. Measured:
    letting it detect for itself, the German track of the reported case came back as **27 words of
    Russian subtitle boilerplate** out of a four-minute song, and one English track as 18 — while the
    same model given the language heard 190 and 300. But it is only ever told what these two word
    lists can be sure of (`docs/qa-catalog.md`, BV).
    """
    words = re.findall(r"[\w']+", " ".join(lines).lower())
    de, en = sum(w in _DE for w in words), sum(w in _EN for w in words)
    best, other = ("de", en) if de > en else ("en", de)
    if max(de, en) >= LANGUAGE_SURE and max(de, en) >= 2 * other:
        return best
    return None


STAMP = re.compile(r"^\s*\[(\d{1,3}):(\d{2}(?:[.:]\d{1,3})?)\]\s?")


def stamped(text: str, at: float | None) -> str:
    """One line of a `.lrc`, in the shape a hand-typed one has: `[01:23.4] the words`."""
    if at is None:
        return text
    tenths = max(0, round(at * 10))
    return f"[{tenths // 600:02d}:{(tenths % 600) / 10:04.1f}] {text}".rstrip()


def plain_lines(text: str) -> list[str]:
    """The words an aligner should be given: no blank lines, no stamps already on them.

    Blank lines are dropped rather than passed through as empty ones, because "could not place"
    should mean what it says; the page puts the stamps back on the lines it sent (§9, slice 36).
    """
    return [STAMP.sub("", line).strip() for line in (text or "").splitlines() if STAMP.sub("", line).strip()]


# -- two methods, checked against each other (§9, slice 38) -----------------------------------------

# Seconds two methods may differ by and still count as agreeing. 2.0, from the 16-track run in
# catalog Y and not from taste: the distances come in two shapes — jitter under ~1.5 s, where the CTC
# pass is the better of the two, and a real parting of the ways at 5–18 s, which is the failure this
# mode exists for. At 1.0 s the check took stamps away from lines the primary had placed within 0.1 s
# of a hand-checked sidecar, and every unplaced line is a manual action for whoever asked.
VERIFY_THRESHOLD = 2.0
# Seconds past which a line is not "two methods differing" but one of them having lost the song. The
# two rules measure different things and must not share a constant: widening what counts as agreement
# would otherwise widen what counts as a salvageable track. 5.0 is where catalog Y's distances
# separate — jitter tails off below 2 s, real divergence sits at 5–18 s.
VERIFY_LOST = 5.0
MOSTLY = 0.5  # more than half the lines LOST means the two are not disagreeing, they are elsewhere


# -- how much a method's own output believes itself (§9, slice 44) -----------------------------------
#
# When two aligners place a track 30-120 s apart, one of them has lost the song and §9, slice 38 had no way
# to say which, so it kept the primary by policy. These are the signals that decide it by evidence.
# Three of them are method-independent arithmetic on the stamps; two are what each method already
# says about itself; the strongest is the one that asks the audio.

# **The signal that works is coverage**, and it was not the one this was started with: a lyric's
# stamps should span the part of the track where somebody is singing. A method that has lost the
# song squeezes the whole lyric into a fraction of it. Measured over sixteen real tracks (catalog
# AE): the five where a method was 28-120 s out span 0.41-0.70 of the singing, the twenty-seven
# answers that followed the song span 0.86-1.12, and any floor from 0.70 to 0.85 separates them
# with nothing wrong on either side.
LOST_SPAN = 0.75
# Lines stacked within a third of a second of each other are a method that gave up and piled the
# rest of the lyric where it stopped. It catches four of the same five on its own; it is kept as a
# second reason because it catches a different shape of failure, not because these five need it.
LOST_PILED = 0.12
PILED_WITHIN = 0.35    # seconds between two stamps for them to count as piled
LOST_BACKWARDS = 0.25  # a quarter of its stamps going backwards is not a song
LOST_PAST_END = 0.1    # a tenth of its stamps beyond the end of the file
# What was tried first and does not work, kept as a measured number and never as a rule: these
# tracks are 55-86% singing, so a method that is elsewhere still lands *inside* singing nearly every
# time. A human's own stamps shifted by a full minute put only 12-30% into silence, and on Argent —
# where the second method is 120 s out — 0 of its 46 stamps fell in silence.
SUNG_WITHIN = 1.5      # seconds: how late a stamp may be and still be "while somebody sings"


# A correct stamp sits at the *start* of a sung phrase, because that is where a line begins. This is
# the same measurement as "in silence" asked more sharply, and the sweep in catalog AE is why: a
# track is 55-85% singing, so a method that is elsewhere in the song still lands *inside* singing
# most of the time (12-30% of deliberately shifted stamps fell in silence, never the half a rule
# could use), while it lands at a phrase *start* much less often.
ONSET_WITHIN = 1.5     # seconds after a phrase starts


@dataclass
class Signals:
    """What one method's answer says about whether it followed the song.

    Every field is a count of that method's own stamps, so the two methods' signals mean the same
    thing and can be compared. `confidence` is the exception and is recorded, never thresholded: a
    CTC score and a decoder's word probability are not the same quantity, and pretending they are
    would be the kind of number that looks like evidence without being any.
    """

    placed: int = 0
    total: int = 0
    failed: int = 0             # lines or segments the method itself would not place
    past_end: int = 0           # stamps beyond the end of the file
    backwards: int = 0          # stamps earlier than the stamp before them
    piled: int = 0              # stamps within PILED_WITHIN of the one before
    in_silence: int = 0         # stamps where the separated vocal is silent (recorded, not judged)
    off_onset: int = 0          # stamps that are not at the start of a sung phrase (likewise)
    silence_checked: int = 0    # how many stamps could be checked that way at all
    span: float | None = None   # of the sung part of the track, how much the stamps cover
    confidence: float | None = None

    def to_parameters(self, prefix: str) -> dict[str, str]:
        return {f"{prefix}_{name}": f"{value:g}" if isinstance(value, float) else str(value)
                for name, value in asdict(self).items() if value is not None}


def signals_of(starts: list[float | None], length: float | None = None,
               sung: list[tuple[float, float]] | None = None, failed: int = 0,
               confidence: float | None = None) -> Signals:
    """The arithmetic signals, from one method's stamps and the audio they claim to describe."""
    placed = [t for t in starts if t is not None]
    got = Signals(placed=len(placed), total=len(starts), failed=failed, confidence=confidence)
    if length:
        got.past_end = sum(1 for t in placed if t > length)
    got.backwards = sum(1 for before, after in itertools.pairwise(placed) if after < before)
    got.piled = sum(1 for before, after in itertools.pairwise(sorted(placed))
                    if after - before < PILED_WITHIN)
    if sung:
        got.silence_checked = len(placed)
        got.in_silence = sum(1 for t in placed if not _sung_at(t, sung))
        got.off_onset = sum(1 for t in placed if not _at_onset(t, sung))
        singing = sung[-1][1] - sung[0][0]
        if len(placed) > 1 and singing > 0:
            got.span = round((max(placed) - min(placed)) / singing, 3)
    return got


def _sung_at(at: float, sung: list[tuple[float, float]], within: float = SUNG_WITHIN) -> bool:
    """Is anybody singing at this moment, or about to be? Neither method is exact to the syllable."""
    return any(start - within <= at <= end for start, end in sung)


def _at_onset(at: float, sung: list[tuple[float, float]], within: float = ONSET_WITHIN) -> bool:
    """Is a phrase starting here? A line begins when the singing begins, not in the middle of it."""
    return any(start - within <= at <= start + within for start, _ in sung)


def lost(signals: Signals) -> str:
    """Why this method looks like it lost the song, or empty if it does not (§9, slice 44).

    Deliberately not a score: each rule is a sentence a person can check against the track, and the
    first one that fires is the one the notice says.
    """
    placed = signals.placed
    if not placed:
        return ""
    if signals.span is not None and signals.span < LOST_SPAN:
        return f"its stamps cover only {signals.span:.0%} of the part of the track where somebody sings"
    if signals.piled > placed * LOST_PILED:
        return f"{signals.piled} of its {placed} stamps are piled on top of each other"
    if signals.backwards > placed * LOST_BACKWARDS:
        return f"{signals.backwards} of its {placed} stamps go backwards"
    if signals.past_end > placed * LOST_PAST_END:
        return f"{signals.past_end} of its {placed} stamps are past the end of the track"
    return ""


def which_lost(first: Signals, second: Signals) -> tuple[str, str]:
    """Which of the two lost the song: ("first"|"second"|"", reason).

    **Only when exactly one of them looks lost.** If both do, or neither, there is no evidence here
    to prefer one over the other and the caller falls back to the policy it had before (§9, slice 38) —
    which is the whole discipline of this: a signal that cannot tell says so.
    """
    why_first, why_second = lost(first), lost(second)
    if why_first and not why_second:
        return "first", why_first
    if why_second and not why_first:
        return "second", why_second
    return "", ""


def verified(primary: Timed, second: Timed, threshold: float = VERIFY_THRESHOLD,
             lost_beyond: float = VERIFY_LOST, evidence: tuple[Signals, Signals] | None = None) -> Timed:
    """Keep the stamps two independent aligners agree about — and say so when they do not.

    Two regimes, and which one applies is decided by `lost`, not by `threshold`:

    **They agree about the track** (at most half the comparable lines more than `lost` apart). Then
    the per-line rule runs: a line the two place more than `threshold` apart comes back unplaced
    rather than guessed at, and everywhere else the *primary* supplies the number.

    **One of them has lost the song** (more than half the lines that far apart). Then the per-line
    rule is switched off and one method's stamps are kept whole. *Which* one is decided by
    `evidence` when it can decide (§9, slice 44): each method's own answer carries signals — stamps where
    nobody sings, stamps going backwards, stamps past the end of the file — and when exactly one
    method looks lost by them, **the other one's stamps are kept** and the notice says which lost
    and why. When the signals cannot tell, or there are none, the primary is kept as before.
    This inverts the rule this function shipped with, and the reason is a measurement rather than an
    opinion (`docs/qa-catalog.md`, section Y): on all five of sixteen real tracks where the condition
    fired, the primary was the accurate one — twice to within a tenth of a second of a hand-checked
    sidecar — and the Whisper pass was 28 to 120 seconds out. Placing nothing caught a bad primary
    nought times out of five and threw away a good alignment five times. A rule that cannot tell
    *which* method is lost must not discard the one the evidence favours; telling them apart is
    backlog item 21. Letting the per-line rule run here too would strip most of the stamps anyway and
    make the inversion hollow.
    """
    lines = list(primary.lines)
    checked = {i: line.start for i, line in enumerate(second.lines)} if second else {}
    comparable = disagreed = gone = 0
    for i, line in enumerate(lines):
        other = checked.get(i)
        if line.start is None or other is None:
            continue
        comparable += 1
        apart = abs(line.start - other)
        if apart > threshold:
            disagreed += 1
        if apart > lost_beyond:
            gone += 1
    elsewhere = comparable > 0 and gone > comparable * MOSTLY
    # which of them lost it, when the evidence can say (§9, slice 44)
    loser, why = which_lost(*evidence) if (elsewhere and evidence) else ("", "")
    winner = second if loser == "first" else primary
    source = list(winner.lines)
    out = []
    for i, line in enumerate(lines):
        other = checked.get(i)
        apart = None if (line.start is None or other is None) else abs(line.start - other)
        if elsewhere:
            # the whole-track case keeps one method's stamps entire: the per-line rule would strip
            # most of them anyway and make the choice hollow
            kept = source[i] if i < len(source) else TimedLine(text=line.text)
            out.append(TimedLine(text=line.text, start=kept.start, end=kept.end))
            continue
        keep = apart is None or apart <= threshold
        out.append(TimedLine(text=line.text, start=line.start if keep else None,
                             end=line.end if keep else None))
    parameters = dict(primary.parameters)
    parameters.update({"verified_against": second.model if second else "",
                       "threshold": f"{threshold:g}", "lost_beyond": f"{lost_beyond:g}",
                       "compared": str(comparable), "disagreed": str(disagreed), "lost": str(gone)})
    if evidence:
        parameters.update(evidence[0].to_parameters("first"))
        parameters.update(evidence[1].to_parameters("second"))
    if elsewhere and loser:
        parameters["lost_method"] = second.model if loser == "second" else primary.model
        parameters["lost_why"] = why
        parameters["kept_method"] = winner.model
    elif elsewhere:
        parameters["one_method"] = "a second method disagreed about the whole track"
    # the stamps are the kept method's, so the record of whose clock this is says that method —
    # otherwise a sidecar would claim a clock it does not carry (§9, slice 44)
    model = f"{primary.model} + {second.model}" if second else primary.model
    if elsewhere and loser:
        model = f"{winner.model} (the other method lost the song)"
    return Timed(lines=out, provider=primary.provider, model=model,
                 version=primary.version, parameters=parameters)


# -- words back into lines -------------------------------------------------------------------

def _key(word: str) -> str:
    """A word stripped to what two systems can be expected to agree on."""
    return re.sub(r"[^\w']", "", (word or "").lower())


LOOKAHEAD = 4  # how far out of step a returned word list may be before a line is given up on


def line_starts(owners: list[int], wanted: list[str], got: list[dict[str, Any]]) -> dict[int, float]:
    """Which second each line starts at, from a provider's word list.

    `owners[i]` is the line word `i` belongs to. Vendors do not return exactly the words they were
    given — one splits "don't", another drops a bracketed aside — so this walks both lists together
    and allows a few words of slippage rather than assuming they line up. A word that cannot be
    found within the window is skipped, and a line whose words were all skipped simply has no start,
    which is what `unplaced` is for.
    """
    starts: dict[int, float] = {}
    j = 0
    for i, want in enumerate(wanted):
        key = _key(want)
        k = j
        while k < min(len(got), j + LOOKAHEAD) and _key(str(got[k].get("text") or got[k].get("word") or "")) != key:
            k += 1
        if k >= min(len(got), j + LOOKAHEAD):
            continue
        start = got[k].get("start")
        if start is not None:
            starts.setdefault(owners[i], float(start))
        j = k + 1
    return starts


# -- placing words by listening first (§9, slice 83) -----------------------------------------------
#
# **Forced alignment cannot know that a line is not in the recording.** It places every line it is
# given, because that is what it is; slice 81 takes back the ones where nobody was singing, which
# catches a line pinned into silence but not one glued to real singing. Listening asks a different
# question: *were these words said at all?* A transcript with word times is the recording's own
# account of itself, and a line whose words are not in it was not sung — which is the answer the
# reported case needs and the aligner can never give.


@dataclass
class HeardWord:
    """One word a provider heard, and when. `start is None` for a provider that gives no times."""

    text: str
    start: float | None = None
    end: float | None = None


@dataclass
class Heard:
    """What a provider heard, word by word — the raw material `listen` matches the given lines to."""

    words: list[HeardWord]
    provider: str
    model: str
    version: str = ""
    parameters: dict[str, str] = field(default_factory=dict)

    @property
    def timed(self) -> list[HeardWord]:
        """Only the words that carry a time: the rest cannot place anything."""
        return [w for w in self.words if w.start is not None]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def heard_from(raw: list[dict[str, Any]], provider: str, model: str, version: str = "",
               parameters: dict[str, str] | None = None) -> Heard:
    """A provider's word list, whatever it calls its fields, as `Heard`."""
    words = []
    for one in raw:
        text = str(one.get("text") or one.get("word") or one.get("punctuated_word") or "").strip()
        if not text:
            continue
        start, end = one.get("start"), one.get("end")
        words.append(HeardWord(text=text, start=None if start is None else round(float(start), 2),
                               end=None if end is None else round(float(end), 2)))
    return Heard(words=words, provider=provider, model=model, version=version,
                 parameters=dict(parameters or {}))


#: share of a line's own words that must be heard, in one run, for the line to be placed. Measured
#: (`docs/qa-catalog.md`, section BV) rather than chosen.
HEARD_ENOUGH = 0.5
#: heard words that may sit between two of a line's words and still count as *one* run — a
#: transcriber mishears and drops words inside a line it otherwise got right.
HEARD_SKIP = 3


def place_by_listening(lines: list[str], heard: Heard, *, enough: float = HEARD_ENOUGH,
                       skip: int = HEARD_SKIP) -> Timed:
    """Place each given line where its words were heard, and leave the rest unplaced (§9, slice 83).

    Matched **in order**, over the whole song at once, by `difflib` over normalised words: a chorus
    sung three times matches its three occurrences in the order they were sung, and a line that is
    not in the recording matches nothing without pushing the lines after it out of step. No model and
    no arithmetic on audio here — this is string work, and it belongs in the core.

    A line is placed when at least `enough` of its own words are found inside **one run** of heard
    words (a run tolerating `skip` heard words between two of them, because a transcriber drops and
    mishears words inside a line it otherwise has). Its start is that run's first word's start, its
    end the last one's end. Anything less and the line comes back unplaced, saying how much of it was
    heard — *the same first-class answer as an aligner's refusal, with a reason a person can check
    against the audio.*
    """
    words: list[tuple[int, str]] = []          # (line, normalised word) for every word with a key
    counts: list[int] = []
    for i, line in enumerate(lines):
        mine = [k for k in (_key(w) for w in line.split()) if k]
        counts.append(len(mine))
        words.extend((i, k) for k in mine)
    got = heard.timed
    keys = [_key(w.text) for w in got]
    pairs: dict[int, int] = {}
    if words and got:
        matcher = SequenceMatcher(None, [k for _, k in words], keys, autojunk=False)
        for a, b, size in matcher.get_matching_blocks():
            for n in range(size):
                pairs[a + n] = b + n
    mine_by_line: dict[int, list[int]] = {}
    for at, (line_no, _) in enumerate(words):
        if at in pairs:
            mine_by_line.setdefault(line_no, []).append(pairs[at])

    placed, said = [], []
    for i, line in enumerate(lines):
        run = _longest_run(mine_by_line.get(i, []), skip)
        share = (len(run) / counts[i]) if counts[i] else 0.0
        if run and share >= enough:
            placed.append(TimedLine(text=line, start=got[run[0]].start, end=got[run[-1]].end))
            continue
        placed.append(TimedLine(text=line))
        if counts[i]:
            said.append(f"line {i + 1}: not heard ({len(run)} of {counts[i]} words)")
        else:
            said.append(f"line {i + 1}: no words to listen for")
    timed = Timed(lines=placed, provider=heard.provider, model=heard.model, version=heard.version,
                  method=LISTEN,
                  parameters={**heard.parameters, "heard_words": str(len(got)),
                              "enough": f"{enough:g}", "skip": str(skip)})
    if said:
        timed.parameters["not_heard"] = "; ".join(said)
    return timed


def _longest_run(heard_at: list[int], skip: int) -> list[int]:
    """The longest stretch of matched heard words with no more than `skip` others between any two."""
    best: list[int] = []
    run: list[int] = []
    for at in heard_at:
        if run and at - run[-1] - 1 > skip:
            best, run = max(best, run, key=len), []
        run.append(at)
    return max(best, run, key=len)


SENTENCE_END = re.compile(r"[.!?…]$")


# A lyric line is a phrase between two pauses, not a sentence between two full stops (§9, slice 45). The
# user's own test showed what the difference costs: one "line" of a draft was a whole verse of three
# sung lines, because the vendor's punctuation put a full stop there and nowhere else.
LINE_GAP = 0.6        # a pause this long between two words ends the line
LINE_SECONDS = 8.0    # and no line runs longer than this, however the singer breathes
LINE_WORDS = 12       # nor holds more words than this
SILENCE_GAP = 6.0     # a stretch this long with no words at all gets a line of its own, saying so


def lines_from_words(words: list[dict[str, Any]], gap: float = LINE_GAP,
                     seconds: float = LINE_SECONDS, most: int = LINE_WORDS) -> list[TimedLine]:
    """A transcript's words grouped into lines a lyric editor can hold — by **when they were sung**.

    Three rules, in order: a pause longer than `gap` between two words ends a line, because that is
    what a line break is in a song; a line may not run longer than `seconds` or hold more than
    `most` words, because a singer who never pauses still sings in phrases; and punctuation *may*
    end a line but can never hold one together across a pause. Before this the breaks followed the
    vendor's sentences alone, and a draft of a 3:25 song came back as eleven lines, one of them a
    whole verse (`docs/qa-catalog.md`, section AF).
    """
    lines: list[TimedLine] = []
    current: list[tuple[str, float | None, float | None]] = []

    def flush(upto: int | None = None) -> None:
        """Turn the words held so far (or the first `upto` of them) into a line."""
        nonlocal current
        take, current = (current, []) if upto is None else (current[:upto], current[upto:])
        if not take:
            return
        starts = [a for _, a, _ in take if a is not None]
        ends = [b for _, _, b in take if b is not None]
        lines.append(TimedLine(text=" ".join(t for t, _, _ in take),
                               start=starts[0] if starts else None, end=ends[-1] if ends else None))

    def widest_gap() -> int | None:
        """Where a line that must be split should be split: at its own longest pause.

        Cutting at the word count instead leaves orphans — the user's draft had a line reading
        just "Rauch." because the twelfth word happened to fall there.
        """
        best, where = 0.0, None
        for i in range(1, len(current)):
            before, after = current[i - 1][2], current[i][1]
            if before is not None and after is not None and after - before > best:
                best, where = after - before, i
        return where

    for w in words:
        text = str(w.get("punctuated_word") or w.get("text") or w.get("word") or "").strip()
        if not text:
            continue
        at = None if w.get("start") is None else float(w["start"])
        ends_at = None if w.get("end") is None else float(w["end"])
        last_end = next((b for _, _, b in reversed(current) if b is not None), None)
        started = next((a for _, a, _ in current if a is not None), None)
        # the pause *before* this word decides whether it belongs to the line being built
        if current and last_end is not None and at is not None and at - last_end > gap:
            flush()
        elif current and ((started is not None and at is not None and at - started > seconds)
                          or len(current) >= most):
            flush(widest_gap())
        current.append((text, at, ends_at))
        if SENTENCE_END.search(text):
            flush()
    flush()
    return lines


def with_gaps(lines: list[TimedLine], length: float | None = None,
              gap: float = SILENCE_GAP) -> list[TimedLine]:
    """Say where the machine heard nothing, instead of letting the next line jump a minute (§9, slice 45).

    A draft that goes from 0:23 to 1:16 without a word looks, in the editor, exactly like a song
    with a long instrumental — and exactly like a transcriber that missed the whole chorus. It was
    the second, and nothing on the screen said so.
    """
    out: list[TimedLine] = []
    last: float | None = None
    for line in lines:
        if last is not None and line.start is not None and line.start - last > gap:
            out.append(TimedLine(text=f"\u2026 ({round(line.start - last)} s without words)", start=round(last, 2)))
        out.append(line)
        if line.end is not None:
            last = line.end
        elif line.start is not None:
            last = line.start
    if length and last is not None and length - last > gap:
        out.append(TimedLine(text=f"\u2026 ({round(length - last)} s without words)", start=round(last, 2)))
    return out


def coverage(lines: list[TimedLine], length: float | None = None,
             gap: float = SILENCE_GAP) -> dict[str, str]:
    """How much of the audio a draft actually has words for, and how many holes it left.

    The old notice said "11 of 11 lines came with a time", which is true of any draft and tells
    nobody anything: of course every line the machine wrote has a time, it wrote them from times.
    """
    spoken = [line for line in lines if line.start is not None and not line.text.startswith("\u2026 (")]
    covered = sum((line.end or line.start or 0) - (line.start or 0) for line in spoken)
    holes, last = 0, None
    for line in spoken:
        if last is not None and line.start is not None and line.start - last > gap:
            holes += 1
        last = line.end if line.end is not None else line.start
    if length and last is not None and length - last > gap:
        holes += 1
    got = {"covered": f"{covered:.1f}", "gaps": str(holes), "gap_longer_than": f"{gap:g}"}
    if length:
        got["length"] = f"{length:.1f}"
    return got


# -- a provider on another machine ------------------------------------------------------------


class HttpTiming:
    """`noaap timing-serve`, somewhere else on the network.

    The inference is the same code the `local` provider runs; what changes is which machine pays
    for it. Nothing leaves the network, and this side inherits no dependency heavier than the
    `httpx` noaap already has.
    """

    name = "http"

    def __init__(self, endpoint: str, timeout: float = 900.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self._capabilities: frozenset[str] | None = None

    def capabilities(self) -> frozenset[str]:
        """Asked once and remembered: it is a property of the far end, not of this request."""
        if self._capabilities is None:
            import httpx

            try:
                r = httpx.get(f"{self.endpoint}/capabilities", timeout=10.0)
                r.raise_for_status()
                self._capabilities = frozenset(str(c) for c in r.json().get("capabilities", []))
            except Exception as e:
                raise TimingUnavailable(f"{self.endpoint} did not answer: {e}") from e
        return self._capabilities

    def align(self, audio: Path, lines: list[str], *, language: str | None = None,
              check: Callable[[], None] | None = None) -> Timed:
        return self._send("align", audio, lines, language, check)

    def _send(self, what: str, audio: Path, lines: list[str], language: str | None,
              check: Callable[[], None] | None) -> Timed:
        return Timed.from_dict(self._ask(what, audio, lines, language, check))

    def _ask(self, what: str, audio: Path, lines: list[str], language: str | None,
             check: Callable[[], None] | None) -> dict[str, Any]:
        import httpx

        if check:
            check()  # the last cheap moment before a request that may take minutes
        data = {"lines": "\n".join(lines), "language": language or ""}
        try:
            with audio.open("rb") as fh:
                r = httpx.post(f"{self.endpoint}/{what}", data=data,
                               files={"audio": (audio.name, fh, "application/octet-stream")},
                               timeout=self.timeout)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise TimingUnavailable(f"{self.endpoint} refused: {e.response.status_code} "
                                    f"{e.response.text[:200]}") from e
        except Exception as e:
            raise TimingUnavailable(f"{self.endpoint} could not be reached: {e}") from e
        if check:
            check()
        return dict(r.json())

    def transcribe(self, audio: Path, *, language: str | None = None,
                   check: Callable[[], None] | None = None) -> Timed:
        if TRANSCRIBE not in self.capabilities():
            raise TimingUnavailable(f"{self.endpoint} only aligns words it is given")
        return self._send("transcribe", audio, [], language, check)

    def heard(self, audio: Path, *, language: str | None = None,
              check: Callable[[], None] | None = None) -> Heard:
        """What the far end heard, word by word (§9, slice 83).

        The recording goes over the network and the **words come back**; the matching to the user's
        own lines happens here, in the core, because this side is the one holding the lyric. The
        machine at the other end never sees it.
        """
        if LISTEN not in self.capabilities():
            raise TimingUnavailable(f"{self.endpoint} cannot listen for words — it needs the "
                                    "`timing-check` extra installed on that machine")
        body = self._ask("heard", audio, [], language, check)
        return Heard(words=[HeardWord(**word) for word in body.get("words", [])],
                     provider=f"{self.name}:{body.get('provider', 'local')}",
                     model=str(body.get("model", "")), version=str(body.get("version", "")),
                     parameters={str(k): str(v) for k, v in (body.get("parameters") or {}).items()})


# -- choosing one ------------------------------------------------------------------------------


def kind_for(cfg: Any, what: str = "") -> str:
    """Which provider is configured for a capability (§9, slice 40).

    Two slots — `timing_align_provider` and `timing_draft_provider` — because the two capabilities
    are bought in different places: the machine that aligns best (`local`, free, needs the models) is
    rarely the one that transcribes best (a vendor, metered, needs nothing installed). An empty slot
    falls back to `timing_provider`, which is what every config written before this said and still
    means both, so nothing anyone has configured breaks.
    """
    # `listen` places words you already have, but the work it does is transcription and it is paid for
    # by the minute like transcription — so it is asked of the drafting slot (§9, slice 83)
    slot = {ALIGN: "timing_align_provider", TRANSCRIBE: "timing_draft_provider",
            LISTEN: "timing_draft_provider"}.get(what, "")
    chosen = (getattr(cfg, slot, "") or "").strip() if slot else ""
    return chosen or (getattr(cfg, "timing_provider", "none") or "none")


def provider(cfg: Any, what: str = "") -> Timing:
    """The provider configured for this capability. The heavy import happens here and nowhere else."""
    kind = kind_for(cfg, what)
    if kind == "local":
        from .timing_local import LocalTiming

        return LocalTiming(device=getattr(cfg, "timing_device", "auto") or "auto",
                           verify=getattr(cfg, "timing_verify", None),
                           threshold=float(getattr(cfg, "timing_verify_threshold", VERIFY_THRESHOLD)),
                           lost=float(getattr(cfg, "timing_verify_lost", VERIFY_LOST)))
    if kind in VENDORS:
        from .timing_cloud import cloud_provider

        return cloud_provider(kind, cfg)
    if kind == "http":
        endpoint = (getattr(cfg, "timing_endpoint", "") or "").strip()
        if not endpoint:
            raise TimingUnavailable("timing_provider is `http` but timing_endpoint is empty")
        return HttpTiming(endpoint)
    return NoTiming()


# providers whose work happens on this computer. `local` runs in this process; `http` may or may not,
# and is judged by its endpoint. Everything else is a vendor: the audio is uploaded, which is the
# whole point of it and exactly what a private recording may not do.
LOCAL_KINDS = ("local",)


def stays_here(cfg: Any, what: str = "") -> bool:
    """Whether asking this provider keeps the audio on this machine (§9, slice 75).

    `local` does. `http` does **only for a loopback endpoint** — `timing-serve` on this same box is
    the same computer; the one on the desktop upstairs is not, however trusted that desktop is, and
    a rule about audio leaving the machine has to mean the machine. A vendor never does. `none`
    never gets asked anything, so there is nothing to send.
    """
    kind = kind_for(cfg, what)
    if kind in LOCAL_KINDS:
        return True
    if kind == "http":
        return _is_loopback((getattr(cfg, "timing_endpoint", "") or "").strip())
    return False


def _is_loopback(endpoint: str) -> bool:
    """Whether this address is this machine, by its parsed host — never by how it is spelled."""
    import ipaddress
    from urllib.parse import urlsplit

    try:
        host = (urlsplit(endpoint).hostname or "").strip().lower()
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def can(cfg: Any, what: str) -> bool:
    """Whether the provider in *that* slot can do *that* job — the question the page really asks."""
    try:
        return what in provider(cfg, what).capabilities()
    except (TimingUnavailable, ImportError, OSError):
        return False


def capabilities_of(cfg: Any) -> frozenset[str]:
    """What the UI may offer at all: each capability asked of the slot that would serve it.

    A union of two providers, and deliberately so — with `local` aligning and a vendor drafting, both
    are true at once and neither provider alone could say so.
    """
    return frozenset(what for what in (ALIGN, TRANSCRIBE, LISTEN) if can(cfg, what))


def release_gpu_memory() -> bool:
    """Give the graphics card back when nothing is running (§9, slice 41, backlog 18).

    A provider is built per job and dropped with it, so the model weights go by themselves — but
    torch keeps what it allocated in its own pool, and a desktop app sitting on 3 GB of an 8 GB card
    with an empty queue is rude to whatever else wants it (it made a second process's separation fail
    with an out-of-memory while P27 was being measured).

    **Only if torch is already here.** An installation with no timing provider must never import it,
    and importing 1.5 GB of it to free nothing would be the worst possible answer. What cannot be
    returned this way is the CUDA context itself, a few hundred MB that belong to the process until
    it exits.
    """
    torch = sys.modules.get("torch")
    if torch is None:
        return False
    try:
        if not torch.cuda.is_available():
            return False
        gc.collect()  # the pool can only release what nothing points at any more
        torch.cuda.empty_cache()
    except Exception:  # a tidy-up may never take a job's result with it
        log.debug("could not release the graphics card", exc_info=True)
        return False
    return True


# -- the process's hold on the graphics card (§9, slice 82) ------------------------------------------
#
# Measured on this laptop's card (RTX 4060, 8188 MiB) before any of this was written, because the
# numbers are what the shape follows:
#
#   one alignment of a 4-minute track    3608 MiB held while the provider lives
#   the aligner alone                     494 MiB   (400 of it torch's pool)
#   the separator, having separated        854 MiB   (726 of it work, given back by `empty_cache`)
#   the second opinion alone             3776 MiB   **none of it in torch's pool**
#   loading them again off a warm disk    ~2 s      (aligner 0.7 s, second opinion 1.2 s)
#
# Two things follow. First, `release_gpu_memory()` alone frees **nothing** while a provider is alive:
# the weights are still referenced, and faster-whisper's 3.6 GB is not torch's memory at all, so only
# dropping the object gives it back. Second, keeping a provider between jobs is cheap to undo — two
# seconds — which is why the card is given back after a short quiet period rather than held for the
# session or dropped after every single track.


def _fingerprint(cfg: Any, what: str) -> tuple:
    """What makes a provider *that* provider: change any of it and the held one is the wrong one."""
    return (kind_for(cfg, what),
            str(getattr(cfg, "timing_device", "auto") or "auto"),
            getattr(cfg, "timing_verify", None),
            float(getattr(cfg, "timing_verify_threshold", VERIFY_THRESHOLD)),
            float(getattr(cfg, "timing_verify_lost", VERIFY_LOST)),
            (getattr(cfg, "timing_endpoint", "") or "").strip())


class Engines:
    """The providers a program is holding, so that the models stay loaded between jobs (§9, slice 82).

    Built per job and dropped with it, `local` reloaded every model for every track — measured at
    about two seconds a track off a warm disk, and a good deal more off a cold one. Held, the models
    stay on the card between tracks and the card goes back when the work stops: `let_go` is what the
    idle window calls, and it is the only thing that really frees a second opinion.

    Not a module-level cache on purpose. The hold belongs to whoever runs the jobs — one per app,
    one per pass in the CLI — so nothing leaks between two programs, or between two tests.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._held: dict[tuple, Timing] = {}

    def provider(self, cfg: Any, what: str = "") -> Timing:
        """The provider for this capability, the same object as last time where the settings agree."""
        mine = _fingerprint(cfg, what)
        wanted = {mine} | {_fingerprint(cfg, w) for w in (ALIGN, TRANSCRIBE)}
        with self._lock:
            # a settings change leaves a provider nobody can ask for any more: its models go now,
            # not when the idle window happens to come round
            for key in [k for k in self._held if k not in wanted]:
                let_engine_go(self._held.pop(key))
            if mine not in self._held:
                self._held[mine] = provider(cfg, what)
            return self._held[mine]

    def holding(self) -> int:
        with self._lock:
            return len(self._held)

    def let_go(self) -> bool:
        """Drop every held provider and give the card back. True when anything was actually freed."""
        with self._lock:
            held = list(self._held.values())
            self._held.clear()
        freed = [let_engine_go(engine) for engine in held]
        return bool(release_gpu_memory() or any(freed))


def let_engine_go(engine: Any) -> bool:
    """Ask a provider to drop its models. A provider without models has nothing to do here."""
    release = getattr(engine, "release", None)
    if release is None:
        return False
    try:
        return bool(release())
    except Exception:  # a tidy-up may never take a job's result with it
        log.debug("could not let go of %s", getattr(engine, "name", engine), exc_info=True)
        return False


class Idle:
    """Knows whether anything is being served, and how long ago the last thing was (§9, slice 41).

    A request enters it while it works, because a tidy-up on a timer that does not know the thing is
    in use will take the models out of a running alignment — which is exactly what happened the first
    time this was tried against a real server (`docs/qa-catalog.md`, section AB): a six-second idle
    window and a ten-second alignment, and the separator vanished mid-separation.
    """

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._lock = threading.Lock()
        self.working = 0
        self.since = now()
        self.worked = False   # something has been done since the last release: there is a point to one

    def __enter__(self) -> Idle:
        with self._lock:
            self.working += 1
        return self

    def __exit__(self, *_: object) -> None:
        with self._lock:
            self.working -= 1
            self.since = self._now()
            self.worked = True

    def quiet_for(self, seconds: float) -> bool:
        with self._lock:
            return self.working == 0 and self._now() - self.since >= seconds

    def wait_again(self) -> None:
        """Start the quiet period over, so a release is not attempted on every look."""
        with self._lock:
            self.since = self._now()

    def release_if_quiet(self, seconds: float, release: Callable[[], Any],
                         busy: Callable[[], bool] = lambda: False) -> bool:
        """Release, but only from inside the lock a request has to pass to start (§9, slice 82).

        The check and the release are one step here. Doing them in two — ask whether it is quiet,
        then release — leaves exactly the window the comment above is about: a job that starts in
        between has its models taken away while it runs. `busy` is asked in the same breath, because
        a job that is queued but not started yet is work that is coming.
        """
        with self._lock:
            # `worked` is what stops an idle program collecting garbage once a minute for ever: with
            # nothing done since the last release there is nothing to give back a second time
            if not self.worked or self.working or self._now() - self.since < seconds or busy():
                return False
            try:
                release()
            finally:
                self.since, self.worked = self._now(), False
            return True


def release_when_idle(seconds: float, release: Callable[[], Any], *,
                      busy: Callable[[], bool] = lambda: False,
                      sleep: Callable[[float], None] = time.sleep,
                      now: Callable[[], float] = time.monotonic) -> Idle:
    """Watch for a quiet period and give the card back when one comes (§9, slice 41, slice 82).

    Returns the `Idle` a job holds while it works. `seconds = 0` turns the whole thing off, which is
    what a machine that exists to serve this wants. The clock and the sleep are arguments so that a
    test can run an hour of it in a millisecond.
    """
    idle = Idle(now)
    if seconds <= 0:
        return idle

    def watch() -> None:
        while True:
            sleep(max(1.0, seconds / 4))
            idle.release_if_quiet(seconds, release, busy)

    _in_the_background(watch)
    return idle


def _in_the_background(watch: Callable[[], None]) -> None:
    """The one line that starts the watch, so a test can take the loop and run it by hand.

    A test that replaced `threading.Thread` itself would replace it for every other thread in the
    program as well — which is how the job workers stopped starting the first time this was tested.
    """
    threading.Thread(target=watch, name="noaap-card-idle", daemon=True).start()


def verifies_with(cfg: Any) -> bool:
    """Whether an alignment from this provider is checked against a second method (§9, slice 38).

    Asked of the provider, because the answer belongs to the machine doing the work: `local` says yes
    when the second extra is installed, `http` repeats what the serving machine reported, and a vendor
    has no such thing. The settings panel shows it, because it costs the user time.
    """
    try:
        return bool(getattr(provider(cfg, ALIGN), "verifying", bool)())
    except (TimingUnavailable, ImportError, OSError):
        return False


__all__ = ["ALIGN", "HEARD_ENOUGH", "HEARD_SKIP", "LISTEN", "OFFERS", "PRICES", "PROVIDERS", "TRANSCRIBE",
           "VENDORS", "VERIFY_LOST", "VERIFY_THRESHOLD", "Engines", "Heard", "HeardWord", "HttpTiming", "Idle",
           "NoTiming", "Timed", "TimedLine", "Timing", "TimingUnavailable", "can", "capabilities_of",
           "heard_from", "kind_for", "language_hint", "language_of", "let_engine_go", "line_starts", "lines_from_words",
           "place_by_listening", "plain_lines", "provider", "release_gpu_memory", "release_when_idle",
           "stamped", "verified", "verifies_with"]


# -- a line is placed when something supports it (§9, slice 81) --------------------------------------
#
# **Forced alignment places everything, because that is what it is.** Given words that are not in the
# recording it finds the least bad path for them anyway — and a user's own case showed what that looks
# like: four lines of an LRCLIB entry that this cut does not sing were pinned to 0.0, 53.2, 53.7 and
# 55.8 s, the first three of them where nobody is singing at all, and the whole song after them came
# out ~4.8 s late. Nothing objected, and the result said *placed 49 of 49*.
#
# So a placement needs evidence of its own, and the evidence that is measurable here is **whether
# anybody was singing where the line was put**. The aligner's own score is recorded beside every line,
# but on this material it does not separate: measured over that case, the four lines that are not in
# the recording scored 0.001–0.006 while genuine lines scored 0.002–0.824, with four genuine lines at
# or below the worst of the four. A gate on that number would throw away real lines.

VOICED_ENOUGH = 0.25   # of the stretch a line claims, measured; see the slice for the distribution
# characters per second, from a line's own start to the next line's — the backstop of §9 slice 81,
# never the first test. Measured over 128 263 line gaps of a real library: median 8.3, p90 14.7,
# p99 25.7, p99.9 145; everything above ~60 is a stamp artefact rather than anybody singing, and the
# fastest lines that *are* sung (a scat, a patter verse) sit at 50 and below.
TOO_FAST = 60.0


def voiced_share(start: float | None, end: float | None, sung: list[tuple[float, float]]) -> float | None:
    """How much of `[start, end]` lies inside a stretch where somebody is singing, or None."""
    if start is None or end is None or end <= start or not sung:
        return None
    inside = sum(max(0.0, min(end, b) - max(start, a)) for a, b in sung)
    return max(0.0, min(1.0, inside / (end - start)))


def unplace_unsupported(timed: Timed, sung: list[tuple[float, float]],
                        scores: dict[int, float] | None = None) -> list[str]:
    """Take back the placements nothing supports, and say why. Returns one sentence per line taken.

    The line keeps its place in the order and its words; only its stamps go, which is what "unplaced"
    has always meant here. Two reasons, in this order:

    1. **nobody was singing there** — the claim is measured against the vocal stem the aligner already
       made, over the stretch from this line's start to the next line's (or its own end, whichever is
       longer, so that a line followed by an instrumental break is not judged on the break);
    2. **the words could not have been sung that fast** — the backstop, and only where the first test
       had nothing to say.
    """
    taken: list[str] = []
    starts = [line.start for line in timed.lines]
    for i, line in enumerate(timed.lines):
        if line.start is None:
            continue
        nxt = next((s for s in starts[i + 1:] if s is not None), None)
        claimed_end = max(line.end or line.start, min(nxt, line.start + 1.0) if nxt else line.start)
        share = voiced_share(line.start, claimed_end, sung)
        if share is not None and share < VOICED_ENOUGH:
            line.start = line.end = None
            taken.append(f"line {i + 1}: nobody is singing there ({share:.0%} of it)")
            continue
        gap = (nxt - line.start) if nxt is not None else None
        if gap and gap > 0.01 and len(line.text) / gap > TOO_FAST:
            line.start = line.end = None
            taken.append(f"line {i + 1}: {len(line.text)} characters in {gap:.2f}s is not singing")
    if scores:
        timed.parameters["line_scores"] = ",".join(
            f"{i + 1}:{scores[i]:.3f}" for i in sorted(scores) if i in scores)
    return taken
