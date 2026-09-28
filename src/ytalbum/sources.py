"""Where audio comes from, behind one small interface (DESIGN §9, slice 51).

Seven calls cross this boundary. That is the whole of it, and it was already true before the
boundary existed — `service.py` and `download.py` only ever asked `YouTube` for those seven things.
Naming them is what lets a second provider exist.

**A `ref` is opaque.** Only the provider that minted one may parse it. Today every ref is an
eleven-character YouTube video id, and nothing outside `sources/youtube.py` knows that — which is
the assumption this boundary exists to remove.

**Capabilities are asked for, not assumed.** A folder cannot search and has no channels; a provider
says what it can do and the caller checks. A missing capability is a normal answer, not an error.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .config import Config
    from .models import Collection, Entry, SourceRef

# what a provider may be able to do beyond the four every one must
SEARCH = "search"        # find collections by an artist's name
CHANGES = "changes"      # say cheaply whether a collection changed
DETAILS = "details"      # enrich a search hit with a cover and a track count
CLEAN = "clean"          # its titles carry conventions worth stripping (DESIGN §5)

DEFAULT = "youtube"      # a plan with no `provider` was written before providers existed


class SourceError(Exception):
    """Anything a provider could not do. The message is the provider's own words."""


class Blocked(SourceError):
    """The source wants a sign-in before it will answer (a bot check, a login wall)."""


class NoAudio(SourceError):
    """It is there, but not as audio this provider can take."""

    def __init__(self, description: str) -> None:
        super().__init__(description)
        self.description = description


class NotSupported(SourceError):
    """A valid address of a kind this provider does not handle."""


class Cancelled(Exception):
    """The user stopped the job. Not a SourceError: nothing failed."""


@runtime_checkable
class Source(Protocol):
    """One place audio comes from. Four methods are required; the rest are capabilities."""

    name: str

    def capabilities(self) -> frozenset[str]: ...

    def handles(self, address: str) -> bool:
        """Whether this address is one of this provider's, cheaply and without a request."""

    # -- required ----------------------------------------------------------------------
    def collection(self, address: str) -> Collection:
        """Everything at that address — a URL for YouTube, a path for a folder."""

    def audio(self, ref: str, into: Path, choice: str = "best") -> Path:
        """Fetch one item's audio into `into`, and return the file."""

    def probe(self, ref: str) -> Entry:
        """One item's metadata, read again — what `audio` would be fetching."""

    def art(self, address: str) -> bytes:
        """The bytes behind a cover URL this provider gave us."""

    # -- capabilities ------------------------------------------------------------------
    def changed(self, address: str) -> dict[str, Any] | None:
        """CHANGES: the collection's state now, cheaply — the caller compares it with what it stored."""

    def listing(self, address: str) -> list[SourceRef]:
        """SEARCH: the collections an artist or channel publishes."""

    def find(self, query: str, limit: int = 12) -> list[SourceRef]:
        """SEARCH: collections matching a name."""

    def find_playlists(self, query: str, limit: int = 10) -> list[SourceRef]:
        """SEARCH: collections that are not releases — a playlist somebody made."""

    def details(self, address: str) -> dict[str, Any] | None:
        """DETAILS: cover and track count for a search hit."""

    def clean_entry(self, entry: Entry) -> tuple[str | None, str]:
        """CLEAN: (artist, title) as this provider's own conventions read them.

        A provider without this capability has titles that mean what they say — a local folder with
        correct tags, say — and the entry's own fields are used unchanged.
        """

    def url_for(self, ref: str) -> str | None:
        """A link a person can open, or None where the provider has no web page."""

    def one_ref(self, text: str) -> str | None:
        """A single item's ref out of whatever the user typed, or None if it is not one."""

    def settings(self) -> dict[str, Any]:
        """This provider's own configuration, for the settings panel."""


_MAKERS: dict[str, Any] = {}


def register(name: str, make: Any) -> None:
    _MAKERS[name] = make


def known() -> list[str]:
    return sorted(_MAKERS)


def get(name: str | None, cfg: Config, cancel: Any = None) -> Source:
    """The provider by name. An unknown name is a configuration error, not a silent fallback."""
    _load()
    wanted = name or DEFAULT
    if wanted not in _MAKERS:
        raise ValueError(f"unknown source provider {wanted!r}; known: {', '.join(known()) or 'none'}")
    return _MAKERS[wanted](cfg, cancel)


def for_plan(plan: Any, cfg: Config, cancel: Any = None) -> Source:
    """The provider a **collection** belongs to: what `update`, `listing` and the cover ask.

    A *track's audio* is not this. It comes from its chosen candidate, which carries its own
    provider — one album can hold tracks from two of them, which is the point of P48's shape and
    what P51 will actually do. Use `for_candidate`.
    """
    return get(getattr(plan, "provider", None) or DEFAULT, cfg, cancel)


def for_candidate(track: Any, plan: Any, cfg: Config, cancel: Any = None) -> Source:
    """The provider of the candidate a track's audio is taken from.

    Falls back to the plan's own provider for a track whose candidate list has not been written yet
    — which is every track in every plan on disk today.
    """
    chosen = getattr(track, "chosen", None) or getattr(track, "effective_id", None)
    found = track.candidate(chosen) if chosen and hasattr(track, "candidate") else None
    return get(getattr(found, "provider", None) or getattr(plan, "provider", None) or DEFAULT, cfg, cancel)


def can(source: Source, what: str) -> bool:
    return what in source.capabilities()


def _load() -> None:
    if _MAKERS:
        return
    from . import sources_youtube  # noqa: F401  — registers itself on import
