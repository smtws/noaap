"""`noaap serve`: a small web UI (installable as a PWA) on top of service.py.

Stdlib only. One worker thread runs jobs one after another (gentle on YouTube, and no two
jobs ever touch the library at once); the browser polls /api/state.

Safety: listens on 127.0.0.1 by default. Writes need the `X-Noaap` header (so other
websites cannot trigger them through the browser: that header forces a CORS preflight we
never answer) and the Host header must be ours (DNS rebinding). Files are only ever served
by album id, never by a path from the request.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import datetime as dt
import email.utils
import hashlib
import itertools
import json
import logging
import os
import queue
import socket
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from . import config as config_mod
from . import running_from, sources, user_agent
from .config import Config
from .cover import THUMB_SIDE
from .cover import thumbnail as make_thumbnail
from .download import COVER_STEM, PLAN_FILE, _download_cover, iter_plans, load_plan, read_plan
from .lyrics import needs_you, publishable, read_sidecar, reconcile, timings_stale
from .mb import WEB as MB_WEB
from .mb import seed_release, seed_url, seedable
from .models import AlbumPlan, PlanTrack
from .plan import album_length_flag
from .service import Outcome, Service, _inside, collection_address, may_send_audio, refuse_folder
from .sources import Cancelled
from .tag import image_mime
from .text import natural_key
from .timing import (
    ALIGN,
    LISTEN,
    OFFERS,
    PRICES,
    PROVIDERS,
    TRANSCRIBE,
    VENDORS,
    Engines,
    TimingUnavailable,
    capabilities_of,
    kind_for,
    release_when_idle,
    verifies_with,
)
from .treatment import EXCEPTIONS
from .treatment import OPERATIONS as TREATMENT_KEYS
from .trim import original_path

log = logging.getLogger(__name__)

#: how long the queues stay quiet before the graphics card goes back — the config's default, so the
#: number lives in one place (§9, slice 82)
CARD_IDLE = Config.timing_card_idle_seconds

# A write must say it came from our own page rather than from a form on someone else's
# (§9, slice 24). **One spelling** (R-420): the old one was accepted while an installed PWA might
# still be serving the old page from its own cache, and no such page is left.
WRITE_HEADER = "X-Noaap"

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/logic.mjs": ("logic.mjs", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}
MAX_LOG = 400
# **a finished job is kept for the page, not for ever** (P116, I-407): the night run measured a
# server that never forgot one growing 165 → 339 MB over ~4,100 jobs. The page reads the last
# twenty (`recent`) and polls the one it started, so a few hundred is plenty.
KEEP_FINISHED = 200
# what a saved alignment may record about the two methods that produced it (§9, slice 44)
CHECKED_KEYS = frozenset({"first_span", "second_span", "first_piled", "second_piled", "first_placed",
                          "second_placed", "lost_method", "kept_method", "lost_why", "verified_against"})
MAX_BODY = 1 << 20
# **one path may send more** (§9, slice 156): a cover the user picks is a picture of their own, and
# a megabyte is small for one. Base64 in JSON keeps the write header and the JSON content type
# doing their job, and costs a third on top, so the room is for the picture and that third.
MAX_COVER_BODY = 20 << 20
# thumbnails are fetched by us, so the page never talks to Google and the CSP stays strict
THUMB_HOSTS = ("ytimg.com", "ggpht.com", "googleusercontent.com", "coverartarchive.org", "archive.org")
THUMB_CACHE = 300


# -- jobs ------------------------------------------------------------------------------------


@dataclass
class Job:
    id: int
    kind: str
    label: str
    lane: str = "write"  # "write" changes the library and runs alone; "read" runs beside it
    target: str | None = None  # the album (source_id) this job holds, where it knows it
    state: str = "queued"  # queued | running | done | failed | blocked | cancelled
    log: list[str] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    result: Any = None
    created: float = field(default_factory=time.time)
    finished: float | None = None

    def summary(self, full: bool = False) -> dict[str, Any]:
        d = {"id": self.id, "kind": self.kind, "label": self.label, "state": self.state, "lane": self.lane,
             "created": self.created, "finished": self.finished}
        d["log"] = self.log if full else self.log[-3:]
        if full:
            d["result"] = self.result
        return d


class Jobs:
    # jobs that change the library run one at a time; reading jobs (search, preview,
    # channel listing) get their own lane so a search never waits for a download
    # they only read: the answer goes to the page. `repair_check` is the dry run of slice 85 — it
    # writes nothing, so it belongs here and may run beside a download (§9, slice 91).
    READ_ONLY = ("search", "preview", "channel", "align", "draft", "repair_check", "take_in_check",
                 "identify")   # it writes nothing; `identify_apply` is the write (§9, slice 142)

    def __init__(self, make_service: Callable[[Job], Service], release: Callable[[], Any] | None = None,
                 wrote: Callable[[], Any] | None = None,
                 idle_seconds: float = CARD_IDLE, sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], float] = time.monotonic) -> None:
        self.make_service = make_service
        self._jobs: dict[int, Job] = {}
        self._ids = itertools.count(1)
        self._queues: dict[str, queue.Queue[tuple[Job, Callable[[Service], Any]]]] = {"write": queue.Queue(), "read": queue.Queue()}
        self._lock = threading.Lock()
        # the graphics card goes back when the queues have been quiet this long (§9, slice 82). A job
        # holds `self.idle` while it runs, and `busy` covers the one that is queued but has not
        # started, so nothing is ever taken out from under work that is coming.
        # **somebody wrote** (§9, slice 139): the model the page is answered from is stale the moment
        # one of our own passes finishes, and waiting a minute to notice our own work is absurd.
        # **what the write touched, where the job knows it** (§9, slice 152): a job for one album
        # patches that album into what is held; one for the whole library asks for a walk.
        self.wrote = wrote or (lambda target: None)
        self.idle = release_when_idle(idle_seconds if release else 0.0, release or (lambda: None),
                                     busy=self.busy, sleep=sleep, now=now)
        for lane in self._queues:
            threading.Thread(target=self._work, args=(lane,), name=f"noaap-jobs-{lane}", daemon=True).start()

    def submit(self, kind: str, label: str, action: Callable[[Service], Any], target: str | None = None) -> Job:
        job = Job(next(self._ids), kind, label, lane="read" if kind in self.READ_ONLY else "write", target=target)
        with self._lock:
            self._jobs[job.id] = job
            done = [i for i, j in self._jobs.items() if j.state not in ("queued", "running")]
            for i in done[:max(0, len(done) - KEEP_FINISHED)]:  # oldest first: ids only grow
                del self._jobs[i]
        self._queues[job.lane].put((job, action))
        return job

    def _all(self) -> list[Job]:
        """A copy to look through: `submit` forgets old jobs from another thread."""
        with self._lock:
            return list(self._jobs.values())

    def get(self, job_id: int) -> Job | None:
        return self._jobs.get(job_id)

    def recent(self, n: int = 20) -> list[Job]:
        return sorted(self._all(), key=lambda j: j.id, reverse=True)[:n]

    def busy(self, lane: str | None = None) -> bool:
        """Something is queued or running (by default in any lane)."""
        return any(j.state in ("queued", "running") and lane in (None, j.lane) for j in self._all())

    def writing(self) -> Job | None:
        """The queued or running job that changes the library, if there is one."""
        return next((j for j in self._all() if j.lane == "write" and j.state in ("queued", "running")), None)

    def working_on(self, target: str) -> Job | None:
        """The queued or running write job that has this album in its hands, if there is one.

        Only jobs that know their album can be found this way: a `fetch` is named by its URL and
        learns the album id while it runs, so it is not one of them.
        """
        return next((j for j in self._all() if j.target == target and j.state in ("queued", "running")), None)

    def cancel(self, job_id: int) -> Job | None:
        """Queued: will never run. Running: stops at the next safe point."""
        job = self._jobs.get(job_id)
        if job and job.state in ("queued", "running"):
            job.cancel.set()
            job.log.append("cancel requested…" if job.state == "running" else "cancelled before it started")
            if job.state == "queued":
                job.state, job.finished = "cancelled", time.time()
        return job

    def _work(self, lane: str) -> None:
        while True:
            job, action = self._queues[lane].get()
            if job.cancel.is_set():
                continue  # cancelled while queued
            job.state = "running"
            try:
                with self.idle:   # nothing lets go of the card while this runs (§9, slice 82)
                    result = action(self.make_service(job))
                job.result = _jsonable(result)
                outcomes = result if isinstance(result, list) else [result]
                if any(isinstance(o, Outcome) and o.blocked for o in outcomes):
                    job.state = "blocked"
                elif any(isinstance(o, Outcome) and o.status in ("failed", "incomplete") for o in outcomes):
                    job.state = "failed"
                else:
                    job.state = "done"
            except Cancelled:
                job.log.append("cancelled — everything finished so far is kept")
                job.state = "cancelled"
            except TimingUnavailable as e:
                # **a sentence, not a traceback** (§9, slice 82): every one of these is written for the
                # person reading the job's log — no provider, no room on the card, the card gone half
                # way through — and a stack trace under it says nothing they can act on.
                job.log.append(str(e))
                job.state = "failed"
            except Exception as e:  # a job must never kill the worker
                log.debug("job %s failed", job.id, exc_info=True)
                job.log.append(f"error: {e}")
                job.log.append(traceback.format_exc(limit=3))
                job.state = "failed"
            finally:
                job.finished = time.time()
                if job.lane == "write":
                    with contextlib.suppress(Exception):
                        self.wrote(job.target)


# what to call each container when a player asks for it. The suffix is the file's own claim and the
# only one this program has; an unknown one is `application/octet-stream`, which says "bytes" rather
# than a wrong name (§9, slice 77).
AUDIO_TYPES = {".opus": "audio/ogg", ".ogg": "audio/ogg", ".oga": "audio/ogg",
               ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac",
               ".flac": "audio/flac", ".wav": "audio/wav", ".webm": "audio/webm",
               ".mka": "audio/x-matroska", ".alac": "audio/mp4"}


def _fetched_cover(service: Service, url: str) -> bytes:
    """The picture at an address the user typed (§9, slice 156).

    Fetched through the album's own source, so whatever that source requires of a request — its
    headers, its session — applies here too, exactly as it does when a pass fetches a cover.
    """
    found = _download_cover(url, service.source_for_address(url))
    if not found:
        raise ValueError(f"nothing that is a picture came back from {url}")
    return found[1]


def audio_type(path: Path | None) -> str:
    return AUDIO_TYPES.get(path.suffix.lower(), "application/octet-stream") if path else "application/octet-stream"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Outcome):
        return {"status": value.status, "message": value.message, "changes": list(value.changes),
                "offered": list(value.offered), "album_dir": str(value.album_dir or ""),
                "plan": value.plan.to_dict() if value.plan else None}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, AlbumPlan):
        return value.to_dict()
    return value


class Details:
    """Fills in what a listing does not tell us (track count, cover), one playlist at a time.

    Runs beside the job worker: slow on purpose, cached, and it backs off when YouTube
    starts refusing (a fast search must never cost a bot check).
    """

    WORKERS = 2  # together with PAUSE: about one request per second
    PAUSE = 1.0  # between playlists, per worker
    BACKOFF = 600.0  # after a bot check

    def __init__(self, source: Callable[[], Any]) -> None:
        self._source = source
        self.known: dict[str, dict[str, Any]] = {}
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._blocked_until = 0.0
        for i in range(self.WORKERS):
            threading.Thread(target=self._work, name=f"noaap-details-{i}", daemon=True).start()

    def want(self, refs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Queue what we do not know yet; return what we already have."""
        for ref in refs:
            source_id, url = str(ref.get("id", "")), str(ref.get("url", ""))
            if not source_id or source_id in self.known or not self._source().handles(url):
                continue
            with self._lock:
                if source_id in self._pending:
                    continue
                self._pending.add(source_id)
            self._queue.put((source_id, url))
        return {r["id"]: self.known[r["id"]] for r in refs if r.get("id") in self.known}

    def _work(self) -> None:
        while True:
            source_id, url = self._queue.get()
            if time.monotonic() < self._blocked_until:
                self.known[source_id] = {"unknown": True}
            else:
                try:
                    self.known[source_id] = self._source().details(url) or {"unknown": True}
                except Exception as e:
                    self.known[source_id] = {"unknown": True}
                    if "not a bot" in str(e):
                        self._blocked_until = time.monotonic() + self.BACKOFF
                        log.info("details paused: YouTube is refusing requests")
                    else:
                        log.debug("details for %s: %s", url, e)
            with self._lock:
                self._pending.discard(source_id)
            time.sleep(self.PAUSE)


# -- the app ---------------------------------------------------------------------------------


# said by every door that would have sent a private album's audio somewhere (§9, slice 75)
PRIVATE_AUDIO = ("this album's audio came from a source one person paid its creator for: it is not sent to a timing provider that runs anywhere but this machine")

def _album_row(plan: AlbumPlan, cover: bool) -> dict[str, Any]:
    """One album as the page lists it. `cover` is passed in because the walk already knows."""
    return {
        "id": plan.source_id,
        "albumartist": plan.albumartist,
        "album": plan.album,
        "year": plan.year,
        "kind": plan.kind,
        "tracks": len(plan.tracks),
        "done": sum(t.state == "done" for t in plan.tracks),
        "failed": sum(t.state == "failed" and t.in_source for t in plan.tracks),
        "cover": cover,
        "mb": bool(plan.mbid) or any(t.mbid for t in plan.tracks),
        "lyrics": sum(t.lyrics in ("synced", "plain") for t in plan.tracks),
        "length": album_length_flag(plan),  # set only when most of the album disagrees
        "needs_choice": sum(t.error_kind == "no_audio_stream" for t in plan.tracks),
        # tracks where the near-miss check ran and could not decide for you (§9, slice 46):
        # the words exist and nothing was taken, so they wait for a person
        "needs_you": sum(needs_you(t) for t in plan.tracks),
        # tracks where a merge found another copy and could not rank it against the one in use
        # (§9, slice 55) — counted like `needs_you`, for the same reason: it is a decision only a
        # person can take, and nothing listed it before.
        "copies": sum(bool(t.undecided_copies()) for t in plan.tracks),
    }


@dataclass
class Held:
    """One walk of the library, kept in memory — what every read is answered from (§9, slice 139).

    Measured on the user's library over NFS, 1,523 albums and 20,100 tracks: one `/api/state` cost
    **107 seconds**, cold and warm alike, and did three full traversals to get there — `albums()`
    read and parsed every plan, `missing()` read them all again and `stat`ed every one of the 20,100
    files (62.6 s of it), and `library_version()` ran an `rglob` over every directory in the tree to
    hash mtimes the other two had already visited. `album()` called `library_version()` too, so one
    cover request paid the same `rglob`, and a page load fires one per card.
    """

    version: str = ""
    albums: list[dict[str, Any]] = field(default_factory=list)
    index: dict[str, Path] = field(default_factory=dict)            # source_id -> album folder
    plans: dict[Path, tuple[int, AlbumPlan]] = field(default_factory=dict)
    covers: dict[Path, tuple[int, bool]] = field(default_factory=dict)
    # **not measured here** (R-499, R-500): one `stat` per track is 62.6 s on this library, so it
    # belongs to `check`/`repair` and never to a page request. What the last sweep found is carried.
    missing: dict[str, Any] = field(default_factory=lambda: {"albums": 0, "tracks": 0, "where": [],
                                                             "measured": None})
    at: float = 0.0


class App:
    def __init__(self, cfg: Config, library: Path, host: str = "127.0.0.1", port: int = 8765, service_factory=None) -> None:
        self.cfg, self.library, self.host, self.port = cfg, library.expanduser(), host, port
        self.last_request = time.monotonic()
        self.http = httpx.Client(timeout=10, follow_redirects=True, headers={"User-Agent": user_agent()})
        self._thumbs: dict[str, tuple[bytes, str]] = {}
        # one hold on the graphics card for the whole server, so the models stay loaded from one
        # track to the next and the idle window is the one thing that gives them back (§9, slice 82)
        self.engines = Engines()
        self._service_factory = service_factory or (lambda job: Service(cfg, self.library, log=lambda s: _append(job, s), on_track=lambda t, what: _append(job, f"{what}: {t.number:02d} {t.artist} - {t.title}"), cancel=job.cancel, engines=self.engines))
        self.jobs = Jobs(self._service_factory, release=self.engines.let_go,
                         wrote=self.wrote_one, idle_seconds=cfg.timing_card_idle_seconds)
        self.details = Details(lambda: sources.get(None, self.cfg))
        self._track_index: dict[str, Any] = {"version": "", "albums": {}}
        # a service with no job behind it, for the questions the page asks while nothing is running
        # (today: what lrclib already holds for a track, §9, slice 42). Built once, because its lyrics cache
        # is a sqlite connection and an album panel asks this per track.
        self._reader: Service | None = None
        self._reader_for: Path | None = None
        # {source_id: album folder}, rebuilt when any plan file changes (§9, slice 90)
        self._album_index: dict[str, Path] = {}
        self._album_index_for: str | None = None
        # **one walk, one model** (§9, slice 139). `_rescanning` lets exactly one walk run, and it is
        # not held while anything is read — a cover or a track must never wait behind a rescan.
        self._held = Held()
        self._rescanning = threading.Lock()
        self._scanner: threading.Thread | None = None
        self._dirty = threading.Event()
        self._stop_scanning = threading.Event()

    # -- the one walk (§9, slice 139) --------------------------------------------------

    STALE_SECONDS = 60      # the bound a page's answer may be behind the disk by
    QUIET_SECONDS = 5       # the floor between two walks, so a burst of writes costs one

    def held(self) -> Held:
        """What a read is answered from. **Never walks the library** — except for the very first
        caller, who has nothing to be answered from yet (§9, slice 139, slice 152).

        It used to walk whenever `_dirty` was set, which every write of ours sets. So while the user
        was deleting albums and identifying them, nearly every `/api/state` paid a full walk: one
        glob, a `stat` per plan and a `stat` per album folder, about three thousand round trips.
        Over NFS with the share busy that was **8 seconds a request** against the 0.58 s this walk
        costs on an idle share (R-523/R-524). A page's answer must not depend on how loaded the disk
        is, so our own writes now patch the one album they touched into what is held
        (`album_changed`), and the walk belongs to the background thread alone.
        """
        if not self._held.at:
            self.rescan()
        self._keep_watching()
        return self._held

    def held_now(self) -> Held:
        """What is held this instant, and **never a walk** (§9, slice 139, R-499 item 4).

        For the requests that carry bytes — a cover, a track's audio. A page load fires one cover
        request per card, and none of them may wait behind a rescan: they are answered from whatever
        the last walk found, and an id the walk has not seen yet is looked up on its own below.
        """
        if not self._held.at:
            self.rescan()           # nothing is held at all, so there is nothing else to answer from
        self._keep_watching()
        return self._held


    def _keep_watching(self) -> None:
        if self._scanner is None or not self._scanner.is_alive():
            self._scanner = threading.Thread(target=self._scanning, name="noaap-rescan", daemon=True)
            self._scanner.start()

    def _scanning(self) -> None:
        """Rescan at most once in `STALE_SECONDS`, and soon after a pass of ours has written.

        **Soon, not at once** (§9, slice 152). Since a read never walks, this thread is the only
        thing that does, and every write sets `_dirty` — so an `update` writing album after album
        would have it walking back to back, which on a library over NFS is seconds of the share's
        attention each time, for nothing a page is waiting on. `QUIET_SECONDS` is the floor between
        two walks: a burst of writes coalesces into one, and each write has already patched its own
        album in, which is what the page actually needed.
        """
        while not self._stop_scanning.is_set():
            self._dirty.wait(self.STALE_SECONDS)
            if self._stop_scanning.is_set():
                return
            self._dirty.clear()
            try:
                self.rescan()
            except Exception as e:                      # a rescan that fails keeps the old answer
                log.warning("rescan failed, keeping what was held: %s", e)
            if self._stop_scanning.wait(self.QUIET_SECONDS):
                return

    def library_changed(self) -> None:
        """A pass of ours wrote something, so the next tick rescans instead of waiting a minute."""
        self._dirty.set()

    def wrote_one(self, target: str | None = None) -> None:
        """A write job has finished. If it names its album, patch that album in; else ask for a walk.

        Either way `_dirty` is set, so the background thread walks shortly: a patch is what makes the
        page see its own edit at once, not a replacement for the walk (§9, slice 152).
        """
        album_dir = self._held.index.get(target) if target else None
        if album_dir is None:
            self.library_changed()
            return
        self.album_changed(album_dir)

    def album_changed(self, album_dir: Path) -> None:
        """**One album of ours has just been written, so patch that album in** (§9, slice 152).

        A page that cannot see what it has just done is broken, and that is why `held` used to walk
        on every write. But seeing one's own edit does not need the other 1,522 albums re-`stat`ed:
        it needs this folder read again. So this reads one plan, looks once for its cover, and swaps
        in a model that differs in that album alone — no glob, no walk, three I/O calls instead of
        three thousand. A full walk still follows in the background, because a patch cannot see a
        folder that was renamed or one that appeared somewhere else.
        """
        self._dirty.set()
        old = self._held
        if not old.at:
            return                      # nothing is held yet; the first reader will walk anyway
        fresh = replace(old, plans=dict(old.plans), index=dict(old.index),
                        covers=dict(old.covers), albums=list(old.albums), at=time.monotonic())
        path = album_dir / PLAN_FILE
        gone = [sid for sid, d in fresh.index.items() if d == album_dir]
        try:
            when = path.stat().st_mtime_ns
            plan = read_plan(path, album_dir)
        except (OSError, ValueError, KeyError, TypeError):
            # written and then removed, or not readable: drop what we held about it
            fresh.plans.pop(album_dir, None)
            fresh.covers.pop(album_dir, None)
            for sid in gone:
                fresh.index.pop(sid, None)
            fresh.albums = [a for a in fresh.albums if a["id"] not in gone]
            fresh.version = self._stamp_of(fresh.plans)
            self._held = fresh
            return
        try:
            folder = album_dir.stat().st_mtime_ns
        except OSError:
            folder = 0
        cover = any(album_dir.glob(f"{COVER_STEM}.*"))
        fresh.plans[album_dir] = (when, plan)
        fresh.covers[album_dir] = (folder, cover)
        for sid in gone:
            if sid != plan.source_id:
                fresh.index.pop(sid, None)
        fresh.index[plan.source_id] = album_dir
        row = _album_row(plan, cover)
        kept = [a for a in fresh.albums if a["id"] not in gone and a["id"] != row["id"]]
        kept.append(row)
        kept.sort(key=lambda a: (natural_key(a["albumartist"]), a["year"] is None,
                                 a["year"] or 0, natural_key(a["album"])))
        fresh.albums = kept
        fresh.version = self._stamp_of(fresh.plans)
        self._held = fresh           # one assignment, so a reader sees the old model or the new one

    @staticmethod
    def _stamp_of(plans: dict[Path, tuple[int, AlbumPlan]]) -> str:
        """The library's version, from what is held rather than from a walk — so a patch and a walk
        answer the same string for the same library."""
        stamp = [f"{d / PLAN_FILE}:{when}" for d, (when, _) in sorted(plans.items())]
        return hashlib.sha1("".join(stamp).encode()).hexdigest()[:12]

    def rescan(self, measure: bool = False) -> Held:
        """Walk once and swap the result in. One walk at a time; readers never wait for it."""
        if not self._rescanning.acquire(blocking=False):
            return self._held       # somebody is already walking; what is held is good enough
        try:
            fresh = self._walk(self._held, measure=measure)
            self._held = fresh      # one assignment, so a reader sees the old model or the new one
            return fresh
        finally:
            self._rescanning.release()

    def _walk(self, old: Held, measure: bool = False) -> Held:
        """One `glob('*/*/.noaap.json')` — 0.58 s on the user's library against `rglob`'s 10.5 s,
        because it does not descend into an album — and **only the plans that changed are read
        again**: 0.01 s each, against 7.25 s for all 1,523 of them.
        """
        fresh = Held(missing=old.missing, at=time.monotonic())
        if not self.library.exists():
            return fresh
        for path in sorted(self.library.glob(f"*/*/{PLAN_FILE}")):
            album_dir = path.parent
            try:
                when = path.stat().st_mtime_ns
            except OSError:
                continue
            if (kept := old.plans.get(album_dir)) and kept[0] == when:
                plan = kept[1]
            else:
                try:
                    plan = read_plan(path, album_dir)
                except (ValueError, KeyError, TypeError, OSError) as e:
                    log.warning("ignoring unreadable plan %s: %s", path, e)
                    continue
            fresh.plans[album_dir] = (when, plan)
            fresh.index[plan.source_id] = album_dir
            # the cover is a file in the album folder, so the folder's own mtime says whether to look
            try:
                folder = album_dir.stat().st_mtime_ns
            except OSError:
                folder = 0
            if (seen := old.covers.get(album_dir)) and seen[0] == folder:
                cover = seen[1]
            else:
                cover = any(album_dir.glob(f"{COVER_STEM}.*"))
            fresh.covers[album_dir] = (folder, cover)
            fresh.albums.append(_album_row(plan, cover))
        fresh.version = self._stamp_of(fresh.plans)
        # artist, then chronological, then by name. Albums with no year all tie, so compilations
        # keep their natural order (Vol. 1 … Vol. 20) until someone fills a year in.
        fresh.albums.sort(key=lambda a: (natural_key(a["albumartist"]), a["year"] is None,
                                         a["year"] or 0, natural_key(a["album"])))
        if measure:
            fresh.missing = self._measure_missing(fresh)
        return fresh

    def _measure_missing(self, held: Held) -> dict[str, Any]:
        """One `stat` per track — 62.6 s on the user's library, so only a sweep that was asked for
        does this (R-499). `check` and `repair` ask; a page request never does."""
        from .download import lost_files

        found = [(d, lost) for d, (_, p) in sorted(held.plans.items()) if (lost := lost_files(d, p))]
        return {"albums": len(found), "tracks": sum(len(lost) for _, lost in found),
                "where": [str(d.relative_to(self.library)) for d, _ in found[:5]],
                "measured": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}

    # read side

    @property
    def reader(self) -> Service:
        if self._reader is None or self._reader_for != self.library:
            self._reader, self._reader_for = Service(self.cfg, self.library), self.library
        return self._reader

    def library_version(self) -> str:
        """Changes whenever any plan file does — from the one walk, never a tree of its own.

        It used to `rglob` the whole library on every poll (10.5 s on the user's over NFS) to hash
        mtimes the walk had already read, and `album()` asked for it on every cover request
        (§9, slice 139).
        """
        return self.held().version

    #: one track in the index: video id, artist, title, downloaded, trim start, trim end
    TRACK_FIELDS = ("video_id", "artist", "title", "done", "trim_start", "trim_end")

    def track_index(self) -> dict[str, Any]:
        """Every track by album, as compact rows — the UI filters and plays songs with it.

        Sent once and re-fetched only when `library_version` changes, so typing costs nothing.
        Rows are lists, not objects: the field names would otherwise repeat 1300 times.
        """
        version = self.library_version()
        if self._track_index["version"] != version:
            albums = (
                {
                    plan.source_id: [
                        [t.video_id, t.artist, t.title, int(t.state == "done"), t.trim_start, t.trim_end] for t in plan.tracks
                    ]
                    for _, plan in iter_plans(self.library)
                }
                if self.library.exists()
                else {}
            )
            self._track_index = {"version": version, "fields": list(self.TRACK_FIELDS), "albums": albums}
        return self._track_index

    def albums(self) -> list[dict[str, Any]]:
        """Every album, from the one walk (§9, slice 139)."""
        return self.held().albums

    def album(self, source_id: str) -> tuple[Path, AlbumPlan] | None:
        """One album, by an index of folders rather than by reading the library until it matches.

        **Every request that names an album used to walk the plans** (§9, slice 90): measured on a
        library of 250 albums, 6 ms for the first album and **155 ms for the last**, and a page load
        fires one cover request per card — about 18 seconds of JSON parsing for one refresh, which is
        what the first press of play was waiting behind. The index is rebuilt when any plan file
        changes, which `library_version` already answers cheaply.
        """
        if not self.library.exists():
            return None
        # **what is held, never a fresh walk** (R-499 item 4): a cover or a track must not queue
        # behind a rescan, and an id this model has not seen is looked up by itself just below.
        held = self.held_now()
        album_dir = held.index.get(source_id)
        if album_dir is None or not album_dir.is_dir():
            # not in what is held: the album may be newer than the last walk, so look once
            found = next(((d, p) for d, p in iter_plans(self.library) if p.source_id == source_id), None)
            if found:
                self.library_changed()
            return found
        # **the plan is read, not taken from the walk**: an album panel is where a person edits, and
        # an edit must start from what is on disk this second, not from a model up to a minute old.
        try:
            return album_dir, load_plan(album_dir)
        except (ValueError, KeyError, TypeError, OSError):
            return None

    def with_links(self, plan: AlbumPlan) -> dict[str, Any]:
        """The plan as the page wants it: each candidate with a link, where its provider has one.

        The page must not build a link from a ref. A ref means something only to the provider that
        minted it — a folder's is a path on this machine, and a URL made out of one would be both
        wrong and a home directory in a link (§9, slices 51 and 54).
        """
        out = plan.to_dict()
        album_dir = found[0] if (found := self.album(plan.source_id)) else None
        links: dict[str, str | None] = {}
        for track, row in zip(plan.tracks, out["tracks"], strict=False):
            # **whether the untouched original is really there** (§9, slice 88). `o=1` falls back to the
            # cut file when it is not, so a page that assumes otherwise adds the trim to a file that
            # already carries it — the offsets and the window then belong to a file nobody is holding.
            row["original_kept"] = bool(album_dir is not None and track.trimmed
                                        and original_path(album_dir, track).is_file())
        for track in out["tracks"]:
            for candidate in track.get("candidates") or []:
                name = candidate.get("provider") or sources.DEFAULT
                if name not in links:
                    try:
                        links[name] = getattr(sources.get(name, self.cfg), "url_for", lambda _r: None)
                    except ValueError:  # a provider this build does not have: no link, no crash
                        links[name] = None
                maker = links[name]
                candidate["url"] = maker(candidate["ref"]) if maker else None
        return out

    def album_view(self, source_id: str) -> AlbumPlan | None:
        """The album as the view opens it: checked against the `.lrc` files on disk first.

        A sidecar edited or deleted outside the UI used to go unnoticed until some pass walked the
        album — the row could show ♪ for words that were gone (DESIGN.md §9, slice 30). Opening the album
        now applies the same `reconcile` rules the passes apply, so the view tells the truth at
        once; when they found something, a write job makes it durable and brings the tags along.
        """
        found = self.album(source_id)
        if not found:
            return None
        album_dir, plan = found
        changed = [t for t in plan.tracks if t.state == "done" and reconcile(album_dir, t, album_dir / t.filename)[1]]
        if changed and not self.jobs.working_on(source_id):
            self.jobs.submit("lyrics", f"Check the lyrics of {plan.album}",
                             lambda s: s.sync_lyrics(source_id), target=source_id)
        return plan

    def cover(self, source_id: str, thumb: bool = False) -> tuple[bytes, str, float] | None:
        """The album's cover, its type, and when the file was last written (§9, slice 90).

        With `thumb`, a thumbnail for the grid instead — kept on the local disk, so the cover crosses
        NFS once and never again while the file is unchanged (§9, slice 140).
        """
        found = self.album(source_id)
        if not found:
            return None
        for path in sorted(found[0].glob(f"{COVER_STEM}.*")):
            try:
                about = path.stat()
            except OSError:
                continue
            if thumb and (small := self._thumb_file(path, about)):
                return small[0], small[1], about.st_mtime
            data = path.read_bytes()
            if mime := image_mime(data):
                return data, mime, about.st_mtime
        return None

    def _thumb_file(self, path: Path, about: os.stat_result) -> tuple[bytes, str] | None:
        """The cached thumbnail of this cover, making it first if need be (§9, slice 140).

        **Keyed by where the file is and what it was when we read it** — its size and its mtime —
        so a cover that is replaced gets a new key and the old entry is simply never asked for
        again. Nothing here fails a request: a cache that cannot be written or read falls through to
        the cover itself.
        """
        key = hashlib.sha1(f"{path}:{about.st_size}:{about.st_mtime_ns}:{THUMB_SIDE}".encode()).hexdigest()[:20]
        where = config_mod.cache_dir() / "thumbs" / f"{key}.jpg"
        with contextlib.suppress(OSError):
            if where.is_file():
                return where.read_bytes(), "image/jpeg"
        data = path.read_bytes()
        if not image_mime(data):
            return None
        made = make_thumbnail(data)
        if made is None:
            return None            # already no bigger than a thumbnail: send the file itself
        with contextlib.suppress(OSError):
            where.parent.mkdir(parents=True, exist_ok=True)
            tmp = where.with_suffix(".part")
            tmp.write_bytes(made[0])
            tmp.replace(where)     # whole or absent, never half a picture
        return made

    def lyrics(self, source_id: str, video_id: str) -> dict[str, Any] | None:
        """The lyrics of one track, read from the `.lrc` beside it (that file is the original)."""
        found = self.album(source_id)
        if not found:
            return None
        album_dir, plan = found
        track = next((t for t in plan.tracks if t.video_id == video_id), None)
        if not track:
            return None
        return {"status": track.lyrics, "lrclib_id": track.lyrics_id, "text": read_sidecar(album_dir, track) or "",
                "owner": track.provenance.get("lyrics"), "state": track.state,
                # who put the stamps there, when it was not a person (§9, slice 36)
                "timed_by": track.lyrics_timed_by, "words_by": track.lyrics_words_by,
                # timestamps written for another file point at the wrong seconds; the panel says so
                # until the words are saved again, and never re-times anything itself (§9, slice 34)
                "timings": timings_stale(track),
                # whether these words may be given back to lrclib, and why not when they may not
                # (§9, slice 42). The reason is shown, because "no button" is a worse answer than "no,
                # because these are lrclib's own words".
                "publish": self._publish_state(album_dir, plan, track),
                # an entry that is nearly this recording, and what was made of it (§9, slice 46)
                "fit": track.lyrics_fit,
                "can_check": bool(track.state == "done" and track.file_length
                                  and (track.lyrics or "none") == "none" and ALIGN in capabilities_of(self.cfg)
                                  and may_send_audio(self.cfg, plan, ALIGN)),
                # **what the server will refuse, the page does not offer** (§9, slice 75): a private
                # album's audio goes to a timing provider only if that provider runs on this machine
                "may_send_audio": {"align": may_send_audio(self.cfg, plan, ALIGN),
                                   "draft": may_send_audio(self.cfg, plan, TRANSCRIBE),
                                   "listen": may_send_audio(self.cfg, plan, LISTEN)}}

    def recycled(self) -> int:
        """How many things are in the bin — counted, not listed (§9, slice 91).

        The header shows this on every poll, so it may not read a file: one `iterdir` of the bin, where
        the full listing reads a `bin.json` per entry and measures its files.
        """
        from .recycle import bin_root

        root = bin_root(self.library)
        try:
            return sum(1 for path in root.iterdir() if path.is_dir())
        except OSError:
            return 0

    def recycle(self) -> list[dict[str, Any]]:
        """The bin, for the page. Deliberately without any path: an entry is its id.

        **The library this server is serving**, not the one a config file happens to name: an app given
        its library directly — a test, `noaap serve --library` — showed an empty bin for a bin that had
        things in it.
        """
        from .recycle import entries

        return [{"id": e.id, "when": e.when, "reason": e.reason, "artist": e.artist,
                 "title": e.title, "album": e.album, "bytes": e.bytes,
                 "track": bool(e.data.get("track"))} for e in entries(self.library)]

    def _publish_state(self, album_dir: Path, plan: AlbumPlan, track: PlanTrack) -> dict[str, Any]:
        text = (read_sidecar(album_dir, track) or "").strip()
        published = track.lyrics_published or {}
        if not text:
            return {"can": False, "why": "", "published": published.get("at", "")}
        why = publishable(track, text, self.reader.their_words(track))
        return {"can": not why, "why": why, "published": published.get("at", ""),
                "lines": len(text.splitlines()),
                "length": round(track.file_length or track.duration or 0.0, 1),
                "album": plan.album or "", "artist": track.artist, "title": track.title}

    def audio_path(self, source_id: str, video_id: str, original: bool = False) -> Path | None:
        """The finished track's file — looked up in the plan, never taken from the request.

        `original` asks for the untouched download of a track that has been trimmed: the
        player works in the trim's coordinates (they count from the start of the video), so
        it must hear the file those numbers describe, not the one already cut to them.
        """
        found = self.album(source_id)
        if not found:
            return None
        album_dir, plan = found
        track = next((t for t in plan.tracks if t.video_id == video_id and t.state == "done"), None)
        if not track:
            return None
        if original and (uncut := original_path(album_dir, track)).is_file():
            return uncut
        path = _inside(album_dir, track.filename)
        return path if path and path.is_file() else None

    def thumbnail(self, url: str) -> tuple[bytes, str] | None:
        """Fetch a thumbnail for the page (allowlisted hosts only), with a small memory cache."""
        host = urlsplit(url).hostname or ""
        if urlsplit(url).scheme != "https" or not any(host == h or host.endswith("." + h) for h in THUMB_HOSTS):
            return None
        if hit := self._thumbs.get(url):
            return hit
        try:
            r = self.http.get(url)
        except httpx.HTTPError as e:
            log.debug("thumbnail %s: %s", url, e)
            return None
        if r.status_code != 200 or not (mime := image_mime(r.content)):
            return None
        if len(self._thumbs) >= THUMB_CACHE:
            self._thumbs.pop(next(iter(self._thumbs)))
        self._thumbs[url] = (r.content, mime)
        return self._thumbs[url]

    def missing(self) -> dict[str, Any]:
        """`{albums, tracks, where, measured}` — empty when every plan's files are where it says.

        Not the same question as "the folder this album was taken in from is gone": that is a fact
        about a source, the tracks here are complete, and it belongs where a re-fetch is asked for
        (R-207, ruling 3).

        **What the last sweep found, with the time it was taken.** Asking it afresh is one `stat` per
        track — 62.6 s on the user's library over NFS, three quarters of what `/api/state` used to
        cost — so `check` and `repair` measure it and a page request reads the answer (§9, slice 139).
        """
        return self.held().missing

    def watching(self) -> list[dict[str, Any]]:
        """The configured watches, what each is for, and when it was last looked at.

        Read from the watcher's own state file: this service does not watch anything, and saying so
        from the same place the watcher writes is the only way the page can be honest about a
        process it does not run (§9, slice 59).
        """
        from . import watch as watch_pass

        state = watch_pass.read_state()
        out = []
        for row in self.cfg.watches:
            kept = state.get(row.name, {})
            out.append({"name": row.name, "shape": row.shape, "folder": str(row.folder),
                        "there": row.folder.is_dir(), "looked": kept.get("looked"),
                        "waiting": len(kept.get("waiting") or {})})
        return out

    # what a provider that needs an account of its own is configured with. The field names are the
    # provider's own (`<name>_cookies_file`), so this asks the registry and the config rather than
    # naming anybody: a second such source is configurable here the day it registers itself.
    SOURCE_FIELDS = ("cookies_from_browser", "cookies_file", "audio_from_video", "captions", "post_cap")
    AS_TEXT = ("cookies_from_browser", "cookies_file")

    def source_settings(self) -> dict[str, dict[str, Any]]:
        """Each such provider's own settings (§9, slice 92) — a cookies file as a *path*, never its
        contents, which this server does not read and this answer therefore cannot carry."""
        out: dict[str, dict[str, Any]] = {}
        for name in sources.known():
            fields = {short: getattr(self.cfg, f"{name}_{short}")
                      for short in self.SOURCE_FIELDS if hasattr(self.cfg, f"{name}_{short}")}
            if not fields:
                continue
            out[name] = {short: ("" if value is None else str(value)) if short in self.AS_TEXT else value
                         for short, value in fields.items()}
        return out

    def settings(self) -> dict[str, Any]:
        runtime = self.cfg.resolved_js_runtime()
        pot = self.cfg.resolved_pot_provider()
        return {
            "library": str(self.library),
            # the version this process is, and where its code comes from — a release install or a
            # checkout (§9, slice 93). Its own field because the release script reads it back.
            "version": running_from()[0],
            "running_from": running_from()[1],
            "cookies_from_browser": self.cfg.cookies_from_browser,
            "cookies_file": str(self.cfg.cookies_file or ""),
            "browsers": config_mod.detect_browsers(),
            "musicbrainz": self.cfg.musicbrainz,
            # whether a pass may take away empty folders not of its own making (§9, slice 104)
            "remove_empty_folders": self.cfg.remove_empty_folders,
            # **what every album should have** (§9, slice 100): the state the library is in. A pass
            # brings the albums it touches to these; an album can only be excepted from one.
            "state": {key: bool(getattr(self.cfg, key)) for key in TREATMENT_KEYS},
            # what `noaap watch` is looking at, if anything (§9, slice 59). Named by the watch and
            # by its shape; the folder is shown as the user wrote it and never in a job's label.
            "watching": self.watching(),
            # albums whose own files are not where their plan says — the one thing worth saying
            # when a library has been moved and not yet told about it (§9, slice 60)
            "missing": self.missing(),
            # Two slots, one per capability (§9, slice 40), and everything that depends on *which* provider
            # is answered per slot: what it can do, whether it sends the audio away, what it charges.
            "timing": {"provider": self.cfg.timing_provider,
                       "align_provider": kind_for(self.cfg, ALIGN),
                       "draft_provider": kind_for(self.cfg, TRANSCRIBE),
                       "endpoint": self.cfg.timing_endpoint or "",
                       # asked of the provider, not of the config: an endpoint that is down, or an
                       # extra that is not installed, offers nothing and the page shows nothing
                       "capabilities": sorted(capabilities_of(self.cfg)),
                       # whether a key is set, never the key itself (§9, slice 37)
                       "keys": {v: bool(getattr(self.cfg, f"timing_{v}_key", "")) for v in VENDORS},
                       "vendors": list(VENDORS),
                       # which kinds may be chosen for which slot, so the panel offers no
                       # provider that could never do that job (§9, slice 40)
                       "offers": {kind: list(what) for kind, what in OFFERS.items()},
                       "price": {what: list(PRICES.get(kind_for(self.cfg, what), ("", "")))
                                 for what in (ALIGN, TRANSCRIBE, LISTEN)},
                       "sends_audio": {what: kind_for(self.cfg, what) in VENDORS
                                       for what in (ALIGN, TRANSCRIBE, LISTEN)},
                       # whether every alignment is checked against a second method (§9, slice 38): it is
                       # the provider's answer, and it costs the user time, so the panel says so
                       "verifies": verifies_with(self.cfg),
                       # what `local` is told to use, and whether it checks itself (§9, slice 92)
                       "device": self.cfg.timing_device,
                       "verify": "auto" if self.cfg.timing_verify is None else bool(self.cfg.timing_verify)},
            # where MusicBrainz lives, so the page can link to a recording it cannot seed (§9, slice 43)
            "musicbrainz_web": MB_WEB,
            # where else music comes from (§9, slice 92). A path, never the contents of the file.
            "sources": self.source_settings(),
            "pot_mode": self.cfg.pot_mode,
            "pot_idle_minutes": round(self.cfg.pot_idle / 60),
            "concurrency": self.cfg.concurrency,
            "info": {
                # **which code is answering** (§9, slice 93): the released version, and whether this
                # process is that release or a checkout somebody may be editing
                "noaap version": " — ".join(running_from()),
                "config file": str(config_mod.read_path()),
                "JavaScript runtime": " ".join(filter(None, runtime)) if runtime else "none found",
                "token generator": str(pot) if pot else "not set up (see README)",
                "audio": "Opus, the best stream YouTube offers, never re-encoded",
            },
        }

    def save_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        """Validate everything first, then apply to the running app and the config file."""
        changes: dict[str, Any] = {}
        if "cookies_from_browser" in body:
            browser = body["cookies_from_browser"] or None
            if browser not in (None, *config_mod.detect_browsers()):
                raise ValueError(f"unknown browser {browser!r}")
            changes["cookies_from_browser"] = browser
        if "musicbrainz" in body:
            changes["musicbrainz"] = bool(body["musicbrainz"])
        if "remove_empty_folders" in body:
            changes["remove_empty_folders"] = bool(body["remove_empty_folders"])
        for key in TREATMENT_KEYS:
            if key in body:
                changes[key] = bool(body[key])
        if "pot_mode" in body:
            if body["pot_mode"] not in ("server", "script", "off"):
                raise ValueError("token helper mode must be server, script or off")
            changes["pot_mode"] = body["pot_mode"]
        if "pot_idle_minutes" in body:
            minutes = int(body["pot_idle_minutes"])
            if not 1 <= minutes <= 120:
                raise ValueError("token server idle time must be 1–120 minutes")
            changes["pot_idle"] = minutes * 60
        if "concurrency" in body:
            n = int(body["concurrency"])
            if not 1 <= n <= 4:
                raise ValueError("parallel requests must be 1–4 (more trips YouTube's bot check)")
            changes["concurrency"] = n
        for slot in ("timing_provider", "timing_align_provider", "timing_draft_provider"):
            if slot not in body:
                continue
            chosen = str(body[slot])
            # an empty slot is legal and means "whatever timing_provider says" (§9, slice 40)
            if chosen not in PROVIDERS and not (chosen == "" and slot != "timing_provider"):
                raise ValueError(f"the timing provider must be one of {', '.join(PROVIDERS)}")
            changes[slot] = chosen
        # **Sources** (§9, slice 92): where else music comes from. Written per provider, by the field
        # names the provider gave its own settings, so nothing here decides which sources exist. A
        # cookies file is a *path*; its contents are never read by the page and never sent back to it.
        for name in sources.known():
            for short in self.SOURCE_FIELDS:
                field = f"{name}_{short}"
                if field not in body or not hasattr(self.cfg, field):
                    continue
                if short == "cookies_from_browser":
                    browser = str(body[field] or "").strip() or None
                    if browser not in (None, *config_mod.detect_browsers()):
                        raise ValueError(f"unknown browser {browser!r}")
                    changes[field] = browser
                elif short == "cookies_file":
                    given = str(body[field] or "").strip()
                    if not given:
                        changes[field] = None      # the path is cleared; the file is left alone
                        continue
                    path = Path(given).expanduser()
                    if not path.is_absolute():
                        raise ValueError("the cookies file must be named in full (an absolute path)")
                    if not path.is_file():
                        raise ValueError(f"there is no file at {path}")
                    changes[field] = str(path)
                elif short == "post_cap":
                    changes[field] = max(1, int(body[field]))
                else:
                    changes[field] = bool(body[field])
        if "timing_device" in body:
            device = str(body["timing_device"] or "auto")
            if device not in ("auto", "cpu", "cuda"):
                raise ValueError("the device must be auto, cpu or cuda")
            changes["timing_device"] = device
        if "timing_verify" in body:
            given = body["timing_verify"]
            changes["timing_verify"] = None if given in ("", None, "auto") else bool(given)
        for vendor in VENDORS:
            field = f"timing_{vendor}_key"
            if field in body:
                # write-only: the page sends a key or an empty string, and never gets one back
                changes[field] = str(body[field]).strip()
        if "timing_endpoint" in body:
            endpoint = str(body["timing_endpoint"]).strip()
            if endpoint and not endpoint.startswith(("http://", "https://")):
                raise ValueError("the timing endpoint must be an http(s) URL, e.g. http://host:8770")
            changes["timing_endpoint"] = endpoint or None
        # whichever slot names `http` or a vendor, the thing it needs has to be there — checked for
        # every slot being written, since either can now name either
        for slot in ("timing_provider", "timing_align_provider", "timing_draft_provider"):
            chosen = changes.get(slot, getattr(self.cfg, slot, ""))
            if chosen == "http" and not (changes.get("timing_endpoint") or self.cfg.timing_endpoint):
                raise ValueError("choose an endpoint for the `http` timing provider")
            if chosen in VENDORS and not (changes.get(f"timing_{chosen}_key")
                                          or getattr(self.cfg, f"timing_{chosen}_key", "")):
                raise ValueError(f"{chosen} needs an API key — it is sent to them with the audio, "
                                 "and stays on this machine otherwise")
        # **the watched folders, editable** (§9, slice 92). The watcher is a separate service and this
        # only writes what it reads: the same two shapes, refused as a set by the rules that already
        # judge them, so the page cannot save a nesting the watcher would then refuse to start on.
        watches: list[config_mod.Watch] | None = None
        if "watches" in body:
            rows = body["watches"] if isinstance(body["watches"], list) else []
            watches = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                folder = str(row.get("folder") or "").strip()
                if not folder:
                    continue
                shape = str(row.get("shape") or "intake")
                if shape not in ("intake", "library"):
                    raise ValueError(f"a watched folder is either intake or library, not {shape!r}")
                path = Path(folder).expanduser()
                if not path.is_dir():
                    raise ValueError(f"there is no folder at {path}")
                watches.append(config_mod.Watch(name=str(row.get("name") or path.name),
                                                folder=path, shape=shape))
            if trouble := config_mod.watch_trouble(watches, self.library):
                raise ValueError(trouble[0])

        library = None
        if "library" in body and str(body["library"]).strip() != str(self.library):
            library = Path(str(body["library"]).strip()).expanduser()
            if not library.is_absolute():
                raise ValueError("the library folder must be an absolute path")
            if self.jobs.busy():
                raise ValueError("wait until the running jobs are finished before changing the library")
            library.mkdir(parents=True, exist_ok=True)  # ValueError-free: OSError surfaces as 400 below
            changes["library_root"] = str(library)

        for name, value in changes.items():
            if name != "library_root":
                setattr(self.cfg, name, value)  # job services share this Config: effective from the next job
            config_mod.save_setting(name, value)
        # a provider that nobody can ask for any more should not go on holding the card until the
        # idle window comes round — this is also how "stop using the card" takes effect at once
        if any(name.startswith("timing_") for name in changes) and not self.jobs.busy():
            self.engines.let_go()
        if watches is not None:
            config_mod.save_watches(watches)
            self.cfg.watches = watches
        if library is not None:
            self.library = library
            self._reader = None  # it holds the old library, and its lyrics cache with it
            # and so does the model: every album in it belongs to the library we have just left
            # (§9, slice 139), so it is thrown away rather than marked stale
            self._held = Held()
        return self.settings()

    def state(self) -> dict[str, Any]:
        """What the page polls. **One call, one model, no walk** (§9, slice 139) — and the model it
        reads may be up to `STALE_SECONDS` behind the disk, which the README states."""
        held = self.held()
        return {
            "held_at": round(time.monotonic() - held.at, 1),   # how old this answer is, in seconds
            "stale_after": self.STALE_SECONDS,
            "settings": self.settings(),
            "library": str(self.library),
            "albums": held.albums,
            "jobs": [j.summary() for j in self.jobs.recent()],
            "busy": self.jobs.busy(),
            "busy_write": self.jobs.busy("write"),
            "musicbrainz": self.cfg.musicbrainz,
            "tracks_version": held.version,
            # what the bin holds, so the header can offer it only when there is something in it
            "recycled": self.recycled(),
        }

    # write side (each returns a queued job)

    def submit(self, action: str, body: dict[str, Any]) -> Job:
        match action:
            case "open":  # a URL or an artist name, whatever the user typed
                text = str(body.get("q", "")).strip()
                if not text:
                    raise ValueError("empty input")
                if text.startswith(("http://", "https://")):
                    if collection_address(text):
                        return self.jobs.submit("channel", f"channel {text}", lambda s: {"groups": _groups(s.channel(text))})
                    return self.jobs.submit("preview", f"preview {text}", lambda s: s.fetch(text, dry=True))
                return self.jobs.submit("search", f"search {text}", lambda s: _search_result(s.search(text)))
            case "fetch":
                urls = [str(u) for u in body.get("urls") or [] if str(u).startswith(("http://", "https://"))]
                if not urls:
                    raise ValueError("no URLs")
                label = self.describe(urls[0]) if len(urls) == 1 else f"{len(urls)} sources"

                def fetch_all(s: Service):
                    outcomes = []
                    for i, url in enumerate(urls, 1):
                        if len(urls) > 1:
                            s.log(f"=== [{i}/{len(urls)}] {url}")
                        outcomes.append(s._guarded(lambda: s.fetch(url)))
                        if outcomes[-1].blocked:
                            s.log("stopping: YouTube is blocking requests")
                            break
                    return outcomes

                return self.jobs.submit("fetch", f"Fetch {label}" if len(urls) > 1 else f"Update {label}" if " — " in label else f"Fetch {label}", fetch_all)
            case "arrived":
                # **the watcher's one way in** (§9, slice 59, R-200 ruling 2). It names a
                # configured watch root and a path *relative* to it; an absolute path is not
                # accepted at all, and one that climbs out of the root is refused. It is a write
                # like any other, so it needs the `X-Noaap` header too.
                return self.jobs.submit(*self._arrival(body))
            case "identify":
                # **the same two steps as a repair** (§9, slice 142): look, then apply what was
                # listed. The lookup rewrites titles, numbers and discs, and a person who cannot see
                # that first is being asked to trust it blind.
                source_id = str(body.get("id", ""))
                found = self.album(source_id)
                if not found:
                    raise ValueError("unknown album")
                album_dir, plan = found
                where = str(album_dir.relative_to(self.library))
                dry = bool(body.get("dry_run"))
                what = f"{plan.albumartist} — {plan.album}"
                # `deep`, always: the point of the button is to look, so an album the cheap check
                # would call unchanged must still be read.
                return self.jobs.submit(
                    "identify" if dry else "identify_apply",
                    f"{'Identify' if dry else 'Apply what MusicBrainz says about'} {what}",
                    lambda s: s.update_all(report_only=dry, deep=True, only=[where]),
                    target=source_id)
            case "update":
                deep = bool(body.get("deep"))
                artist = str(body.get("artist") or "").strip() or None
                label = f"Update {artist}" if artist else "Update the library"
                return self.jobs.submit("update", label + (" (full)" if deep else ""), lambda s: s.update_all(deep=deep, artist=artist))
            case "repair":
                if running := self.jobs.writing():
                    # it renames folders all over the library, so it must not run beside a writer
                    raise ValueError(f"“{running.label}” is running — wait for it, then repair")
                # **two steps, and the first one writes nothing** (§9, slice 91): the check is the dry
                # run of slice 85, whose log names every file it would touch, and the page only offers
                # the apply after one has been read.
                if body.get("dry_run"):
                    # **the sweep that measures what is missing rides along here** (§9, slice 139):
                    # one `stat` per track is 62.6 s on the user's library, so it belongs to a pass
                    # somebody asked for and never to a page poll.
                    return self.jobs.submit("repair_check", "Check what a repair would do",
                                            lambda s: (s.repair(dry_run=True), self.rescan(measure=True))[0])
                return self.jobs.submit("repair", "Repair the library",
                                        lambda s: (s.repair(), self.rescan(measure=True))[0])
            case "take_in":
                # **a folder taken in is a check and then an apply** (§9, slice 92), like the repair:
                # merge compares and takes the better copies, adopt takes it in where it stands.
                folder = Path(str(body.get("folder", "")).strip()).expanduser()
                mode = str(body.get("mode") or "merge")
                if mode not in ("merge", "adopt", "take-in"):
                    raise ValueError(f"unknown way of taking a folder in: {mode}")
                # **`take-in` may be the library itself** (§9, slice 101): a collection that is already
                # where it belongs is taken in where it stands, which is the whole point of the pass.
                if why := refuse_folder(folder, self.library, itself=mode == "take-in"):
                    raise ValueError(why)
                dry = bool(body.get("dry_run"))
                if not dry and (running := self.jobs.writing()):
                    raise ValueError(f"“{running.label}” is running — wait for it, then take the folder in")
                what = "Check what taking in" if dry else "Take in"
                if mode == "take-in":
                    # the switches are this run's, not the library's state (§9, slice 100)
                    choices = {key: bool(body.get(key, True)) for key in
                               ("musicbrainz", "lyrics", "cover_beside", "cover_embedded",
                                "lyrics_embedded", "tags")}
                    # the page always sends one; a body without it leaves the answer to the
                    # setting, which is what `take_in_all` does with None (R-410, ruling 3)
                    names = ({"keep": "keep", "scheme": "scheme"}.get(str(body.get("names")))
                             if body.get("names") is not None else None)
                    keep = str(body.get("keep_originals") or "").strip()
                    kept = Path(keep).expanduser() if keep else None
                    if kept is not None and not kept.is_absolute():
                        raise ValueError("the folder for the originals must be named in full")
                    return self.jobs.submit("take_in_check" if dry else "take_in",
                                            f"{'Check what taking in' if dry else 'Take in'} "
                                            f"{folder.name} would do" if dry else f"Take in {folder.name}",
                                            lambda s: s.take_in_all(folder, names=names, keep=kept,
                                                                    dry_run=dry, **choices))
                return self.jobs.submit("take_in_check" if dry else "take_in",
                                        f"{what} {folder.name} would do ({mode})" if dry
                                        else f"{what} {folder.name} ({mode})",
                                        lambda s: s.take_in(folder, mode, dry_run=dry,
                                                            new=bool(body.get("new"))))
            case "except":
                # **the one thing kept per album** (§9, slice 100), and only the user writes it: the
                # album view turning one of the library's operations off for this album alone.
                source_id = str(body.get("id", ""))
                key, on = str(body.get("key", "")), bool(body.get("on"))
                if key not in EXCEPTIONS:
                    raise ValueError(f"no such exception: {key}")
                found = self.album(source_id)
                if not found:
                    raise ValueError("no such album")
                return self.jobs.submit("except", f"{'Except' if on else 'Stop excepting'} "
                                        f"{found[1].album} from {EXCEPTIONS[key].split(' ')[0]}",
                                        lambda s: s.set_exception(source_id, key, on), target=source_id)
            case "prune":
                source_id = str(body.get("id", ""))
                found = self.album(source_id)
                if not found:
                    raise ValueError("unknown album")
                album_dir = found[0]
                return self.jobs.submit("prune", f"Remove gone tracks from {found[1].album}", lambda s: s.prune(album_dir), target=source_id)
            case "trim_channel":
                channel = str(body.get("channel", "")).strip()
                if not channel:
                    raise ValueError("no channel")
                from .service import parse_time

                start, end = parse_time(body.get("start")), parse_time(body.get("end"))
                if start is not None and end is not None and end <= start:
                    raise ValueError("the end must come after the start")
                return self.jobs.submit("trim", f"Trim all tracks from {channel}", lambda s: s.trim_channel(channel, start, end))
            case "delete_track":
                source_id, video_id = str(body.get("id", "")), str(body.get("video_id", ""))
                found = self.album(source_id)
                if not found or not video_id:
                    raise ValueError("unknown album or track")
                track = next((t for t in found[1].tracks if t.video_id == video_id), None)
                label = f"{track.artist} - {track.title}" if track else video_id
                return self.jobs.submit("delete", f"Delete {label}", lambda s: s.delete_track(source_id, video_id), target=source_id)
            case "delete_album":
                source_id = str(body.get("id", ""))
                found = self.album(source_id)
                if not found:
                    raise ValueError("unknown album")
                return self.jobs.submit("delete", f"Delete album {found[1].album}", lambda s: s.delete_album(source_id), target=source_id)
            case "lyrics":
                source_id = str(body.get("id", ""))
                found = self.album(source_id)
                if not found:
                    raise ValueError("unknown album")
                refetch = bool(body.get("refetch"))
                verb = "Look up all lyrics of" if refetch else "Fetch lyrics for"
                return self.jobs.submit("lyrics", f"{verb} {found[1].album}", lambda s: s.fetch_lyrics(refetch=refetch, source_id=source_id), target=source_id)
            case "save_lyrics":
                source_id, video_id = str(body.get("id", "")), str(body.get("video_id", ""))
                found = self.album(source_id)
                if not found or not video_id:
                    raise ValueError("unknown album or track")
                track = next((t for t in found[1].tracks if t.video_id == video_id), None)
                if not track:
                    raise ValueError("no such track in this album")
                if track.state != "done":
                    raise ValueError(f"{track.title}: there is no file yet to put lyrics beside")
                if running := self.jobs.working_on(source_id):
                    # the pass would retag from the file this save is about to write
                    raise ValueError(f"“{running.label}” is working on this album — wait for it, then save again")
                text = str(body.get("text", ""))
                timed_by = str(body.get("timed_by", ""))[:120]
                words_by = str(body.get("words_by", ""))[:120]
                # the two methods' own figures, if this alignment was cross-checked (§9, slice 44): only
                # the handful that are evidence, only as short strings, and only from our own keys
                sent = body.get("checked") if isinstance(body.get("checked"), dict) else {}
                checked = {k: str(v)[:40] for k, v in sent.items() if k in CHECKED_KEYS}
                what = "Clear the lyrics of" if not text.strip() else "Save your lyrics for"
                return self.jobs.submit("lyrics", f"{what} {track.title}",
                                        lambda s: s.save_lyrics(source_id, video_id, text, timed_by, words_by, checked),
                                        target=source_id)
            case "draft":
                source_id, video_id = str(body.get("id", "")), str(body.get("video_id", ""))
                found = self.album(source_id)
                if not found or not video_id:
                    raise ValueError("unknown album or track")
                track = next((t for t in found[1].tracks if t.video_id == video_id), None)
                if not track:
                    raise ValueError("no such track in this album")
                if TRANSCRIBE not in capabilities_of(self.cfg):
                    raise ValueError("no timing provider can derive words — see `timing_provider` in the config")
                if not may_send_audio(self.cfg, found[1], TRANSCRIBE):
                    raise ValueError(PRIVATE_AUDIO)
                return self.jobs.submit("draft", f"Draft the words of {track.title}",
                                        lambda s: s.draft_lyrics(source_id, video_id), target=source_id)
            case "check_lyrics":
                source_id, video_id = str(body.get("id", "")), str(body.get("video_id", ""))
                found = self.album(source_id)
                if not found or not video_id:
                    raise ValueError("unknown album or track")
                track = next((t for t in found[1].tracks if t.video_id == video_id), None)
                if not track:
                    raise ValueError("no such track in this album")
                if ALIGN not in capabilities_of(self.cfg):
                    raise ValueError("checking a near miss needs a timing provider that can align — "
                                     "see `timing_align_provider` in the config")
                if not may_send_audio(self.cfg, found[1], ALIGN):
                    raise ValueError(PRIVATE_AUDIO)
                return self.jobs.submit("lyrics", f"Check lrclib's near miss for {track.title}",
                                        lambda s: s.check_near_lyrics(source_id, video_id), target=source_id)
            case "take_plain_lyrics":
                source_id, video_id = str(body.get("id", "")), str(body.get("video_id", ""))
                found = self.album(source_id)
                if not found or not video_id or not str(body.get("entry", "")).isdigit():
                    raise ValueError("unknown album, track or entry")
                track = next((t for t in found[1].tracks if t.video_id == video_id), None)
                if not track:
                    raise ValueError("no such track in this album")
                entry = int(body["entry"])
                return self.jobs.submit("lyrics", f"Take lrclib's words for {track.title}",
                                        lambda s: s.take_plain_lyrics(source_id, video_id, entry),
                                        target=source_id)
            case "restore":
                entry = str(body.get("entry", ""))
                if not entry:
                    raise ValueError("which entry?")
                return self.jobs.submit("library", f"Restore {entry} from the recycle bin",
                                        lambda s: s.restore(entry))
            case "empty_recycle":
                days = body.get("older_than")
                return self.jobs.submit("library", "Empty the recycle bin",
                                        lambda s: s.empty_recycle(None if days is None else float(days)))
            case "publish_lyrics":
                source_id, video_id = str(body.get("id", "")), str(body.get("video_id", ""))
                found = self.album(source_id)
                if not found or not video_id:
                    raise ValueError("unknown album or track")
                track = next((t for t in found[1].tracks if t.video_id == video_id), None)
                if not track:
                    raise ValueError("no such track in this album")
                # the same gate the page uses, asked again here: a button is a suggestion, and this
                # one cannot be taken back (§9, slice 42)
                text = (read_sidecar(found[0], track) or "").strip()
                if why := publishable(track, text, self.reader.their_words(track)):
                    raise ValueError(f"these words cannot be published: {why}")
                return self.jobs.submit("publish_lyrics", f"Publish the words of {track.title} to lrclib",
                                        lambda s: s.publish_lyrics(source_id, video_id), target=source_id)
            case "align":
                source_id, video_id = str(body.get("id", "")), str(body.get("video_id", ""))
                found = self.album(source_id)
                if not found or not video_id:
                    raise ValueError("unknown album or track")
                track = next((t for t in found[1].tracks if t.video_id == video_id), None)
                if not track:
                    raise ValueError("no such track in this album")
                # two ways to place the same words (§9, slice 83), each asked of its own slot: the
                # aligner forces them onto the clock, listening hears the song first and matches
                method = str(body.get("method") or ALIGN)
                if method not in (ALIGN, LISTEN):
                    raise ValueError(f"unknown way of placing words: {method}")
                if method not in capabilities_of(self.cfg):
                    raise ValueError("no timing provider can align words — see `timing_provider` in the config"
                                     if method == ALIGN else
                                     "no timing provider can listen for the words — that needs a provider "
                                     "that transcribes with word times, in `timing_draft_provider`")
                if not may_send_audio(self.cfg, found[1], method):
                    raise ValueError(PRIVATE_AUDIO)
                text = str(body.get("text", ""))
                # the read lane: this writes nothing, so it may run beside a download, and it can
                # take minutes on a machine without a GPU (§9, slice 36)
                label = ("Align the words of" if method == ALIGN else "Listen for the words of")
                return self.jobs.submit("align", f"{label} {track.title}",
                                        lambda s: s.align_lyrics(source_id, video_id, text, method),
                                        target=source_id)
            case "lyrics_track":
                source_id, video_id = str(body.get("id", "")), str(body.get("video_id", ""))
                found = self.album(source_id)
                if not found or not video_id:
                    raise ValueError("unknown album or track")
                track = next((t for t in found[1].tracks if t.video_id == video_id), None)
                if not track:
                    raise ValueError("no such track in this album")
                if track.state != "done":
                    raise ValueError(f"{track.title}: there is no file yet to match lyrics against")
                if track.provenance.get("lyrics") == "user":
                    raise ValueError(f"{track.title}: these lyrics are yours — delete them first")
                reject = bool(body.get("reject"))
                if reject and not track.lyrics_id:
                    raise ValueError(f"{track.title}: there is no lrclib match to reject")
                if running := self.jobs.working_on(source_id):
                    raise ValueError(f"“{running.label}” is working on this album — wait for it, then try again")
                what = "Reject the lyrics of" if reject else "Look up the lyrics of"
                return self.jobs.submit("lyrics", f"{what} {track.title}",
                                        lambda s: s.lookup_track(source_id, video_id, reject=reject), target=source_id)
            case "cover":
                # **a cover the user chooses** (§9, slice 156; R-519 item 2). Either the bytes of a
                # file they picked, base64 in the JSON body, or an address — and an address is
                # fetched *here*, because a page cannot read the bytes of a picture on another site
                # and because what gets written must be checked before it is written.
                source_id = str(body.get("id", ""))
                if not self.album(source_id):
                    raise ValueError("unknown album")
                url = str(body.get("url") or "").strip()
                raw = body.get("data")
                if url and not url.startswith(("http://", "https://")):
                    raise ValueError("a cover address has to be http:// or https://")
                if not url and not isinstance(raw, str):
                    raise ValueError("no picture: give a file or an address")
                data = b""
                if isinstance(raw, str):
                    try:
                        data = base64.b64decode(raw, validate=True)
                    except (ValueError, binascii.Error) as e:
                        raise ValueError("that file did not arrive in one piece") from e
                    if not data:
                        raise ValueError("that file is empty")
                    if len(data) > MAX_COVER_BODY:
                        raise ValueError("that picture is too large")
                what = self.describe(source_id)
                return self.jobs.submit("cover", f"Set the cover of {what}",
                                        lambda s: s.set_cover(source_id, data or _fetched_cover(s, url), url),
                                        target=source_id)
            case "edit":
                source_id = str(body.get("id", ""))
                if not self.album(source_id):
                    raise ValueError("unknown album")
                edits = body.get("edits") or {}
                return self.jobs.submit("edit", f"Save {self.describe(source_id)}", lambda s: s.apply_edits(source_id, edits), target=source_id)
        raise ValueError(f"unknown action {action!r}")

    def _arrival(self, body: dict[str, Any]) -> tuple[str, str, Callable[[Service], Any]]:
        """What the watcher asked for, as a job — or a refusal naming what is wrong.

        Nothing here trusts a path. The root must be one the config names; the arrival is resolved
        beneath it and must still be beneath it afterwards, which is what stops `..` and a symlink
        that points elsewhere.
        """
        name = str(body.get("watch") or "").strip()
        root = self.cfg.watch(name)
        if root is None:
            raise ValueError(f"no watched folder called {name!r}")
        arrival = str(body.get("album") or "").strip()
        if not arrival or Path(arrival).is_absolute():
            raise ValueError("an arrival is named by its path inside the watched folder")
        folder = (root.folder / arrival).resolve()
        if folder != root.folder.resolve() and root.folder.resolve() not in folder.parents:
            raise ValueError("that is not inside the watched folder")
        if not folder.is_dir():
            raise ValueError("there is no such folder in there any more")
        # the label names the watch and the album, never the path: this line reaches the page
        label = f"{name}: {arrival}"
        if root.shape == "library":
            # the folder **is** the album, so a file added to one that is already ours is a new
            # track of it — the opposite of intake, and the reason the two shapes exist (R-200).
            # `reread` and not `fetch`: a fetch would copy, and copying inside the library is how
            # a dropped file ends up written over an existing one (§9, slice 59).
            return "watch", f"Read {label}", lambda s: s.reread(folder)
        return "watch", f"Take in {label}", lambda s: s.fetch(str(folder))

    def describe(self, url: str) -> str:
        """A readable job label: the album's name if the URL is one we have, else a short URL."""
        for _, plan in iter_plans(self.library) if self.library.exists() else []:
            if plan.source_url == url or plan.source_id in url:
                return f"{plan.albumartist} — {plan.album}"
        return url.replace("https://", "").replace("www.", "")[:60]

    # server

    def touch(self) -> None:
        self.last_request = time.monotonic()

    def idle_for(self) -> float:
        """Seconds without requests and without queued/running jobs (0 while busy)."""
        return 0.0 if self.jobs.busy() else time.monotonic() - self.last_request

    def allowed_host(self, host_header: str | None) -> bool:
        if self.host in ("0.0.0.0", "::"):
            return True  # explicitly opened to the network
        name = (host_header or "").rsplit(":", 1)[0].strip("[]")
        return name in {"localhost", "127.0.0.1", "::1", self.host}

    def make_server(self, sock: socket.socket | None = None) -> ThreadingHTTPServer:
        """A server on (host, port), or on an already listening socket (systemd socket activation)."""
        app = self

        class Handler(_Handler):
            pass

        Handler.app = app
        if sock is None:
            return _Server((self.host, self.port), Handler)
        server = _Server(sock.getsockname()[:2], Handler, bind_and_activate=False)
        server.socket.close()
        server.socket = sock
        server.server_address = sock.getsockname()[:2]
        return server


class _Server(ThreadingHTTPServer):
    """The same server, without a traceback for a client that hung up (§9, slice 90)."""

    def handle_error(self, request: Any, client_address: Any) -> None:
        kind = sys.exc_info()[0]
        if kind is not None and issubclass(kind, (ConnectionResetError, BrokenPipeError, TimeoutError)):
            log.debug("the client went away: %s", kind.__name__)
            return
        log.exception("error while answering %s", client_address)


def _asset(name: str) -> bytes:
    return resources.files("noaap").joinpath("webui", name).read_bytes()


# what a served module imports: the URL is versioned when the module is served, and the importer's
# own hash covers it too — otherwise a new logic.mjs would sit behind a cached app.js that never
# asks for it again
IMPORTS = {"app.js": ("logic.mjs",)}


def _asset_hash(name: str) -> str:
    """Changes with the file and with anything it imports, so a new version is never served
    from a browser cache."""
    data = _asset(name) + b"".join(_asset(dep) for dep in IMPORTS.get(name, ()))
    return hashlib.sha1(data).hexdigest()[:10]


def _append(job: Job, line: str) -> None:
    job.log.append(line)
    if len(job.log) > MAX_LOG:
        del job.log[: len(job.log) - MAX_LOG]


def _ref(r) -> dict[str, Any]:
    return {"url": r.url, "id": r.source_id, "title": r.title, "tab": r.tab, "artist": r.artist, "count": r.count, "thumbnail": r.thumbnail}


def _groups(groups) -> list[dict[str, Any]]:
    return [{"label": label, "refs": [_ref(r) for r in refs]} for label, refs in groups]


def _search_result(result) -> dict[str, Any]:
    return {"groups": _groups(result.groups), "channel": result.channel_url, "missing": result.missing}


class _Handler(BaseHTTPRequestHandler):
    # **HTTP/1.1, because a player seeks by asking again** (§9, slice 77). The default is 1.0, which
    # closes the connection after every response, so each range request a seek makes pays for a new
    # one. Every response here carries a `Content-Length`, which is what 1.1 requires of us.
    protocol_version = "HTTP/1.1"
    app: App
    server_version = "noaap"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("%s " + fmt, self.address_string(), *args)

    #: a request that takes longer than this is worth a line of its own, with where the time went
    SLOW = 0.5

    def end_headers(self) -> None:
        """Stamp when the first byte of the answer went out, for the slow-request line."""
        self._headers_at = time.monotonic()
        super().end_headers()

    def handle_one_request(self) -> None:
        """One request, timed — and **a client that left is not an error** (§9, slice 90).

        A media element opens, seeks and abandons connections constantly; every one of those used to
        leave a `ConnectionResetError` traceback in the journal. It is one line at debug level now, and
        a request slower than `SLOW` says so with the time it took to answer at all.
        """
        # **the clock starts when a request has arrived, not when we start waiting for one** (P116e,
        # R-560). On a kept-alive connection this method first sits in `readline` until the browser
        # sends its next request — the page's 8 s idle poll made every `/api/state` "slow: 8.03 s",
        # and a connection the browser closed after minutes logged the *previous* request's path
        # with "0.00 s to the first byte". `parse_request` stamps the start; no stamp, no request.
        self._started = None
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError, TimeoutError) as e:
            log.debug("the client went away: %s", type(e).__name__)
            self.close_connection = True
            return
        if self._started is None:
            return
        took = time.monotonic() - self._started
        if took >= self.SLOW:
            log.warning("slow request: %s took %.2f s (%.2f s to the first byte)",
                        (self.path or "?").split("?")[0], took, max(0.0, self._headers_at - self._started))

    def parse_request(self) -> bool:
        self._started = self._headers_at = time.monotonic()
        return super().parse_request()

    # routing

    def do_GET(self) -> None:
        self.app.touch()
        if not self.app.allowed_host(self.headers.get("Host")):
            return self._error(HTTPStatus.FORBIDDEN, "host not allowed")
        url = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path in STATIC:
            name, ctype = STATIC[url.path]
            body = _asset(name)
            if url.path == "/":  # never cached itself; points at content-hashed assets
                for asset in ("app.js", "style.css"):
                    body = body.replace(f'"/{asset}"'.encode(), f'"/{asset}?v={_asset_hash(asset)}"'.encode())
            for dep in IMPORTS.get(name, ()):  # the same for what that asset imports
                body = body.replace(f'"./{dep}"'.encode(), f'"./{dep}?v={_asset_hash(dep)}"'.encode())
            return self._send(HTTPStatus.OK, body, ctype, cache=url.path != "/")
        match url.path:
            case "/api/state":
                return self._json(self.app.state())
            case "/api/tracks":
                return self._json(self.app.track_index())
            case "/api/recycle":
                # what noaap moved aside instead of deleting (§9, slice 49). No filesystem path
                # leaves this endpoint: an entry is addressed by its id and nothing else.
                return self._json({"entries": self.app.recycle()})
            case "/api/mbseed":
                # the fields for MusicBrainz's own release editor (§9, slice 43). Nothing is sent from
                # here: the page builds a form with these and the person submits it themselves.
                found = self.app.album(q.get("id", ""))
                if not found:
                    return self._error(HTTPStatus.NOT_FOUND, "no such album")
                plan = found[1]
                if why := seedable(plan):
                    return self._error(HTTPStatus.BAD_REQUEST, f"this album is not one to offer: {why}")
                return self._json({"url": seed_url(), "fields": seed_release(plan)})
            case "/api/album":
                plan = self.app.album_view(q.get("id", ""))
                return self._json(self.app.with_links(plan)) if plan else self._error(HTTPStatus.NOT_FOUND, "no such album")
            case "/api/cover":
                # `thumb=1` is the grid's: a card is 168px and a cover averages 65 KiB (§9, slice 140).
                # The panel and the player ask without it, because they show the picture large.
                cover = self.app.cover(q.get("id", ""), thumb=q.get("thumb") == "1")
                # **a cover is a file on disk and may be cached** (§9, slice 90): `no-store` made a
                # refresh fetch every one of them again — 52 MB on the user's library, with the first
                # press of play waiting behind it.
                # **an album without a cover is not an error** (P116, R-549): a 404 for it put two
                # console errors in every view of such an album. 204 says "nothing to show"; the
                # page's `onerror` still hides the picture. An unknown album is still a 404.
                if cover:
                    return self._send(HTTPStatus.OK, cover[0], cover[1], modified=cover[2])
                if self.app.album(q.get("id", "")):
                    return self._nothing()
                return self._error(HTTPStatus.NOT_FOUND, "no such album")
            case "/api/thumb":
                thumb = self.app.thumbnail(q.get("u", ""))
                return self._send(HTTPStatus.OK, thumb[0], thumb[1], cache=True) if thumb else self._error(HTTPStatus.NOT_FOUND, "no thumbnail")
            case "/api/lyrics":
                found = self.app.lyrics(q.get("id", ""), q.get("v", ""))
                return self._json(found) if found else self._error(HTTPStatus.NOT_FOUND, "no such track")
            case "/api/audio":
                path = self.app.audio_path(q.get("id", ""), q.get("v", ""), original=q.get("o") == "1")
                # what the file is, not what most of them happen to be: every track was served as
                # `audio/ogg`, FLAC, mp3 and m4a included (§9, slice 77)
                return self._file(path, audio_type(path)) if path else self._error(HTTPStatus.NOT_FOUND, "no such track")
            case "/api/job":
                job = self.app.jobs.get(int(q.get("id", "0") or 0)) if q.get("id", "").isdigit() else None
                return self._json(job.summary(full=True)) if job else self._error(HTTPStatus.NOT_FOUND, "no such job")
        return self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_HEAD(self) -> None:
        """The same answer as a GET, without the body — a 501 is not an answer (§9, slice 77)."""
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    def _read_body(self) -> bytes:
        """Everything the client sent with this request, at most `MAX_BODY` + 1 bytes of it.

        Read in one place and read *early*: on a reused connection an unread body becomes the next
        request line. One byte over the limit is enough to answer "too large" without holding a
        larger one in memory.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return b""
        cap = MAX_COVER_BODY if urlsplit(self.path).path == "/api/cover" else MAX_BODY
        return self.rfile.read(min(max(length, 0), cap + 1)) if length > 0 else b""

    def do_POST(self) -> None:
        self.app.touch()
        # **the body is read before anything else can refuse it** (§9, slice 77). On HTTP/1.1 the
        # connection is reused, so a body left unread is still on the socket when the next request
        # line is parsed — the server then reports `Unsupported method ('{}POST')`. Under HTTP/1.0
        # the close hid it. Every path that answers a POST has consumed it by the time it answers.
        self._body = self._read_body()
        if not self.app.allowed_host(self.headers.get("Host")):
            return self._error(HTTPStatus.FORBIDDEN, "host not allowed")
        if self.headers.get(WRITE_HEADER) != "1" \
                or not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._error(HTTPStatus.FORBIDDEN, f"missing {WRITE_HEADER} header or JSON content type")
        url = urlsplit(self.path)
        if not url.path.startswith("/api/"):
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        cap = MAX_COVER_BODY if url.path == "/api/cover" else MAX_BODY
        if len(self._body or b"") > cap:
            return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too large")
        try:
            body = json.loads(self._body or b"{}")
            body = body if isinstance(body, dict) else {}
            if url.path == "/api/details":
                refs = body.get("refs") or []
                if not isinstance(refs, list) or len(refs) > 200:
                    raise ValueError("refs must be a list of at most 200 entries")
                return self._json(self.app.details.want([r for r in refs if isinstance(r, dict)]))
            if url.path == "/api/cancel":
                job = self.app.jobs.cancel(int(body.get("id", 0)))
                return self._json(job.summary()) if job else self._error(HTTPStatus.NOT_FOUND, "no such job")
            if url.path == "/api/settings":
                try:
                    return self._json(self.app.save_settings(body))
                except OSError as e:
                    return self._error(HTTPStatus.BAD_REQUEST, f"cannot use that folder: {e.strerror or e}")
            job = self.app.submit(url.path.removeprefix("/api/"), body)
        except (ValueError, json.JSONDecodeError) as e:
            return self._error(HTTPStatus.BAD_REQUEST, str(e))
        return self._json({"job": job.summary()}, HTTPStatus.ACCEPTED)

    def do_OPTIONS(self) -> None:  # never answer CORS preflights: other origins must not write
        self._error(HTTPStatus.FORBIDDEN, "no cross-origin access")

    # responses

    def _file(self, path: Path, ctype: str) -> None:
        """Stream a file, honouring a single `Range: bytes=a-b` so players can seek."""
        stat = path.stat()
        size = stat.st_size
        if not self.headers.get("Range") and self._unchanged_since(stat.st_mtime):
            return self._not_modified(stat.st_mtime)
        start, end = 0, size - 1
        rng = self.headers.get("Range", "")
        partial = rng.startswith("bytes=") and "," not in rng
        if partial:
            a, _, b = rng.removeprefix("bytes=").partition("-")
            try:
                if a:
                    start, end = int(a), min(int(b), size - 1) if b else size - 1
                else:  # suffix range: the last N bytes
                    start = max(size - int(b), 0)
            except ValueError:
                partial = False
            if partial and (start > end or start >= size):
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                # **every answer says how long it is** (§9, slice 77): on a reused connection a
                # client waits for a body it was never told the length of, and this one has none
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-cache")
        # a range request is never answered with a 304: the client is asking for a piece it does not have
        # what a player needs to revalidate instead of fetching the whole thing again
        self.send_header("Last-Modified", self.date_time_string(int(path.stat().st_mtime)))
        self.end_headers()
        if getattr(self, "_head_only", False):
            return
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            try:
                while remaining > 0 and (chunk := f.read(min(remaining, 1 << 16))):
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the player skipped or seeked away

    def _unchanged_since(self, modified: float) -> bool:
        """Whether the client already has this version (§9, slice 90).

        `Last-Modified` was sent and `If-Modified-Since` was **never read**, so a browser told to
        revalidate got the whole file back every time: measured, one refresh of a 250-album library
        re-downloaded **52 MB** of covers, and the first press of play waited behind it.
        """
        since = self.headers.get("If-Modified-Since")
        if not since:
            return False
        try:
            asked = email.utils.parsedate_to_datetime(since)
        except (TypeError, ValueError):
            return False
        if asked.tzinfo is None:
            asked = asked.replace(tzinfo=UTC)
        # the header has a second's resolution, so compare at that resolution
        return int(modified) <= int(asked.timestamp())

    def _not_modified(self, modified: float) -> None:
        self.send_response(HTTPStatus.NOT_MODIFIED)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Last-Modified", self.date_time_string(int(modified)))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status)

    def _nothing(self) -> None:
        """204: the request was fine and there is nothing to send."""
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _send(self, status: HTTPStatus, body: bytes, ctype: str, cache: bool = False,
              modified: float | None = None) -> None:
        # a HEAD gets the headers a GET would send — **the length included** — and no body at all
        head_only = getattr(self, "_head_only", False)
        if modified is not None and self._unchanged_since(modified):
            return self._not_modified(modified)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache" if cache or modified is not None else "no-store")
        if modified is not None:
            self.send_header("Last-Modified", self.date_time_string(int(modified)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; script-src 'self'")
        self.end_headers()
        if not head_only:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True   # the page navigated away mid-answer


def systemd_socket() -> socket.socket | None:
    """The listening socket systemd passed us (sd_listen_fds protocol), if any."""
    if os.environ.get("LISTEN_PID") != str(os.getpid()) or int(os.environ.get("LISTEN_FDS", "0")) < 1:
        return None
    for name in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
        os.environ.pop(name, None)  # not for our children (the token server)
    return socket.socket(fileno=3)


def serve(cfg: Config, library: Path, host: str = "127.0.0.1", port: int = 8765, idle_exit: float = 0) -> None:
    """Run the web UI. With idle_exit > 0 the server stops after that many seconds without
    requests or jobs — meant for socket activation, where the next request starts it again."""
    sock = systemd_socket()
    app = App(cfg, library, host, port)
    if sock is not None:
        app.host, app.port = sock.getsockname()[:2]
    server = app.make_server(sock)
    shown = "localhost" if app.host in ("127.0.0.1", "::1") else app.host
    print(f"noaap: http://{shown}:{app.port}/  (library {library})" + (" [socket-activated]" if sock else " — Ctrl+C to stop"), flush=True)
    if app.host in ("0.0.0.0", "::"):
        print("warning: reachable from your network without a login — anyone there can start downloads")
    if idle_exit > 0:

        def watch() -> None:
            while True:
                time.sleep(min(30.0, idle_exit / 4))
                if app.idle_for() > idle_exit:
                    print(f"idle for {idle_exit:.0f}s, stopping", flush=True)
                    server.shutdown()
                    return

        threading.Thread(target=watch, name="noaap-idle", daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
