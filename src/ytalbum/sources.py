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


@runtime_checkable
class Source(Protocol):
    """One place audio comes from. Four methods are required; the rest are capabilities."""

    name: str

    def capabilities(self) -> frozenset[str]: ...

    # -- required ----------------------------------------------------------------------
    def collection(self, url: str) -> Collection:
        """Everything at that address: the album/playlist/folder and its entries."""

    def audio(self, ref: str, into: Path, choice: str = "best") -> Path:
        """Fetch one item's audio into `into`, and return the file."""

    def probe(self, ref: str) -> Entry:
        """One item's metadata, read again — what `audio` would be fetching."""

    def art(self, url: str) -> bytes:
        """The bytes behind a cover URL this provider gave us."""

    # -- capabilities ------------------------------------------------------------------
    def changed(self, url: str) -> dict[str, Any] | None:
        """CHANGES: the collection's state now, cheaply — the caller compares it with what it stored."""

    def listing(self, url: str) -> list[SourceRef]:
        """SEARCH: the collections an artist or channel publishes."""

    def find(self, query: str, limit: int = 12) -> list[SourceRef]:
        """SEARCH: collections matching a name."""

    def find_playlists(self, query: str, limit: int = 10) -> list[SourceRef]:
        """SEARCH: collections that are not releases — a playlist somebody made."""

    def details(self, url: str) -> dict[str, Any] | None:
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
    """The provider a plan belongs to. Absent `provider` means it was written before slice 51."""
    return get(getattr(plan, "provider", None) or DEFAULT, cfg, cancel)


def can(source: Source, what: str) -> bool:
    return what in source.capabilities()


def _load() -> None:
    if _MAKERS:
        return
    from . import sources_youtube  # noqa: F401  — registers itself on import
