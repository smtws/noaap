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
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled, DownloadError

from . import captions, sources, ytdlp
from .config import Config
from .models import Candidate, Collection, Entry, SourceRef

log = logging.getLogger(__name__)

NAME = "patreon"
HOST = "patreon.com"
BASE = f"https://www.{HOST}/"
# where a post's files live: its images on Patreon's own content host, its video, audio and captions
# on the video platform Patreon streams through. A page neither of them serves, and neither is read
# as one. Both are checked by **parsed host**, never by substring (§9, slices 73 and 74).
MEDIA_HOST = "patreonusercontent.com"
STREAM_HOST = "mux.com"      # measured: `…edgemv.mux.com` serves the renditions and their `.vtt`
MEDIA_HOSTS = (MEDIA_HOST, STREAM_HOST)
# an address that is a playlist rather than a file, by what it is called — used only to *say* what a
# fetch would cost; what it really is, is decided by the bytes that come back (§9, slice 76)
_MEDIA_PLAYLIST = re.compile(r"\.m3u8(\?|$)", re.I)

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
# …and one more shape, for the audio inside a post's video (§9, slice 72). It is deliberately not a
# media ref: a video post's `id` is the **post's** id, the media API knows nothing about it, and the
# audio has to be copied out of a rendition rather than downloaded as a file. A ref that says which
# of the two it is can be read back; one that guesses cannot.
_VIDEO_REF = re.compile(r"^patreon:video:(\d+)$")

NO_COOKIES = ("Patreon needs your own session: set `patreon_cookies_from_browser` "
              "(for example `firefox`) or `patreon_cookies_file`")
NOT_IN_TIER = "this post is not in your tier"
# **it says what to do, and does not claim to know which of the two it is** (§9, slice 72). Patreon
# answers 403 both when a login has lapsed and when Cloudflare wants its bot check again — and the
# bot-check cookie `__cf_bm` lives **thirty minutes**, so a session that worked an hour ago fails
# with a login that is perfectly valid. Measured: the P59 live run was refused while `session_id` was
# good for another year and `__cf_bm` had expired 45 minutes earlier. One action fixes both.
LAPSED = ("Patreon would not answer for this session — open patreon.com in the browser noaap reads "
          "cookies from (that renews the login and the bot check alike), then try again")
_STALE = re.compile(r"HTTP Error 40[13]|forbidden|unauthorized|log ?in|sign ?in", re.I)
_RATE = re.compile(r"HTTP Error 429|rate.?limit|too many requests", re.I)
_NO_ACCESS = re.compile(r"do not have access", re.I)
_GONE = re.compile(r"HTTP Error 404|not found|removed|deleted", re.I)

# What a post may hold that this provider does not take. A video post is not a silent audio rip
# (R-239, ruling 7), and an embed of a service noaap has no provider for is named, not guessed at.
VIDEO_ONLY = ("this post holds video, not audio — `patreon_audio_from_video = true` copies the audio "
              "out of it, and takes nothing else")
PROTECTED = "this post is protected, and noaap does not go near that"
NO_RENDITION = "this post's video carries no audio stream to copy"
# why a post's captions were **not** taken. Each is said once, on the track's own line, because a
# silent refusal here looks exactly like a post that has no captions at all (§9, slice 74).
NO_CAPTIONS = "this post has no captions"
MACHINE_CAPTIONS = "its captions were generated by the platform, so they are a draft of the audio and not the creator's text"
CAPTIONS_ELSEWHERE = "its captions are served from somewhere this provider does not read"
NOT_WEBVTT = "its captions are neither a WebVTT file nor a playlist of them"
OFFSET_AUDIO = "its audio does not start at zero, so the caption stamps would all be out by that much"
UNMEASURED_AUDIO = "its audio could not be measured, and caption stamps are not trusted unmeasured"
# what a copied stream can be put in **without being re-encoded**. Anything else is refused by name:
# a container that cannot hold this codec would mean transcoding, and transcoding is not copying.
CONTAINERS = {"aac": "m4a", "mp4a": "m4a", "alac": "m4a", "opus": "opus", "vorbis": "ogg",
              "mp3": "mp3", "flac": "flac", "ac-3": "eac3", "ec-3": "eac3"}
CAP = 200  # posts a campaign listing reads at most, before it says so and stops
COPY_PATIENCE = 900  # seconds ffmpeg gets to copy one stream; a long episode is long
# **what a caption playlist may cost, at most** (§9, slice 76). One request per segment and no retry,
# so these are request budgets as much as size budgets: 600 segments is about an hour and a half of a
# narration at the ten-second segments this platform writes, and 8 MB is more text than any of it.
# Over either, the track is refused whole — nothing partial is ever written.
CAPTION_SEGMENTS = 600
CAPTION_BYTES = 8_000_000


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
    return text if (_REF.match(text) or _VIDEO_REF.match(text)) else None


def media_id(ref: str) -> str | None:
    m = _REF.match((ref or "").strip())
    return m[1] if m else None


def ref_for(media: str | int) -> str:
    return f"patreon:media:{media}"


def video_ref(post: str | int) -> str:
    """The ref for *the audio inside* this post's video (§9, slice 72)."""
    return f"patreon:video:{post}"


def video_post(ref: str) -> str | None:
    m = _VIDEO_REF.match((ref or "").strip())
    return m[1] if m else None


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


def _copy_audio(video: Path, out: Path) -> None:
    """Copy the audio stream out of `video` into `out`. **No encoder runs.**

    `-c:a copy` is the whole point: what lands in the library is the creator's own stream, bit for
    bit, in a container that can hold it. If ffmpeg cannot do that, nothing is left behind and the
    failure says so — a re-encode would be a different recording wearing the same name.
    """
    try:
        done = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vn", "-map", "0:a:0",
             "-c:a", "copy", str(out)],
            capture_output=True, text=True, errors="replace", timeout=COPY_PATIENCE)
    except FileNotFoundError:
        raise sources.SourceError("ffmpeg is needed to copy audio out of a video and was not found") from None
    except (OSError, subprocess.SubprocessError) as e:
        out.unlink(missing_ok=True)
        raise sources.SourceError(f"could not copy the audio out of the video: {e}") from None
    if done.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        raise sources.SourceError("could not copy the audio out of the video: "
                                  + (ytdlp.one_sentence(done.stderr) or "ffmpeg refused"))


def _is_media_address(url: str) -> bool:
    """An address of Patreon's media host — a file, not a page, and **the host is parsed, not searched**.

    A substring test said yes to `https://evil.example/x.jpg?patreonusercontent.com` and to
    `https://patreonusercontent.com.evil.example/x.jpg`, and a yes here sends the browser's session
    and a referer to that host (R-255, finding 1). So: https only, the host from the parser (which
    drops any `user@` in front of it), equal to the media host or a sub-domain of it, and no port but
    the ordinary one — a media host on another port is not the media host we know.
    """
    text = (url or "").strip()
    # **two parsers, two answers, so neither shape is allowed** (§9, slice 77). `\` is a path
    # separator to a browser and an ordinary character to this parser, so
    # `https://evil.example\@host/…` is *evil.example* to one and *host* to the other; and userinfo
    # before the host is the older version of the same trick. An address that needs a ruling on
    # whose parser is right is not one we ask anything of.
    if "\\" in text:
        return False
    try:
        parts = urlsplit(text)
    except ValueError:      # a malformed address is not ours either
        return False
    if parts.username is not None or parts.password is not None or "@" in (parts.netloc or ""):
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.port not in (None, 443):
        return False
    return any(host == known or host.endswith("." + known) for known in MEDIA_HOSTS)


def _first_caption(tracks: dict[str, Any]) -> tuple[str | None, str | None]:
    """The first caption track with a WebVTT file, and its language. Order is the answer's own."""
    for language, files in (tracks or {}).items():
        for one in files or []:
            if isinstance(one, dict) and (one.get("ext") or "").lower() == "vtt" and one.get("url"):
                return str(language), str(one["url"])
    return None, None


def _audio_starts_at(path: Path) -> float | None:
    """The first audio stamp of this file, or None if it cannot be read.

    A caption file is timed to the asset; the audio copied out of it is timed to itself. They agree
    only if the audio stream starts at zero, and *checking* costs one process where *assuming* costs
    every line being out by the same amount, invisibly (the spike said so before this was built).
    """
    try:
        done = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                               "-show_entries", "stream=start_time", "-of", "csv=p=0", str(path)],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return float(done.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def _protected(info: dict[str, Any]) -> bool:
    """Any sign of access control on this post — and it is never argued with (R-249, item 3).

    `has_drm` on the post or on any rendition, a password, or a format yt-dlp itself marked. Nothing
    is attempted against any of them: the refusal names the reason and the post is left alone.
    """
    if not isinstance(info, dict):
        return False
    if info.get("has_drm") or info.get("_has_drm") or info.get("video_password") or info.get("password"):
        return True
    return any(f.get("has_drm") or f.get("_has_drm")
               for f in (info.get("formats") or []) if isinstance(f, dict))


def _audio_of(fmt: dict[str, Any]) -> tuple[float, float]:
    """How good this rendition's *audio* is: its bitrate, then its sample rate."""
    return (float(fmt.get("abr") or 0), float(fmt.get("asr") or 0))


def _picture_cost(fmt: dict[str, Any]) -> tuple[float, float, float, str]:
    """How much picture comes with it — the thing we are paying for and throwing away."""
    return (float(fmt.get("vbr") or fmt.get("tbr") or 0), float(fmt.get("height") or 0),
            float(fmt.get("filesize") or fmt.get("filesize_approx") or 0),
            str(fmt.get("format_id") or ""))


def rendition(info: dict[str, Any]) -> dict[str, Any] | None:
    """The one to download: **the best audio, and the smallest picture carrying it** (R-249, item 2).

    Chosen by the audio, never by the picture. Patreon's video posts are one Mux ladder — 270p to
    1080p — whose renditions carry the *same* AAC stream, so the choice costs nothing in audio and
    saves the whole difference in bytes: the live run took the 270p rendition for a sixth of the
    1080p one and the same audio came out. An audio-only format, where one exists, has no picture at
    all and therefore wins on its own terms.
    """
    usable = [f for f in (info.get("formats") or [])
              if isinstance(f, dict) and (f.get("acodec") or "none") not in ("none", None)
              and not (f.get("has_drm") or f.get("_has_drm"))]
    if not usable:
        # a post read as a single format rather than a ladder still carries one
        single = info if (info.get("acodec") or "none") not in ("none", None) else None
        return single
    best = max(_audio_of(f) for f in usable)
    return min([f for f in usable if _audio_of(f) == best], key=_picture_cost)


def container_for(fmt: dict[str, Any]) -> str | None:
    """The suffix a *copy* of this audio stream can live in, or None if copying it means changing it."""
    codec = str(fmt.get("acodec") or "").lower()
    if not codec or codec == "none":
        return None
    for name, suffix in CONTAINERS.items():
        if codec.startswith(name):
            return suffix
    return None


class Patreon:
    """The reads this provider makes, and the only place a Patreon answer is shaped."""

    def __init__(self, cfg: Config, cancel: Any = None,
                 others: Callable[[], list[Any]] | None = None) -> None:
        self.cfg = cfg
        self.cancel = cancel
        self.last_transfer: dict[str, Any] | None = None
        # what the last download found *beside* the audio: the post's own captions, or why not
        self.last_captions: dict[str, Any] | None = None
        # what the last collection read implies captions would cost, for a dry run to say
        self.last_captions_note: str | None = None
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
            raise sources.NoAudio(self._why_nothing(info))
        creator = (info.get("channel") or info.get("uploader") or "").strip() or None
        self.last_captions_note = self._captions_note(info)
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

    def _why_nothing(self, info: dict[str, Any]) -> str:
        """Which of the four reasons this post gave us nothing — named, never a shrug."""
        if info.get("availability") == "needs_auth":
            return NOT_IN_TIER
        if _protected(info):
            return PROTECTED
        if _has_video(info):
            if not self.cfg.patreon_audio_from_video:
                return VIDEO_ONLY
            chosen = rendition(info)
            if chosen is None or container_for(chosen) is None:
                return NO_RENDITION
        return "this post holds nothing noaap can take"

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
            # **off by default, and the refusal names the setting** (R-249, item 1). A creator who
            # posts a narrated story as video has audio in it and nothing else; with the setting on
            # that audio is copied out, and the picture is paid for once and thrown away.
            if not self.cfg.patreon_audio_from_video or _protected(item) or _protected(post):
                return None
            chosen = rendition(item) or rendition(post)
            if chosen is None or (suffix := container_for(chosen)) is None:
                return None
            ref = video_ref(_post_key(item, post))
            if video_post(ref) is None:      # same rule as a media ref: unreadable is not a ref
                log.info("not a Patreon post id, so not ours: %r", _post_key(item, post))
                return None
            copy = Candidate(ref=ref, provider=NAME, added_by="source",
                             why="the audio copied out of this post's video", when=_today(),
                             from_video=True, length=_seconds(item.get("duration")))
            return Entry(
                video_id=ref, position=position, title=_title(item, post), channel=creator,
                duration=_seconds(item.get("duration")), thumbnail=_image(item) or _image(post),
                ext=suffix, copies=[copy],
            )
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
        # **an address we did not build is never handed to the client** (R-255, point 2). This used to
        # fall back to `address` itself, so a plan's `source_url` — a field a person can edit — decided
        # where a request went, with the browser's cookies for whatever host it named.
        where = campaign_of(address) or (post_url(post_id(address)) if post_id(address) else None)
        if not where:
            log.info("not a Patreon post or campaign address, so nothing is asked of it: %r", address)
            return None
        try:
            info = self._read(where, extract_flat=True)
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
        self.last_transfer = None   # what the last download cost, for whoever reports it
        self.last_captions = None
        if (post := video_post(ref)) is not None:
            return self._audio_out_of_video(post, into)
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

    def _audio_out_of_video(self, post: str, into: Path) -> Path:
        """The audio stream of a post's video, **copied** — one rendition in, one audio file out.

        Never re-encoded: ffmpeg copies the stream into a container that can hold it, and a codec no
        container on the list can hold is refused rather than transcoded. The video is downloaded
        only because a container forces it, lives in a scratch directory outside the library, and is
        deleted in `finally` — whether the copy worked, failed or was interrupted (R-249, item 2).
        """
        info = self._read(post_url(post))
        if _protected(info):
            raise sources.NoAudio(PROTECTED)
        chosen = rendition(info)
        suffix = container_for(chosen) if chosen else None
        if chosen is None or suffix is None:
            raise sources.NoAudio(NO_RENDITION)
        into.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix="noaap-patreon-"))
        try:
            options = self._options(outtmpl=str(scratch / "%(id)s.%(ext)s"), skip_download=False,
                                    format=str(chosen.get("format_id") or "best"))
            with YoutubeDL(options) as ydl:
                try:
                    ydl.download([post_url(post)])
                except DownloadCancelled:
                    raise sources.Cancelled("stopped") from None
                except DownloadError as e:
                    raise self._failure(e, post_url(post)) from None
            arrived = [f for f in scratch.iterdir() if f.is_file()]
            if not arrived:
                raise sources.NoAudio(f"nothing arrived for {post_url(post)}")
            video = max(arrived, key=lambda f: f.stat().st_size)
            out = into / f"{post}.{suffix}"
            _copy_audio(video, out)
            # **what this cost is reported, not buried in a log nobody turns on** (§9, slice 73). The
            # first live fetch was asked for exactly these two numbers and could not answer: they were
            # written at INFO and the run was not verbose. A download that throws most of itself away
            # says so where the track line is.
            self.last_transfer = {"downloaded": video.stat().st_size, "kept": out.stat().st_size,
                                  "thrown_away": "video"}
            # **the words that came with it** (§9, slice 74). Taken here because this is the one
            # moment both halves exist: the post's answer, which says whether captions are the
            # creator's or the platform's, and the rendition, whose audio has to start at zero for
            # the stamps to mean anything.
            if self.cfg.patreon_captions:
                self.last_captions = self._captions(info, video)
            log.info("patreon %s: %d byte(s) of video carried %d byte(s) of audio, and the video is gone",
                     post, video.stat().st_size, out.stat().st_size)
            return out
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def _captions_note(self, info: dict[str, Any]) -> str | None:
        """What taking this post's captions would cost, said **before** anything is fetched.

        A dry run makes no request for them, so this is the shape and not a count: a playlist costs
        one request for itself and one for each segment, and the segment count is in the playlist
        (§9, slice 76, R-262 item 7). After a real fetch the track's own line says the exact number.
        """
        if not self.cfg.patreon_captions:
            return None
        language, url = _first_caption(info.get("subtitles") or {})
        if not url:
            return "captions: none in this post" if not info.get("automatic_captions") else \
                "captions: only the platform's own, which are not taken"
        if _MEDIA_PLAYLIST.search(url):
            return (f"captions: a playlist ({language}) — taking them costs one request for it and "
                    f"one for each segment, at most {CAPTION_SEGMENTS}")
        return f"captions: one file ({language}), one request"

    def _captions(self, info: dict[str, Any], rendition: Path) -> dict[str, Any]:
        """The post's own captions, or the reason there are none — never a silent nothing.

        Every refusal is a sentence somebody can act on, because "no words appeared" reads the same
        whether the post has no captions, the platform wrote them, or this program refused them.
        """
        said = {"by": NAME}
        if info.get("automatic_captions") and not info.get("subtitles"):
            return {**said, "refused": MACHINE_CAPTIONS}
        tracks = info.get("subtitles") or {}
        language, url = _first_caption(tracks)
        if not url:
            return {**said, "refused": NO_CAPTIONS}
        if not _is_media_address(url):
            return {**said, "refused": CAPTIONS_ELSEWHERE}
        # a constant offset on every line is worse than no words: it looks right and is not
        offset = _audio_starts_at(rendition)
        if offset is None:
            return {**said, "refused": UNMEASURED_AUDIO}
        if abs(offset) > 0.001:
            return {**said, "refused": f"{OFFSET_AUDIO} ({offset:+.3f} s)"}
        try:
            raw = self._media_bytes(url, accept="text/vtt,application/vnd.apple.mpegurl,*/*")
        except sources.SourceError as e:
            return {**said, "refused": f"its captions could not be read: {e}"}
        if captions.is_webvtt(raw):
            return {**said, "bytes": len(raw), "file": raw, "language": language,
                    "map": captions.NO_MAP if not captions.timestamp_base(
                        raw.decode("utf-8-sig", "replace"))[1] else captions.SAME_MAP,
                    "segments": 1, "requests": 1}
        if not captions.is_playlist(raw):
            return {**said, "refused": NOT_WEBVTT}
        found, refused, asked = self._caption_segments(url, raw)
        if refused:
            return {**said, **found, "refused": refused, "requests": asked}
        return {**said, **found, "language": language, "requests": asked}

    def _caption_segments(self, where: str, raw: bytes) -> tuple[dict[str, Any], str | None, int]:
        """Fetch a caption playlist's segments in order and join them (§9, slice 76).

        **Every address is checked before it is asked**, the relative ones after they are resolved
        against the playlist they came from: one segment on a host that is not ours refuses the whole
        track, because a caption file assembled from two places is not this post's captions. Nothing
        partial is ever returned — a refusal comes back with no cues at all.
        """
        segments, problem = captions.playlist(raw)
        asked = 1                      # the playlist itself
        if problem:
            return {}, problem, asked
        if not segments:
            return {}, "its caption playlist lists no segments", asked
        if len(segments) > CAPTION_SEGMENTS:
            return {}, (f"its caption playlist lists {len(segments)} segments, more than the "
                        f"{CAPTION_SEGMENTS} this program will fetch"), asked
        # **every address is resolved and checked before any of them is asked** (R-263, defect 2). It
        # was one at a time, so a foreign third segment was found only after two requests had already
        # gone out. The list is judged as a list, and then it is fetched.
        addresses = [urljoin(where, part) for part in segments]
        if not all(_is_media_address(one) for one in addresses):
            return {}, CAPTIONS_ELSEWHERE, asked
        if len(set(addresses)) != len(addresses):
            return {}, "its caption playlist lists the same segment twice", asked
        texts, total = [], 0
        for position, address in enumerate(addresses, 1):
            asked += 1      # counted before the attempt: a request that failed was still made
            try:
                body = self._media_bytes(address, accept="text/vtt,*/*")
            except sources.SourceError as e:
                return {}, f"its caption segment {position} could not be read: {e}", asked
            total += len(body)
            if total > CAPTION_BYTES:
                return {}, (f"its caption segments are larger than the "
                            f"{CAPTION_BYTES // 1_000_000} MB this program will read"), asked
            texts.append(body.decode("utf-8-sig", "replace"))
        cues, problem, convention = captions.read_segments(texts)
        if problem:
            return {"bytes": total, "map": convention}, problem, asked
        if not cues:
            return {"bytes": total, "map": convention}, "its caption segments hold no lines", asked
        # **what it cost and what it met**, recorded rather than inferred from the shape of the
        # result afterwards (§9, slice 76, R-266). Neither ever reaches a plan: they describe the
        # transfer, not the words.
        return {"cues": cues, "bytes": total, "map": convention, "segments": len(addresses)}, None, asked

    def probe(self, ref: str) -> Entry:
        """One media on its own, for a track whose post is not being read as a whole."""
        if (post := video_post(ref)) is not None:
            info = self._read(post_url(post))
            entry = self._entry(info, 1, info)
            if entry is None:
                raise sources.NoAudio(self._why_nothing(info))
            return entry
        media = media_id(ref)
        if not media:
            raise sources.NotSupported(f"not a Patreon media ref: {ref}")
        info = self._read(f"https://www.patreon.com/api/media/{media}")
        entry = self._entry(info, 1, info)
        if entry is None:
            raise sources.NoAudio(NOT_IN_TIER)
        return entry

    def fetch_bytes(self, address: str) -> bytes:
        """The post's image, the campaign's, or **an image address already in hand**.

        The third case is why this changed (§9, slice 73). A cover address that a post read gave us
        minutes ago was handed back here and re-read *as though it were a post*: not a post address,
        not a campaign address, so it went to yt-dlp's extractor as a page — which is not what a JPEG
        is. The live run reported `could not fetch any cover` for an address whose signature was good
        for another two weeks. An address of the media host is now simply fetched, with the session
        and the referer a browser would send.
        """
        if _is_media_address(address):
            return self._image_bytes(address)
        # the same rule as everywhere else in this file: an address we did not build is not asked
        # (R-255, point 2). This had the raw address as its last fallback, which is how a field a
        # person can edit could have decided where a request went.
        where = (post_url(post_id(address)) if post_id(address) else None) or campaign_of(address)
        if not where:
            raise sources.SourceError(f"not a Patreon address, so no cover is asked of it: {address}")
        info = self._read(where, extract_flat=True)
        url = _image(info)
        if not url:
            raise sources.SourceError(f"no cover for {address}")
        if not _is_media_address(url):
            # what the answer points at is checked too: the image of a post lives on the media host
            raise sources.SourceError(f"this post's image is not on the media host: {urlsplit(url).hostname}")
        return self._image_bytes(url)

    def _image_bytes(self, url: str) -> bytes:
        return self._media_bytes(url)

    def _media_bytes(self, url: str, accept: str = "image/*,*/*") -> bytes:
        """One GET, with this provider's session and the referer its host expects.

        **Where it landed is checked too.** The client follows redirects, so an address that starts on
        the media host can end anywhere; bytes from anywhere else are not this post's image and are
        refused rather than saved (R-255, finding 1). What a redirect target receives on the way is
        its own domain's cookies from the browser, which no check here can take back — which is why
        the only addresses handed to this method are ones we built or ones that passed the host test.
        """
        from yt_dlp.networking import Request

        with YoutubeDL(self._options()) as ydl:
            try:
                answer = ydl.urlopen(Request(url, headers={"Referer": BASE, "Accept": "image/*,*/*"}))
                data = answer.read()
            except Exception as e:  # a missing cover must never stop an album
                raise sources.SourceError(f"could not read the cover: {ytdlp.one_sentence(str(e))}") from None
        landed = getattr(answer, "url", None) or url
        if not _is_media_address(landed):
            raise sources.SourceError(f"the cover address led somewhere else ({urlsplit(landed).hostname})")
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


def _post_key(item: dict[str, Any], post: dict[str, Any]) -> str:
    """This post's own id — from its address first, because that is where it is certainly the post's.

    A video post's `id` **is** the post id (measured on five live posts), but a post read as a
    playlist of attachments gives each file an id of its own, so the address is asked first.
    """
    for one in (item, post):
        if isinstance(one, dict) and (found := post_id(str(one.get("webpage_url") or ""))):
            return found
    return str(post.get("id") or item.get("id") or "")


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
