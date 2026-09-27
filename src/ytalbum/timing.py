"""Putting words on a clock — the boundary, not the machinery (DESIGN.md §9.36).

Two capabilities, because they are two jobs with two different markets: **transcribe** derives words
from audio, **align** places words you already have. ytalbum wants the second far more often —
LRCLIB supplies the words for two tracks in three and the timings for fewer — and it is the one that
needs no large model.

Nothing here imports a model, and the core never imports one either. The default provider is
`none`: it can do neither, the page renders no action for it, and ytalbum is exactly what it was
before this file existed. A provider that *can* do something is an optional extra (`local`) or
another machine (`http`), and `docs/spikes/2026-09-alignment.md` is why it is shaped this way.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

ALIGN = "align"
TRANSCRIBE = "transcribe"
PROVIDERS = ("none", "local", "http", "elevenlabs", "deepgram")
VENDORS = ("elevenlabs", "deepgram")  # the ones that need a key and send the audio away

# List prices from the vendors' own pricing pages, read on the date beside them. They are here so
# the settings panel can say what a pass would cost without asking anyone at runtime; nothing in
# ytalbum ever queries a price, and a stale number is better than a request nobody asked for.
# What a kind of provider can ever be asked for, which is not the same as what it can do today: the
# two local ones depend on what is installed or on the machine at the other end, and `capabilities()`
# answers that at runtime. This is for the settings panel, so that the slot for drafting words does
# not offer a provider that only aligns, and the slot for aligning does not offer Deepgram (§9.40).
OFFERS = {"none": (), "local": (ALIGN, TRANSCRIBE), "http": (ALIGN, TRANSCRIBE),
          "elevenlabs": (ALIGN, TRANSCRIBE), "deepgram": (TRANSCRIBE,)}

PRICES = {
    "elevenlabs": ("$0.22 per audio hour (alignment and transcription alike)", "2026-09-27"),
    "deepgram": ("$0.0043 per audio minute, i.e. $0.26 per hour (Nova-3, pay as you go)", "2026-09-27"),
}


class TimingUnavailable(RuntimeError):
    """Asked of a provider that cannot do it, or is not installed. Never a crash, always a message."""


@dataclass
class TimedLine:
    """One line of words and where it starts, in the **file's** clock (§9.35).

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

    @property
    def unplaced(self) -> list[int]:
        return [i for i, line in enumerate(self.lines) if line.start is None]

    @property
    def by(self) -> str:
        """What goes into `lyrics_timed_by`: short, and enough to tell two runs apart."""
        return f"{self.provider}/{self.model}" + (f" {self.version}" if self.version else "")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Timed:
        return cls(lines=[TimedLine(**line) for line in d.get("lines", [])],
                   provider=str(d.get("provider", "")), model=str(d.get("model", "")),
                   version=str(d.get("version", "")), parameters=dict(d.get("parameters", {})))


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
            "`ytalbum[timing]` extra installed) or to `http` with an endpoint — see the README.")

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


STAMP = re.compile(r"^\s*\[(\d{1,3}):(\d{2}(?:[.:]\d{1,3})?)\]\s?")


def plain_lines(text: str) -> list[str]:
    """The words an aligner should be given: no blank lines, no stamps already on them.

    Blank lines are dropped rather than passed through as empty ones, because "could not place"
    should mean what it says; the page puts the stamps back on the lines it sent (§9.36).
    """
    return [STAMP.sub("", line).strip() for line in (text or "").splitlines() if STAMP.sub("", line).strip()]


# -- two methods, checked against each other (§9.38) -----------------------------------------

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


def verified(primary: Timed, second: Timed, threshold: float = VERIFY_THRESHOLD,
             lost: float = VERIFY_LOST) -> Timed:
    """Keep the stamps two independent aligners agree about — and say so when they do not.

    Two regimes, and which one applies is decided by `lost`, not by `threshold`:

    **They agree about the track** (at most half the comparable lines more than `lost` apart). Then
    the per-line rule runs: a line the two place more than `threshold` apart comes back unplaced
    rather than guessed at, and everywhere else the *primary* supplies the number.

    **One of them has lost the song** (more than half the lines that far apart). Then the per-line
    rule is switched off and **every** primary stamp is kept, with the disagreement stated loudly.
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
        if apart > lost:
            gone += 1
    elsewhere = comparable > 0 and gone > comparable * MOSTLY
    out = []
    for i, line in enumerate(lines):
        other = checked.get(i)
        apart = None if (line.start is None or other is None) else abs(line.start - other)
        keep = elsewhere or apart is None or apart <= threshold
        out.append(TimedLine(text=line.text, start=line.start if keep else None,
                             end=line.end if keep else None))
    parameters = dict(primary.parameters)
    parameters.update({"verified_against": second.model if second else "",
                       "threshold": f"{threshold:g}", "lost_beyond": f"{lost:g}",
                       "compared": str(comparable), "disagreed": str(disagreed), "lost": str(gone)})
    if elsewhere:
        parameters["one_method"] = "a second method disagreed about the whole track"
    return Timed(lines=out, provider=primary.provider,
                 model=f"{primary.model} + {second.model}" if second else primary.model,
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


SENTENCE_END = re.compile(r"[.!?…]$")


def lines_from_words(words: list[dict[str, Any]], per_line: int = 9) -> list[TimedLine]:
    """A transcript's words grouped into lines a lyric editor can hold.

    Vendors return words, not verses. Breaking at sentence punctuation and otherwise every few
    words gives something a person can read and re-break by hand; it is a draft, and it says so.
    """
    lines: list[TimedLine] = []
    current: list[str] = []
    start: float | None = None
    end: float | None = None
    for w in words:
        text = str(w.get("punctuated_word") or w.get("text") or w.get("word") or "").strip()
        if not text:
            continue
        if start is None:
            start = None if w.get("start") is None else float(w["start"])
        if w.get("end") is not None:
            end = float(w["end"])
        current.append(text)
        if SENTENCE_END.search(text) or len(current) >= per_line:
            lines.append(TimedLine(text=" ".join(current), start=start, end=end))
            current, start, end = [], None, None
    if current:
        lines.append(TimedLine(text=" ".join(current), start=start, end=end))
    return lines


# -- a provider on another machine ------------------------------------------------------------


class HttpTiming:
    """`ytalbum timing-serve`, somewhere else on the network.

    The inference is the same code the `local` provider runs; what changes is which machine pays
    for it. Nothing leaves the network, and this side inherits no dependency heavier than the
    `httpx` ytalbum already has.
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
        return Timed.from_dict(r.json())

    def transcribe(self, audio: Path, *, language: str | None = None,
                   check: Callable[[], None] | None = None) -> Timed:
        if TRANSCRIBE not in self.capabilities():
            raise TimingUnavailable(f"{self.endpoint} only aligns words it is given")
        return self._send("transcribe", audio, [], language, check)


# -- choosing one ------------------------------------------------------------------------------


def kind_for(cfg: Any, what: str = "") -> str:
    """Which provider is configured for a capability (§9.40).

    Two slots — `timing_align_provider` and `timing_draft_provider` — because the two capabilities
    are bought in different places: the machine that aligns best (`local`, free, needs the models) is
    rarely the one that transcribes best (a vendor, metered, needs nothing installed). An empty slot
    falls back to `timing_provider`, which is what every config written before this said and still
    means both, so nothing anyone has configured breaks.
    """
    slot = {ALIGN: "timing_align_provider", TRANSCRIBE: "timing_draft_provider"}.get(what, "")
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
    return frozenset(what for what in (ALIGN, TRANSCRIBE) if can(cfg, what))


def verifies_with(cfg: Any) -> bool:
    """Whether an alignment from this provider is checked against a second method (§9.38).

    Asked of the provider, because the answer belongs to the machine doing the work: `local` says yes
    when the second extra is installed, `http` repeats what the serving machine reported, and a vendor
    has no such thing. The settings panel shows it, because it costs the user time.
    """
    try:
        return bool(getattr(provider(cfg, ALIGN), "verifying", bool)())
    except (TimingUnavailable, ImportError, OSError):
        return False


__all__ = ["ALIGN", "OFFERS", "PRICES", "PROVIDERS", "TRANSCRIBE", "VENDORS", "VERIFY_LOST", "VERIFY_THRESHOLD", "HttpTiming",
           "NoTiming", "Timed", "TimedLine", "Timing", "TimingUnavailable", "can", "capabilities_of", "kind_for",
           "language_of", "line_starts", "lines_from_words", "plain_lines", "provider", "verified", "verifies_with"]
