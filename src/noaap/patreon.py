"""All Patreon I/O, through yt-dlp (DESIGN §9, slice 70).

What this provider is **for**: music a patron already pays for and can only reach while logged in —
a creator's own releases, stems, alternate takes. It is not a source of better copies of anything
public, and it is not a way around anything.

**It cannot work without the patron's own session, and that is measured, not assumed.** yt-dlp's
extractor uses Patreon's mobile user agent only when a `session_id` cookie exists for patreon.com;
without one it asks for TLS impersonation instead, which this installation does not have and will not
get (R-239, ruling 1). Asked anonymously for the *public* post in yt-dlp's own test list, the answer
was `HTTP Error 403: Forbidden`. So: cookies, or a refusal that says so.

**What noaap stores is the setting and nothing else.** Not the cookie, not a copy of it, not a token.
The patron's browser profile or cookie file is read by yt-dlp and never written, never logged, never
copied into a library.

Everything returned is mapped into `models` here, so no other module sees a raw yt-dlp dict and
nothing outside this file and its adapter knows what a Patreon address looks like.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled, DownloadError

from . import sources, ytdlp
from .config import Config
from .models import Candidate, Collection, Entry, SourceRef

log = logging.getLogger(__name__)

NAME = "patreon"
HOST = "patreon.com"
BASE = f"https://www.{HOST}/"

# `patreon.com/posts/<id>` (with or without a slug before it) is a post — the unit this provider
# treats as a collection. A campaign is an owner: `/<vanity>`, `/c/<vanity>`, `/cw/<vanity>`,
# `/m/<id>`, each optionally followed by `/posts`. Both shapes are yt-dlp's, narrowed to what we use.
_POST = re.compile(r"^https?://(?:www\.)?patreon\.com/(?:[^/?#]+/)?posts/(?:[\w-]+-)?(\d+)", re.I)
_OLD_POST = re.compile(r"^https?://(?:www\.)?patreon\.com/creation\?hid=(\d+)", re.I)
_CAMPAIGN = re.compile(
    r"^https?://(?:www\.)?patreon\.com/(?:(?:m|api/campaigns)/(\d+)|(?:cw?/)?"
    r"(?!creation[?/]|posts/|rss[?/])([\w-]+))(?:/posts)?/?(?:$|[?#])", re.I)

# A media id is the ref: a post can hold several files, and a post id cannot name one of them. The
# ref is opaque to everyone but this module (§9, slice 51) — it is `patreon:media:<id>` so that a
# glance at a plan says which provider minted it and nothing has to be guessed from its shape.
_REF = re.compile(r"^patreon:media:(\d+)$")

NO_COOKIES = ("Patreon needs your own session: set `patreon_cookies_from_browser` "
              "(for example `firefox`) or `patreon_cookies_file`")
NOT_IN_TIER = "this post is not in your tier"
LAPSED = ("its session has gone stale — open patreon.com in the browser noaap reads cookies from, "
          "then try again")
_STALE = re.compile(r"HTTP Error 40[13]|forbidden|unauthorized|log ?in|sign ?in", re.I)
_RATE = re.compile(r"HTTP Error 429|rate.?limit|too many requests", re.I)
_NO_ACCESS = re.compile(r"do not have access", re.I)
_GONE = re.compile(r"HTTP Error 404|not found|removed|deleted", re.I)

# What a post may hold that this provider does not take. A video post is not a silent audio rip
# (R-239, ruling 7), and an embed of a service noaap has no provider for is named, not guessed at.
VIDEO_ONLY = "this post holds video, not audio"
CAP = 200  # posts a campaign listing reads at most, before it says so and stops


def is_address(text: str) -> bool:
    """Cheap: is this one of ours? No request, and no guessing from a bare word."""
    return HOST in (text or "")


def post_url(post_id: str) -> str:
    return f"{BASE}posts/{post_id}"


def one_ref(text: str) -> str | None:
    """A single media's ref out of whatever the user typed, else None.

    A post address is **not** a track: a post can hold three files. So a bare post address answers
    `None` here and is a collection instead.
    """
    text = (text or "").strip()
    return text if _REF.match(text) else None


def media_id(ref: str) -> str | None:
    m = _REF.match((ref or "").strip())
    return m[1] if m else None


def ref_for(media: str | int) -> str:
    return f"patreon:media:{media}"


def post_id(address: str) -> str | None:
    text = (address or "").strip()
    for pattern in (_POST, _OLD_POST):
        if m := pattern.match(text):
            return m[1]
    return None


def campaign_of(address: str) -> str | None:
    """The campaign address a listing would read, if that is what this is."""
    text = (address or "").strip()
    if post_id(text):
        return None
    if m := _CAMPAIGN.match(text):
        return f"{BASE}{'m/' + m[1] if m[1] else 'c/' + m[2]}/posts"
    return None


class Patreon:
    """The reads this provider makes, and the only place a Patreon answer is shaped."""

    def __init__(self, cfg: Config, cancel: Any = None,
                 others: Callable[[], list[Any]] | None = None) -> None:
        self.cfg = cfg
        self.cancel = cancel
        # **who else there is, from the caller** (R-241, ruling 1). A post is often a YouTube or
        # SoundCloud link with a note, and saying whose that audio is takes asking them — but a
        # provider does not build a configuration, its own or anybody else's, to find out. The adapter
        # passes this in; without it an embed is simply not recognised, which is the safe answer.
        self._others = others or (lambda: [])

    # -- the one connection to the internet -------------------------------------------

    def _options(self, **extra: Any) -> dict[str, Any]:
        """yt-dlp's options for us: the shared ones, and **this provider's own cookies**.

        Nothing of another provider's cookie handling is reused: SoundCloud's settings are
        SoundCloud's, and a patron who has set neither of ours gets a refusal rather than somebody
        else's session (R-238 c).
        """
        if not self.has_session():
            raise sources.NotSupported(NO_COOKIES)
        return ytdlp.params(cookies_file=self.cfg.patreon_cookies_file,
                            cookies_from_browser=self.cfg.patreon_cookies_from_browser, **extra)

    def has_session(self) -> bool:
        return bool(self.cfg.patreon_cookies_file or self.cfg.patreon_cookies_from_browser)

    def _read(self, url: str, **extra: Any) -> dict[str, Any]:
        with YoutubeDL(self._options(**extra)) as ydl:
            try:
                got = ydl.extract_info(url, download=False)
            except DownloadCancelled:
                raise sources.Cancelled("stopped") from None
            except DownloadError as e:
                raise self._failure(e, url) from None
        if not isinstance(got, dict):
            raise sources.SourceError(f"Patreon said nothing about {url}")
        return got

    def _failure(self, e: Exception, what: str) -> sources.SourceError:
        """yt-dlp's message onto the four failures there are (R-239, ruling 2).

        A lapsed session and a rate limit stop *everything*, so they are `Blocked`. A post the tier
        does not include exists and has no stream for this listener, which is `NoAudio` — the same
        answer a geo-blocked track gets, and the album goes on without it.
        """
        msg = ytdlp.trim(e)
        if _RATE.search(msg):
            raise sources.Blocked("Patreon is refusing requests for now — wait and try again")
        if _NO_ACCESS.search(msg):
            raise sources.NoAudio(NOT_IN_TIER)
        if _STALE.search(msg):
            raise sources.Blocked(LAPSED)
        if _GONE.search(msg):
            raise sources.NoAudio(f"this post is no longer on Patreon: {ytdlp.one_sentence(msg)}")
        return sources.SourceError(ytdlp.one_sentence(msg) or f"could not read {what}")

    # -- a post, which is a collection -------------------------------------------------

    def fetch(self, address: str) -> Collection:
        """One post's media, in the order the post lists them (R-239, ruling 3).

        A post is the collection because nothing on Patreon is an album: a creator posts *things*, and
        one post holding three files is three tracks — the same shape a folder has. No number is
        invented for them; the order is the post's own.
        """
        post = post_id(address)
        if not post:
            raise sources.SourceError(f"not a Patreon post: {address}")
        info = self._read(post_url(post))
        return self._collection(info, post)

    def _collection(self, info: dict[str, Any], post: str) -> Collection:
        items = info.get("entries") if info.get("_type") == "playlist" else [info]
        entries: list[Entry] = []
        for position, item in enumerate(list(items or []), 1):
            if (entry := self._entry(item, position, info)) is not None:
                entries.append(entry)
        if not entries:
            raise sources.NoAudio(NOT_IN_TIER if info.get("availability") == "needs_auth"
                                  else VIDEO_ONLY if _has_video(info) else
                                  "this post holds nothing noaap can take")
        creator = (info.get("channel") or info.get("uploader") or "").strip() or None
        return Collection(
            source_url=post_url(post), source_id=post_url(post),
            # a post is never a release: it is a thing a creator posted, which is what a playlist is
            # to the classifier, and `is_release` says so for good (R-239, ruling 3)
            is_playlist=True,
            title=(info.get("title") or "").strip(), channel=creator,
            thumbnail=_image(info), fetched_at=_now(), entries=entries,
            # the post's own date, which is real — and is not a release year
            modified=_date(info.get("timestamp")),
        )

    def _entry(self, item: dict[str, Any], position: int, post: dict[str, Any]) -> Entry | None:
        """One media of a post, or `None` for what this provider does not take.

        **An embed of a service noaap has a provider for belongs to that provider** (R-239, ruling 4):
        the entry carries its own `provider` and `ref`, and this one does not pretend the audio is
        Patreon's. An embed of anything else is named and skipped.
        """
        if not isinstance(item, dict):
            return None
        creator = (post.get("channel") or post.get("uploader") or None)
        if (other := _other_provider(item, self._others())) is not None:
            name, ref = other
            # **the copy carries whose it is** — that is what a candidate is for, and the core reads
            # the provider off it when it comes to fetching (§9, slices 50 and 51)
            copy = Candidate(ref=ref, provider=name, added_by="source",
                             why=f"embedded in a Patreon post by {creator or 'its creator'}",
                             when=_today())
            return Entry(video_id=ref, position=position, title=_title(item, post), channel=creator,
                         duration=_seconds(item.get("duration")), copies=[copy])
        if _is_video(item):
            return None
        if (media := item.get("id")) is None:
            return None
        # **a ref this provider could not read back is not a ref** (found by handing the providers in:
        # an embed nobody claimed fell through to here and became `patreon:media:dQw4w9WgXcQ`, which
        # `media_id` refuses and `audio` could never fetch). A media id is digits; anything else is
        # not ours, and saying so here beats writing a plan that names nothing.
        ref = ref_for(str(media).rsplit("-", 1)[-1])
        if media_id(ref) is None:
            log.info("not a Patreon media id, so not ours: %r", media)
            return None
        copy = Candidate(ref=ref, provider=NAME, added_by="source", why="a file of this post",
                         when=_today(), bytes=_int(item.get("filesize")),
                         length=_seconds(item.get("duration")))
        return Entry(
            video_id=ref, position=position, title=_title(item, post), channel=creator,
            duration=_seconds(item.get("duration")), thumbnail=_image(item) or _image(post),
            ext=(item.get("ext") or None), copies=[copy],
        )

    # -- what a campaign publishes, and whether it changed -----------------------------

    def list_owner(self, address: str) -> list[SourceRef]:
        """The campaign's posts, newest first — filtered to that campaign, and capped.

        Every ref is a post, because a post is what this provider reads as a collection.
        """
        feed = campaign_of(address)
        if not feed:
            raise sources.SourceError(f"not a Patreon campaign: {address}")
        cap = self.cfg.patreon_post_cap or CAP
        # **the cap is asked for, not applied afterwards.** yt-dlp pages the feed itself, so
        # `playlistend` is what stops it — reading two thousand posts and then throwing away 1800 is
        # not restraint, it is rudeness with a filter on top.
        page = self._read(feed, extract_flat=True, playlistend=cap)
        taking = _Listing(_campaign_id(page), cap)
        taking.take(page)
        if taking.foreign:
            # said out loud, never silently: it means Patreon handed us somebody else's posts
            log.info("%s: %d post(s) of another campaign were left out", feed, taking.foreign)
        if taking.capped:
            log.info("%s: stopped at %d posts", feed, taking.cap)
        return taking.refs

    def source_state(self, address: str) -> dict[str, Any] | None:
        """What `update` compares, in one request: the post ids now, and the newest date.

        For a *post* address that is the media it holds; for a campaign, the posts on its first page.
        A campaign with two thousand posts is not read to answer "has anything changed".
        """
        try:
            info = self._read(campaign_of(address) or address, extract_flat=True)
        except sources.SourceError:
            return None
        items = info.get("entries") if info.get("_type") == "playlist" else [info]
        ids = [str(one.get("id") or "") for one in (items or []) if isinstance(one, dict)]
        return {"ids": [i for i in ids if i], "modified": _date(info.get("timestamp"))
                or max((_date(one.get("timestamp")) or "" for one in (items or [])
                        if isinstance(one, dict)), default="") or None}

    # -- the audio, the art, and one media on its own -----------------------------------

    def download_audio(self, ref: str, into: Path, choice: str = "best") -> Path:
        """The file behind one media ref. One request, and no ladder of formats to choose from.

        Patreon offers whatever the creator uploaded and nothing else, so `choice` has nothing to
        decide here — it stays in the signature because every provider answers the same call.
        """
        media = media_id(ref)
        if not media:
            raise sources.NotSupported(f"not a Patreon media ref: {ref}")
        into.mkdir(parents=True, exist_ok=True)
        before = set(into.iterdir())
        options = self._options(outtmpl=str(into / f"{media}.%(ext)s"), skip_download=False)
        with YoutubeDL(options) as ydl:
            try:
                ydl.download([f"https://www.patreon.com/api/media/{media}"])
            except DownloadCancelled:
                raise sources.Cancelled("stopped") from None
            except DownloadError as e:
                raise self._failure(e, ref) from None
        fresh = [p for p in into.iterdir() if p not in before and p.is_file()]
        if not fresh:
            raise sources.NoAudio(f"nothing arrived for {ref}")
        return max(fresh, key=lambda p: p.stat().st_size)

    def probe(self, ref: str) -> Entry:
        """One media on its own, for a track whose post is not being read as a whole."""
        media = media_id(ref)
        if not media:
            raise sources.NotSupported(f"not a Patreon media ref: {ref}")
        info = self._read(f"https://www.patreon.com/api/media/{media}")
        entry = self._entry(info, 1, info)
        if entry is None:
            raise sources.NoAudio(NOT_IN_TIER)
        return entry

    def fetch_bytes(self, address: str) -> bytes:
        """The post's image, or the campaign's — whichever this address is."""
        info = self._read(address if post_id(address) else (campaign_of(address) or address),
                          extract_flat=True)
        url = _image(info)
        if not url:
            raise sources.SourceError(f"no cover for {address}")
        with YoutubeDL(self._options()) as ydl:
            try:
                data = ydl.urlopen(url).read()
            except Exception as e:  # a missing cover must never stop an album
                raise sources.SourceError(f"could not read the cover: {ytdlp.one_sentence(str(e))}") from None
        return data


# -- reading yt-dlp's answers, and nothing else -------------------------------------------


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _seconds(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out or None   # a zero length is not a length (§9, slice 53)


def _image(info: dict[str, Any]) -> str | None:
    """A post's image, or the campaign's thumbnail — in that order (R-238 e)."""
    if not isinstance(info, dict):
        return None
    for key in ("thumbnail",):
        if url := info.get(key):
            return str(url)
    for one in (info.get("thumbnails") or []):
        if isinstance(one, dict) and one.get("url"):
            return str(one["url"])
    return None


def _title(item: dict[str, Any], post: dict[str, Any]) -> str:
    """A media's own name where it has one, else the post's title.

    A post with three attachments names each of them (`alt_title` is the file's name); a post with one
    file is titled by the post. Neither is a track number and nothing here invents one.
    """
    for key in ("alt_title", "title"):
        if (text := (item.get(key) or "").strip()):
            return text
    return (post.get("title") or "").strip()


def _is_video(item: dict[str, Any]) -> bool:
    """A video post is not a silent audio rip (R-239, ruling 7)."""
    if (item.get("vcodec") or "none") not in ("none", None):
        return True
    return (item.get("ext") or "") in ("mp4", "mkv", "webm", "mov", "m4v")


def _has_video(info: dict[str, Any]) -> bool:
    items = info.get("entries") if info.get("_type") == "playlist" else [info]
    return any(_is_video(one) for one in (items or []) if isinstance(one, dict))


def _other_provider(item: dict[str, Any], others: list[Any]) -> tuple[str, str] | None:
    """`(provider, ref)` when this entry is an embed one of `others` handles (R-239, ruling 4).

    A Patreon post often *is* a YouTube or SoundCloud link with a note. The audio is then that
    service's, and saying so is both honest and what makes the track fetchable at all — the alternative
    is a ref only Patreon could resolve and Patreon does not host.

    **The providers are handed in.** Nothing here builds one, and nothing here builds a configuration to
    build one with: asking who owns an address is a question about addresses, and a provider that
    constructs its neighbours has taken a decision that belongs to whoever assembled them (R-241).
    """
    url = str(item.get("url") or item.get("webpage_url") or "")
    if not url:
        return None
    for other in others:
        name = getattr(other, "name", None)
        if not name or name == NAME:
            continue
        try:
            if other.handles(url):
                return name, (other.one_ref(url) if hasattr(other, "one_ref") else None) or url
        except Exception:      # `handles` is cheap and must not raise; if it does, it is not ours
            continue
    return None


def _date(stamp: Any) -> str | None:
    """A post's date as the rest of the program writes one: YYYYMMDD."""
    from datetime import UTC, datetime

    if (seconds := _int(stamp)) is None:
        return None
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y%m%d")


def _today() -> str:
    from datetime import date

    return date.today().isoformat()


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


# -- what a campaign publishes, and whether it has changed (§9, slice 70) --------------------------
#
# Patreon's own listing is a cursor-paged feed of posts, newest first. Two things are non-negotiable
# here, and both are about restraint rather than correctness:
#
# **Every post is checked against the campaign that was asked for.** yt-dlp #10013 reported asking for
# one campaign's posts and getting *every membership the account had*. The current extractor filters
# on `filter[campaign_id]` and looks right — and this checks anyway, because the cost of checking is
# one comparison and the cost of trusting it is somebody's whole membership on their disk.
#
# **And it stops.** `CAP` posts, then it says so. A creator with two thousand posts is not a library
# and nobody asked for a backup of them.


def _campaign_id(info: dict[str, Any]) -> str | None:
    for key in ("channel_id", "id", "uploader_id"):
        if (value := info.get(key)) not in (None, ""):
            return str(value)
    return None


def _slug_title(url: str) -> str | None:
    """The words in a post address, when the listing gives no title.

    Measured, not assumed: a flat read of a campaign returns `{"url": …, "ie_key": "Patreon"}` and
    nothing else — no title, no id, no date — so the picker printed five bare URLs and a patron had
    nothing to choose by (§9, slice 71). The slug is the creator's own words in their own address;
    it is shown verbatim, hyphens and all, because it is a fragment of a title and not the title.
    The real one arrives with the post itself.
    """
    tail = (url or "").rstrip("/").rsplit("/", 1)[-1].split("?")[0]
    words = re.sub(r"-?\d+$", "", tail)
    return words or None


class _Listing:
    """The part of `listing` that is arithmetic: which posts belong, and when to stop."""

    def __init__(self, wanted: str | None, cap: int = CAP) -> None:
        self.wanted = wanted
        self.cap = cap
        self.refs: list[SourceRef] = []
        self.foreign = 0
        self.capped = False
        self._seen: set[str] = set()

    def take(self, page: dict[str, Any]) -> bool:
        """Add this page's posts; answer whether another page is still wanted."""
        owner = _campaign_id(page)
        for item in page.get("entries") or []:
            if not isinstance(item, dict):
                continue
            mine = _campaign_id(item) or owner
            if self.wanted and mine and mine != self.wanted:
                # **not this campaign's** — the hazard of yt-dlp #10013, dropped and counted
                self.foreign += 1
                continue
            url = str(item.get("url") or "")
            post = post_id(url) or str(item.get("id") or "")
            if not post or post in self._seen:
                continue
            self._seen.add(post)
            self.refs.append(SourceRef(
                url=post_url(post), source_id=post_url(post),
                title=(item.get("title") or _slug_title(url) or post_url(post)),
                # a post is not a release and never a playlist somebody arranged: it is a post
                tab="posts",
                artist=(item.get("channel") or page.get("channel") or page.get("uploader") or None),
                channel_url=(page.get("channel_url") or None),
                count=None, thumbnail=_image(item) or None))
            if len(self.refs) >= self.cap:
                self.capped = True
                return False
        return True
