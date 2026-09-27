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
PROVIDERS = ("none", "local", "http")


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
        import httpx

        if check:
            check()  # the last cheap moment before a request that may take minutes
        data = {"lines": "\n".join(lines), "language": language or ""}
        try:
            with audio.open("rb") as fh:
                r = httpx.post(f"{self.endpoint}/align", data=data,
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
        raise TimingUnavailable("this endpoint only aligns words it is given")


# -- choosing one ------------------------------------------------------------------------------


def provider(cfg: Any) -> Timing:
    """The configured provider. Importing the heavy one happens here and nowhere else."""
    kind = getattr(cfg, "timing_provider", "none") or "none"
    if kind == "local":
        from .timing_local import LocalTiming

        return LocalTiming(device=getattr(cfg, "timing_device", "auto") or "auto")
    if kind == "http":
        endpoint = (getattr(cfg, "timing_endpoint", "") or "").strip()
        if not endpoint:
            raise TimingUnavailable("timing_provider is `http` but timing_endpoint is empty")
        return HttpTiming(endpoint)
    return NoTiming()


def capabilities_of(cfg: Any) -> frozenset[str]:
    """What the UI asks. A provider that cannot be reached or installed offers nothing."""
    try:
        return provider(cfg).capabilities()
    except (TimingUnavailable, ImportError, OSError):
        return frozenset()


__all__ = ["ALIGN", "PROVIDERS", "TRANSCRIBE", "HttpTiming", "NoTiming", "Timed", "TimedLine",
           "Timing", "TimingUnavailable", "capabilities_of", "language_of", "plain_lines", "provider"]
