"""YouTube as one provider among others (DESIGN §9, slice 51).

A thin adapter over `youtube.YouTube`, which keeps doing the work. Everything YouTube-shaped that
the pipeline used to know — an eleven-character id, a `watch?v=` link, a channel URL, the title
conventions — is reachable only through here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import sources
from .config import Config
from .models import Collection, Entry, SourceRef
from .titles import parse_video_title
from .youtube import YouTube, channel_base_url, one_video

WATCH = "https://www.youtube.com/watch?v="


class YouTubeSource:
    """The seven calls, plus the capabilities YouTube happens to have."""

    name = sources.DEFAULT

    def __init__(self, cfg: Config, cancel: Any = None) -> None:
        self.cfg = cfg
        self.yt = YouTube(cfg, cancel)

    def capabilities(self) -> frozenset[str]:
        return frozenset({sources.SEARCH, sources.CHANGES, sources.DETAILS, sources.CLEAN})

    # -- required ----------------------------------------------------------------------

    def collection(self, url: str) -> Collection:
        return self.yt.fetch(url)

    def audio(self, ref: str, into: Path, choice: str = "best") -> Path:
        return self.yt.download_audio(ref, into, choice)

    def probe(self, ref: str) -> Entry:
        return self.yt.probe(ref)

    def art(self, url: str) -> bytes:
        return self.yt.fetch_bytes(url)

    # -- capabilities ------------------------------------------------------------------

    def changed(self, url: str) -> dict[str, Any] | None:
        return self.yt.source_state(url)

    def listing(self, url: str) -> list[SourceRef]:
        return self.yt.list_channel(url)

    def find(self, query: str, limit: int = 12) -> list[SourceRef]:
        return self.yt.search_albums(query, limit)

    def find_playlists(self, query: str, limit: int = 10) -> list[SourceRef]:
        return self.yt.search_playlists(query, limit)

    def details(self, url: str) -> dict[str, Any] | None:
        return self.yt.playlist_details(url)

    def clean_entry(self, entry: Entry) -> tuple[str | None, str]:
        """YouTube titles carry conventions; §5 is what reads them."""
        return parse_video_title(entry.title, entry.channel)

    def url_for(self, ref: str) -> str | None:
        return f"{WATCH}{ref}" if ref else None

    def one_ref(self, text: str) -> str | None:
        return one_video(text)

    def collection_url(self, text: str) -> str | None:
        """A channel's base URL, if that is what this is — YouTube's own notion of an owner."""
        return channel_base_url(text)

    def settings(self) -> dict[str, Any]:
        return {"cookies_from_browser": self.cfg.cookies_from_browser,
                "cookies_file": str(self.cfg.cookies_file) if self.cfg.cookies_file else None,
                "pot_mode": self.cfg.pot_mode, "concurrency": self.cfg.concurrency}


sources.register(sources.DEFAULT, YouTubeSource)
