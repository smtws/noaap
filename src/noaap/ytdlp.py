"""The yt-dlp client, with no idea which site it is talking to (DESIGN §9, slice 57).

Two providers ride yt-dlp now, and everything they have in common is here: the option dict, the
logger, and the first cut at an error message. **Nothing in this module may know a provider's
shapes** — no host, no id pattern, no extractor name, no convention. A guard in the suite says so,
because the whole value of the Source boundary is that this file cannot quietly become YouTube's
again.

Cookies are an argument, not a lookup. A provider that reads the config for its own cookies and
passes them here cannot accidentally hand another provider's credentials to a site they were never
meant for — which is what reading `cfg.cookies_file` in a shared helper would do the first time a
second provider called it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class Logger:
    """yt-dlp's own chatter, routed into ours and mostly kept at debug."""

    def debug(self, msg: str) -> None:
        log.debug(msg.removeprefix("[debug] "))

    def info(self, msg: str) -> None:
        log.debug(msg)

    def warning(self, msg: str) -> None:
        # yt-dlp warns about things it then recovers from itself ("re-fetching using API");
        # real failures arrive as DownloadError. Visible with -v.
        log.info(msg)

    def error(self, msg: str) -> None:
        log.debug(msg)  # raised as DownloadError too; the caller decides what it means


def params(*, cookies_file: Path | str | None = None, cookies_from_browser: str | None = None,
           js_runtime: tuple[str, str | None] | None = None,
           progress: Callable[..., None] | None = None, **extra: Any) -> dict[str, Any]:
    """The options every provider wants, and only those.

    The timeouts and retries are the same for everyone; `sleep_interval_requests` is politeness,
    not a workaround — a burst of requests is rude to any host and several of them answer it by
    refusing for a while.
    """
    out: dict[str, Any] = {
        "quiet": True,
        "noprogress": True,
        "logger": Logger(),
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 2,
        "sleep_interval_requests": 0.5,
    }
    if progress is not None:
        out["progress_hooks"] = [progress]
    if cookies_file:
        out["cookiefile"] = str(cookies_file)
    elif cookies_from_browser:
        browser, _, profile = cookies_from_browser.partition(":")
        out["cookiesfrombrowser"] = (browser, profile or None, None, None)
    if js_runtime:
        name, path = js_runtime
        out["js_runtimes"] = {name: {"path": path} if path else {}}
    out.update(extra)
    return out


def trim(e: Exception | str) -> str:
    """yt-dlp's message without its wrapping: no `ERROR:`, no `[extractor] id:` prefix.

    Deliberately **not** shortened to one sentence: a provider reads this whole string for the
    phrases it knows — an age gate, a bot check, a DRM notice — and shortens afterwards.
    """
    msg = str(e).removeprefix("ERROR: ")
    if msg.startswith("[") and ": " in msg:
        msg = msg.split(": ", 1)[1]
    return msg


def one_sentence(msg: str) -> str:
    """The first sentence of it, for a message a person reads beside a track."""
    return msg.split(". ")[0].strip().rstrip(".")


__all__ = ["Logger", "one_sentence", "params", "trim"]
