"""YouTube as one provider among others (DESIGN §9, slice 51).

A thin adapter over `youtube.YouTube`, which keeps doing the work. Everything YouTube-shaped that
the pipeline used to know — an eleven-character id, a `watch?v=` link, a channel URL, the title
conventions — is reachable only through here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from yt_dlp.utils import DownloadError

from . import sources
from .config import Config
from .models import Collection, Entry, SourceRef
from .titles import channel_artist, parse_video_title
from .youtube import YouTube, channel_base_url, one_video

WATCH = "https://www.youtube.com/watch?v="


class YouTubeSource:
    """The seven calls, plus the capabilities YouTube happens to have."""

    name = sources.DEFAULT

    def __init__(self, cfg: Config, cancel: Any = None) -> None:
        self.cfg = cfg
        self.yt = YouTube(cfg, cancel)

    def handles(self, address: str) -> bool:
        """Cheap: is this one of ours? The details runner asks before spending a request."""
        return any(host in address for host in ("youtube.com", "youtu.be"))

    def capabilities(self) -> frozenset[str]:
        return frozenset({sources.SEARCH, sources.CHANGES, sources.DETAILS, sources.CLEAN})

    # -- required ----------------------------------------------------------------------

    def collection(self, address: str) -> Collection:
        return self.yt.fetch(address)

    def audio(self, ref: str, into: Path, choice: str = "best") -> Path:
        try:
            return self.yt.download_audio(ref, into, choice)
        except DownloadError as e:
            # yt-dlp's own exception stops here: the boundary's job is to keep its vocabulary in
            # (§9, slice 51). `Blocked` and `NoAudio` are already raised inside by name.
            raise sources.SourceError(str(e).removeprefix("ERROR: ").strip()) from e

    def probe(self, ref: str) -> Entry:
        try:
            return self.yt.probe(ref)
        except DownloadError as e:
            raise sources.SourceError(str(e).removeprefix("ERROR: ").strip()) from e

    def art(self, address: str) -> bytes:
        return self.yt.fetch_bytes(address)

    # -- capabilities ------------------------------------------------------------------

    def changed(self, address: str) -> dict[str, Any] | None:
        return self.yt.source_state(address)

    def listing(self, address: str) -> list[SourceRef]:
        return self.yt.list_channel(address)

    def find(self, query: str, limit: int = 12) -> list[SourceRef]:
        return self.yt.search_albums(query, limit)

    def find_playlists(self, query: str, limit: int = 10) -> list[SourceRef]:
        return self.yt.search_playlists(query, limit)

    def details(self, address: str) -> dict[str, Any] | None:
        return self.yt.playlist_details(address)

    def clean_entry(self, entry: Entry) -> tuple[str | None, str]:
        """YouTube titles carry conventions; §5 is what reads them."""
        return parse_video_title(entry.title, entry.channel)

    def is_release(self, collection: Collection) -> bool:
        """YouTube Music's album playlists carry an id that says so."""
        return collection.source_id.startswith("OLAK5uy_")

    def owner_artist(self, owner: str | None) -> str | None:
        return channel_artist(owner)

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
