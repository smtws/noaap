"""SoundCloud as one provider among others (DESIGN §9, slice 57).

A thin adapter over `soundcloud.SoundCloud`, which does the work. Everything SoundCloud-shaped —
a host, a set URL, a numeric track id, the title conventions — is reachable only through here and
that module, and a grep in the suite keeps it that way.

**It declares `LISTING`, not `SEARCH`.** SoundCloud's own search finds tracks, never sets, so there
is no honest way to answer "which albums is this artist's". A provider does not declare what it
cannot do, and the core says which providers can search when a bare name is typed (R-183, ruling a).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import sources
from .config import Config
from .models import Collection, Entry, SourceRef
from .soundcloud import SoundCloud, art_candidates, is_address, one_ref, owner_url
from .titles_soundcloud import owner_is_artist, parse_track_title

NAME = "soundcloud"


class SoundCloudSource:
    """The four required calls, plus the capabilities SoundCloud happens to have."""

    name = NAME

    def __init__(self, cfg: Config, cancel: Any = None) -> None:
        self.cfg = cfg
        self.sc = SoundCloud(cfg, cancel)

    def handles(self, address: str) -> bool:
        return is_address(address)

    def capabilities(self) -> frozenset[str]:
        # no SEARCH: its search finds tracks, not sets. No DETAILS: a listing already carries the
        # cover and the count, so a second request would buy nothing.
        return frozenset({sources.LISTING, sources.CHANGES, sources.CLEAN})

    # -- required ----------------------------------------------------------------------

    def collection(self, address: str) -> Collection:
        return self.sc.fetch(address)

    def audio(self, ref: str, into: Path, choice: str = "best") -> Path:
        return self.sc.download_audio(ref, into, choice)

    def probe(self, ref: str) -> Entry:
        return self.sc.probe(ref)

    def art(self, address: str) -> bytes:
        return self.sc.fetch_bytes(address)

    # -- capabilities ------------------------------------------------------------------

    def changed(self, address: str) -> dict[str, Any] | None:
        return self.sc.source_state(address)

    def listing(self, address: str) -> list[SourceRef]:
        return self.sc.list_owner(address)

    def clean_entry(self, entry: Entry) -> tuple[str | None, str]:
        return parse_track_title(entry.title, entry.channel)

    def is_release(self, collection: Collection) -> bool:
        """Published on the owner's **albums** tab, which is the site's own statement about it.

        yt-dlp does not surface SoundCloud's `set_type`, and a set that is not on that tab is
        somebody's playlist — the same distinction YouTube draws with a Releases tab.
        """
        if not collection.is_playlist or not collection.source_url:
            return False
        return collection.source_id in self.sc.released_by(_owner_of(collection.source_url))

    def owner_artist(self, owner: str | None) -> str | None:
        """On SoundCloud an uploader usually *is* the artist — there is no "- Topic" convention."""
        return owner_is_artist(owner)

    def art_candidates(self, url: str) -> list[str]:
        return art_candidates(url)

    def url_for(self, ref: str) -> str | None:
        """None, and deliberately.

        The ref is the numeric track id, because a permalink can be renamed by its uploader and an
        identity that moves loses the library's grip on what it already has. There is no page
        address that can be built from that id alone, and the protocol would rather have nothing
        than a link that 404s. The panel shows the ref without a link, as it does for a folder.
        """
        return None

    def one_ref(self, text: str) -> str | None:
        return one_ref(text)

    def collection_url(self, text: str) -> str | None:
        return owner_url(text)

    def origins(self) -> dict[str, str]:
        """Its evidence: the set's own fields, the track's title, the set's name."""
        return {"tags": "sc_set", "title": "sc_title", "collection": "playlist"}

    def settings(self) -> dict[str, Any]:
        return {"cookies_from_browser": self.cfg.soundcloud_cookies_from_browser,
                "cookies_file": str(self.cfg.soundcloud_cookies_file)
                if self.cfg.soundcloud_cookies_file else None}


def _owner_of(set_url: str) -> str:
    """The owner's page from one of their set addresses."""
    return set_url.split("/sets/")[0]


sources.register(NAME, SoundCloudSource)
