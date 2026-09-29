"""Patreon as one provider among others (DESIGN §9, slice 70).

A thin adapter over `patreon.Patreon`, which does the work. Everything Patreon-shaped — the host, a
post address, a campaign address, a media ref — is reachable only through here and that module, and a
grep in the suite keeps it that way.

**It declares neither SEARCH nor DETAILS.** Patreon has no public search of posts by name, and the one
search endpoint yt-dlp uses resolves a vanity to a campaign id and nothing else; a track count needs
the post read in full, so DETAILS would buy nothing. A provider does not declare what it cannot do.

**And it works only with the patron's own session.** Without one every call refuses with the sentence
that names the setting, because there is nothing honest to fall back on (R-239, ruling 1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import sources
from .config import Config
from .models import Collection, Entry, SourceRef
from .patreon import NAME, Patreon, campaign_of, is_address, one_ref
from .titles_patreon import clean_title, creator_is_artist


class PatreonSource:
    """The four required calls, plus the capabilities Patreon happens to have."""

    name = NAME

    def __init__(self, cfg: Config, cancel: Any = None) -> None:
        self.cfg = cfg
        self.pt = Patreon(cfg, cancel)

    def handles(self, address: str) -> bool:
        return is_address(address)

    def capabilities(self) -> frozenset[str]:
        return frozenset({sources.LISTING, sources.CHANGES, sources.CLEAN})

    # -- required ----------------------------------------------------------------------

    def collection(self, address: str) -> Collection:
        return self.pt.fetch(address)

    def audio(self, ref: str, into: Path, choice: str = "best") -> Path:
        return self.pt.download_audio(ref, into, choice)

    def probe(self, ref: str) -> Entry:
        return self.pt.probe(ref)

    def art(self, address: str) -> bytes:
        return self.pt.fetch_bytes(address)

    # -- capabilities ------------------------------------------------------------------

    def changed(self, address: str) -> dict[str, Any] | None:
        return self.pt.source_state(address)

    def listing(self, address: str) -> list[SourceRef]:
        return self.pt.list_owner(address)

    def clean_entry(self, entry: Entry) -> tuple[str | None, str]:
        return clean_title(entry.title, entry.channel)

    def owner_artist(self, owner: str | None) -> str | None:
        return creator_is_artist(owner)

    # -- what a person typed -----------------------------------------------------------

    def one_ref(self, text: str) -> str | None:
        return one_ref(text)

    def collection_url(self, text: str) -> str | None:
        """A campaign address is an owner's page; a post address is a collection, not an owner."""
        return campaign_of(text)

    def url_for(self, ref: str) -> str | None:
        """A media has no page of its own; its post does, and that is the honest link.

        Only the provider may build a link from a ref (§9, slice 51) — and here it cannot: a media ref
        does not carry its post. So there is no link, rather than a wrong one.
        """
        return None

    def is_release(self, collection: Collection) -> bool:
        """**Never.** Nothing on Patreon is a release: a post is a thing somebody posted, and calling
        it an album would put a year and a track order on it that nobody wrote (R-239, ruling 3)."""
        return False

    def settings(self) -> dict[str, Any]:
        return {"cookies_from_browser": self.cfg.patreon_cookies_from_browser,
                "cookies_file": str(self.cfg.patreon_cookies_file)
                if self.cfg.patreon_cookies_file else None}

    def origins(self) -> dict[str, str]:
        """Its evidence: the post's own fields, the media's file name, the creator."""
        return {"tags": "patreon_post", "title": "patreon_title", "collection": "post"}


sources.register(NAME, PatreonSource)
