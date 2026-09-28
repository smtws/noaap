"""All SoundCloud I/O, through yt-dlp (DESIGN §9, slice 57).

What this provider is **for**: music that is not on YouTube. It is not a source of better copies.
Without an account SoundCloud offers 160 kbps AAC at best, and the label uploads this library is
made of are DRM protected and yield nothing at all — measured, not assumed (I-141). Nothing here
touches DRM.

Everything returned is mapped into `models` right here, so no other module sees a raw yt-dlp dict,
and nothing outside this file and its adapter knows what a SoundCloud address looks like.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled, DownloadError

from . import sources, ytdlp
from .config import Config
from .models import Collection, Entry, Music, SourceRef

log = logging.getLogger(__name__)

HOST = "soundcloud.com"
BASE = f"https://{HOST}/"
# a user's two tabs, under the names the core's discovery view already groups by
TABS = (("albums", "releases"), ("sets", "playlists"))

# `soundcloud.com/<user>/<slug>` is a track, `/sets/<slug>` a set, `/<user>` alone an owner.
_USER = re.compile(r"^https?://(?:www\.|m\.)?soundcloud\.com/([\w.-]+)/?$", re.I)
_SET = re.compile(r"^https?://(?:www\.|m\.)?soundcloud\.com/([\w.-]+)/sets/([\w:.-]+)", re.I)
_TRACK = re.compile(r"^https?://(?:www\.|m\.)?soundcloud\.com/([\w.-]+)/(?!sets/)([\w.-]+)", re.I)
_API_TRACK = re.compile(r"^https?://api(?:-v2)?\.soundcloud\.com/tracks/(?:soundcloud(?::|%3A)tracks(?::|%3A))?(\d+)", re.I)

# what `audio_choice` means here. `best` is the 160 kbps AAC; the other value is the one stream
# that needs no HLS assembly at all, which is the progressive MP3 (R-183, ruling b).
FORMATS = {"best": "bestaudio/best",
           "combined": "bestaudio[protocol^=http][acodec=mp3]/bestaudio[protocol^=http]/bestaudio"}

DRM = "this track is DRM protected — SoundCloud offers no stream for it"
_RATE = re.compile(r"HTTP Error (429|403)|rate.?limit|too many requests", re.I)
_GONE = re.compile(r"not available|unavailable|geo|blocked in your country|region", re.I)


def is_address(text: str) -> bool:
    """Cheap: is this one of ours? No request, and no guessing from a bare word."""
    return HOST in (text or "") or "api.soundcloud.com" in (text or "")


def owner_url(text: str) -> str | None:
    """The owner's page, if that is what this is — SoundCloud's notion of a channel."""
    if m := _USER.match(text or ""):
        return f"{BASE}{m[1]}"
    return None


def one_ref(text: str) -> str | None:
    """A single track's ref out of whatever the user typed, else None.

    The ref is the **numeric track id**: a permalink can be renamed by its uploader, and an
    identity that moves is how a library loses track of what it already has.
    """
    text = (text or "").strip()
    if m := _API_TRACK.match(text):
        return m[1]
    if text.isdigit():
        return text
    return None


class SoundCloud:
    """The client. One instance per job; `cancel` stops a download between fragments."""

    def __init__(self, cfg: Config, cancel: threading.Event | None = None) -> None:
        self.cfg = cfg
        self.cancel = cancel
        self._albums: dict[str, set[str]] = {}  # uploader -> the ids on their albums tab

    # -- the yt-dlp plumbing ------------------------------------------------------------

    def _params(self, **extra: Any) -> dict[str, Any]:
        """The shared options, with **this site's** cookies and nobody else's (§9, slice 57)."""
        return ytdlp.params(cookies_file=self.cfg.soundcloud_cookies_file,
                            cookies_from_browser=self.cfg.soundcloud_cookies_from_browser,
                            progress=self._progress, **extra)

    def _progress(self, *_: Any) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise DownloadCancelled("cancelled")

    def _read(self, url: str, **extra: Any) -> dict[str, Any]:
        """One extraction, with yt-dlp's vocabulary kept inside (§9, slice 51)."""
        try:
            with YoutubeDL(self._params(skip_download=True, **extra)) as ydl:
                info = ydl.extract_info(url, download=False)
        except DownloadCancelled as e:
            raise sources.Cancelled("cancelled") from e
        except DownloadError as e:
            raise self._failure(e) from e
        if info is None:
            raise sources.SourceError(f"nothing at {url}")
        return info

    def _failure(self, e: DownloadError | str) -> Exception:
        """yt-dlp's complaint, read as one of the boundary's four kinds (R-183, ruling f)."""
        whole = ytdlp.trim(e)
        if "DRM" in whole:
            return sources.NoAudio(DRM)
        if _RATE.search(whole):
            # the same shape as YouTube's bot check: stop the run rather than keep asking
            return sources.Blocked("SoundCloud is refusing requests for now — wait a while")
        if _GONE.search(whole):
            return sources.NoAudio(ytdlp.one_sentence(whole))
        return sources.SourceError(ytdlp.one_sentence(whole))

    # -- stage 1: what is at an address --------------------------------------------------

    def fetch(self, address: str) -> Collection:
        """A set, or a single track as a collection of one.

        A set is read **twice**: flat for the running order and the ids, then in full for the
        titles and durations. That is two requests instead of one, and it buys the thing one
        request cannot give — when a track in the set cannot be read (DRM, removed), the flat
        list still knows its id and its place, so the entry is *skipped by name* instead of the
        whole album failing or a track silently vanishing.
        """
        if _SET.match(address):
            return self._set(address)
        return self._single(address)

    def _set(self, address: str) -> Collection:
        order = self._read(address, extract_flat=True)
        ids = [str(e.get("id") or "") for e in (order.get("entries") or [])]
        full = self._read(address, ignoreerrors=True)
        got = {str(e["id"]): e for e in (full.get("entries") or []) if e and e.get("id")}
        year = full.get("release_year") or order.get("release_year")
        album = full.get("title") or order.get("title") or ""

        entries: list[Entry] = []
        for n, ref in enumerate(ids, 1):
            if (found := got.get(ref)) is None:
                entries.append(Entry(video_id=ref, position=n, title=f"track {n}",
                                     skipped="could not be read", skipped_kind=None, transient=False))
                continue
            entries.append(self._entry(found, n, album=album, year=year))
        return Collection(
            source_url=full.get("webpage_url") or address,
            source_id=str(full.get("id") or order.get("id") or address),
            is_playlist=True,
            title=album,
            channel=full.get("uploader") or order.get("uploader"),
            thumbnail=_thumbnail(full) or _thumbnail(order),
            fetched_at=datetime.now(UTC).date().isoformat(),
            entries=entries,
            modified=_stamp(full) or _stamp(order),
        )

    def _single(self, address: str) -> Collection:
        info = self._read(address, noplaylist=True)
        return Collection(
            source_url=info.get("webpage_url") or address,
            source_id=str(info.get("id") or address),
            is_playlist=False,
            title=info.get("title") or "",
            channel=info.get("uploader"),
            thumbnail=_thumbnail(info),
            fetched_at=datetime.now(UTC).date().isoformat(),
            entries=[self._entry(info, 1)],
            modified=_stamp(info),
        )

    def _entry(self, info: dict[str, Any], position: int, album: str = "", year: Any = None) -> Entry:
        """One track.

        A SoundCloud track carries **no album, no track number and no album artist** — measured on
        real pages, not assumed. What it does carry is a title and an uploader. So the album name
        and the year come from the *set*, which is the only place they exist, and the number is the
        position in it. The artist is deliberately left out of `music`: the title may name one
        ("Bloodywood - Gaddaar"), and reading that is `clean_entry`'s job, as it is for YouTube.
        """
        return Entry(
            video_id=str(info.get("id") or ""),
            position=position,
            title=info.get("title") or "",
            channel=info.get("uploader"),
            duration=info.get("duration"),
            thumbnail=_thumbnail(info),
            music=Music(album=album or None, year=int(year) if year else None),
        )

    # -- the cheap check -----------------------------------------------------------------

    def source_state(self, address: str) -> dict[str, Any] | None:
        """What `update` compares: the ids in the set now, and when it last changed.

        One flat request. The same `{ids, modified}` shape YouTube's plans already store, so
        nothing in `_unchanged` had to learn a second one.
        """
        try:
            info = self._read(address, extract_flat=True)
        except sources.SourceError:
            return None
        ids = [str(e.get("id") or "") for e in (info.get("entries") or []) if e]
        return {"ids": ids, "modified": _stamp(info)}

    # -- what an owner publishes ---------------------------------------------------------

    def list_owner(self, address: str) -> list[SourceRef]:
        """Their albums, then their other sets — the two groups the discovery view already shows."""
        base = owner_url(address) or address.rstrip("/")
        refs: list[SourceRef] = []
        seen: set[str] = set()
        for tab, group in TABS:
            try:
                info = self._read(f"{base}/{tab}", extract_flat=True)
            except sources.SourceError as e:
                log.info("%s/%s: %s", base, tab, e)
                continue
            for e in info.get("entries") or []:
                url = e.get("url") or ""
                if not url or url in seen:
                    continue
                seen.add(url)
                refs.append(SourceRef(url=url, source_id=str(e.get("id") or url),
                                      title=e.get("title") or url, tab=group,
                                      artist=e.get("uploader") or info.get("uploader"),
                                      channel_url=base, count=e.get("playlist_count"),
                                      thumbnail=_thumbnail(e)))
        return refs

    def released_by(self, uploader_url: str) -> set[str]:
        """The set ids on an owner's **albums** tab, cached for this run.

        This is how `is_release` is answered: yt-dlp does not surface SoundCloud's own `set_type`,
        and the tab a set is published under is the site's own statement about it.
        """
        base = owner_url(uploader_url) or uploader_url.rstrip("/")
        if base not in self._albums:
            try:
                info = self._read(f"{base}/albums", extract_flat=True)
                self._albums[base] = {str(e.get("id") or "") for e in (info.get("entries") or []) if e}
            except sources.SourceError:
                self._albums[base] = set()
        return self._albums[base]

    # -- the audio ------------------------------------------------------------------------

    def download_audio(self, ref: str, into: Path, choice: str = "best") -> Path:
        """Fetch one track. `ref` is the numeric id, which is what yt-dlp takes back."""
        into.mkdir(parents=True, exist_ok=True)
        template = str(into / "%(id)s.%(ext)s")
        params = self._params(format=FORMATS.get(choice, FORMATS["best"]),
                              outtmpl={"default": template}, noplaylist=True)
        try:
            with YoutubeDL(params) as ydl:
                info = ydl.extract_info(track_url(ref), download=True)
        except DownloadCancelled as e:
            raise sources.Cancelled("cancelled") from e
        except DownloadError as e:
            raise self._failure(e) from e
        if info is None:
            raise sources.NoAudio(f"nothing came back for {ref}")
        got = sorted(into.glob(f"{ref}.*"))
        if not got:
            raise sources.NoAudio(f"no file arrived for {ref}")
        return got[0]

    def probe(self, ref: str) -> Entry:
        return self._entry(self._read(track_url(ref), noplaylist=True), 1)

    def fetch_bytes(self, url: str) -> bytes:
        with YoutubeDL(self._params()) as ydl:
            return ydl.urlopen(url).read()


def track_url(ref: str) -> str:
    """The address yt-dlp takes for one track id. Not a page a person opens — see `url_for`."""
    return f"https://api.soundcloud.com/tracks/soundcloud:tracks:{ref}"


def _stamp(info: dict[str, Any]) -> str | None:
    """When this last changed, as YYYYMMDD — the same string YouTube's state stores."""
    return info.get("modified_date") or info.get("upload_date") or None


def _thumbnail(info: dict[str, Any]) -> str | None:
    """The cover, largest first. SoundCloud keeps an `-original` variant beside the sized ones."""
    if url := info.get("thumbnail"):
        return str(url)
    thumbs = info.get("thumbnails") or []
    return str(thumbs[-1].get("url")) if thumbs and thumbs[-1].get("url") else None


def art_candidates(url: str) -> list[str]:
    """The original artwork is worth trying before a resized one."""
    if m := re.match(r"(https://i\d?\.sndcdn\.com/[\w-]+)-(?:t\d+x\d+|large|badge|crop)\.(jpg|png)", url or ""):
        return [f"{m[1]}-original.{m[2]}", url]
    return [url]
