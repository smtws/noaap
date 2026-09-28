"""Timing providers that are somebody else's computer (DESIGN.md §9, slice 37).

Two of them, because the two capabilities have two different markets:

- **ElevenLabs** aligns *and* transcribes. It is the only mainstream vendor that sells forced
  alignment of text you supply, which is the thing ytalbum wants most often.
- **Deepgram** transcribes only, and says so: `capabilities()` never claims `align`, so the page
  never offers an alignment it cannot do.

Both send **the track's audio** — the file on disk, which is the cut one, because that is the file
the stamps belong to. That is the whole difference from `local` and `http`, it is the user's
decision rather than the code's, and the page says so before the first request of a session.

Nothing here retries: a retry on a paid endpoint is a second invoice for the same answer. A
rate-limited or refused request comes back as `TimingUnavailable` carrying the vendor's own message.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .timing import (
    ALIGN,
    TRANSCRIBE,
    Timed,
    TimedLine,
    TimingUnavailable,
    line_starts,
    lines_from_words,
)

log = logging.getLogger(__name__)

# generous: a long track has to be uploaded before anything is transcribed, and the vendors queue
UPLOAD_TIMEOUT = 600.0
TYPES = {".opus": "audio/ogg", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".mp4": "audio/mp4",
         ".mp3": "audio/mpeg", ".flac": "audio/flac", ".wav": "audio/wav"}


class CloudTiming:
    """What the two have in common: a key, a timeout, and manners about failure."""

    name = "cloud"
    vendor = "a vendor"

    def __init__(self, key: str, timeout: float = UPLOAD_TIMEOUT) -> None:
        self.key = (key or "").strip()
        self.timeout = timeout
        self.log: Callable[[str], None] = lambda _: None
        # `YTALBUM_TIMING_BASE_ELEVENLABS=http://127.0.0.1:9000/v1` sends the requests somewhere
        # else: a gateway, a proxy, or — which is what it exists for — a server that speaks the
        # vendor's documented shapes, so this can be exercised end to end without spending a cent.
        if base := os.environ.get(f"YTALBUM_TIMING_BASE_{self.name.upper()}"):
            self._rebase(base.rstrip("/"))

    def _rebase(self, base: str) -> None:
        """Point this client at another host. Overridden where the URL is not called BASE."""
        self.BASE = base  # type: ignore[attr-defined]

    def _guard(self, check: Callable[[], None] | None) -> None:
        if not self.key:
            raise TimingUnavailable(
                f"no API key for {self.vendor}. Put one in the settings panel or in the config file "
                f"as `timing_{self.name}_key` — it is never shown again and never leaves this machine.")
        if check:
            check()

    def _post(self, url: str, *, headers: dict[str, str], **kwargs: Any) -> Any:
        import httpx

        try:
            r = httpx.post(url, headers=headers, timeout=self.timeout, **kwargs)
        except Exception as e:
            raise TimingUnavailable(f"{self.vendor} could not be reached: {e}") from e
        if r.status_code == 429:
            raise TimingUnavailable(f"{self.vendor} is rate-limiting this key: {_message(r)}")
        if r.status_code in (401, 403):
            raise TimingUnavailable(f"{self.vendor} refused the API key: {_message(r)}")
        if r.status_code >= 400:
            # no retry: the request may already have been billed, and a second one certainly is
            raise TimingUnavailable(f"{self.vendor} refused the request ({r.status_code}): {_message(r)}")
        try:
            return r.json()
        except ValueError as e:
            raise TimingUnavailable(f"{self.vendor} answered with something that is not JSON") from e

    def align(self, audio: Path, lines: list[str], *, language: str | None = None,
              check: Callable[[], None] | None = None) -> Timed:
        raise TimingUnavailable(f"{self.vendor} does not align words you give it")

    def transcribe(self, audio: Path, *, language: str | None = None,
                   check: Callable[[], None] | None = None) -> Timed:
        raise TimingUnavailable(f"{self.vendor} does not transcribe")


def _message(response: Any) -> str:
    """The vendor's own words about what went wrong, never ours and never the key."""
    try:
        body = response.json()
    except Exception:
        return (response.text or "")[:200].strip() or "no message"
    for key in ("detail", "message", "error", "err_msg"):
        if value := body.get(key):
            if isinstance(value, dict):
                value = value.get("message") or value.get("status") or str(value)
            return str(value)[:200]
    return str(body)[:200]


def _media_type(audio: Path) -> str:
    return TYPES.get(audio.suffix.lower(), "application/octet-stream")


class ElevenLabsTiming(CloudTiming):
    """Forced alignment and transcription. https://elevenlabs.io/docs — `xi-api-key`."""

    name = "elevenlabs"
    vendor = "ElevenLabs"
    BASE = "https://api.elevenlabs.io/v1"
    MODEL = "scribe_v2"

    def capabilities(self) -> frozenset[str]:
        return frozenset({ALIGN, TRANSCRIBE}) if self.key else frozenset()

    def align(self, audio: Path, lines: list[str], *, language: str | None = None,
              check: Callable[[], None] | None = None) -> Timed:
        self._guard(check)
        owners, words = [], []
        for i, line in enumerate(lines):
            for word in line.split():
                owners.append(i)
                words.append(word)
        if not words:
            raise TimingUnavailable("there are no words to place")
        self.log(f"sending {audio.name} to ElevenLabs for alignment")
        with audio.open("rb") as fh:
            body = self._post(f"{self.BASE}/forced-alignment",
                              headers={"xi-api-key": self.key},
                              data={"text": " ".join(words)},
                              files={"file": (audio.name, fh, _media_type(audio))})
        if check:
            check()
        got = body.get("words") or []
        starts = line_starts(owners, words, got)
        placed = [TimedLine(text=line, start=starts.get(i)) for i, line in enumerate(lines)]
        return Timed(lines=placed, provider=self.name, model="forced-alignment",
                     version=str(body.get("version") or ""),
                     parameters={"words": str(len(got)), "loss": str(body.get("loss", ""))})

    def transcribe(self, audio: Path, *, language: str | None = None,
                   check: Callable[[], None] | None = None) -> Timed:
        self._guard(check)
        data = {"model_id": self.MODEL, "timestamps_granularity": "word"}
        if language:
            data["language_code"] = language
        self.log(f"sending {audio.name} to ElevenLabs to transcribe")
        with audio.open("rb") as fh:
            body = self._post(f"{self.BASE}/speech-to-text", headers={"xi-api-key": self.key},
                              data=data, files={"file": (audio.name, fh, _media_type(audio))})
        if check:
            check()
        # `words` carries spacing and audio events beside the words themselves; only words are lyrics
        words = [w for w in (body.get("words") or []) if (w.get("type") or "word") == "word"]
        if not words and body.get("text"):
            # documented as always present, but a transcript with no word list is still a draft
            return Timed(lines=[TimedLine(text=str(body["text"]))], provider=self.name,
                         model=self.MODEL, parameters={"words": "0"})
        return Timed(lines=lines_from_words(words), provider=self.name, model=self.MODEL,
                     parameters={"language": str(body.get("language_code") or language or ""),
                                 "words": str(len(words))})


class DeepgramTiming(CloudTiming):
    """Transcription only. https://developers.deepgram.com — `Authorization: Token …`."""

    name = "deepgram"
    vendor = "Deepgram"
    URL = "https://api.deepgram.com/v1/listen"
    MODEL = "nova-3"

    def _rebase(self, base: str) -> None:
        self.URL = f"{base}/listen"

    def capabilities(self) -> frozenset[str]:
        # never ALIGN: Deepgram transcribes, and a provider that overstates itself is worse than one
        # that cannot do the job at all
        return frozenset({TRANSCRIBE}) if self.key else frozenset()

    def transcribe(self, audio: Path, *, language: str | None = None,
                   check: Callable[[], None] | None = None) -> Timed:
        self._guard(check)
        params = {"model": self.MODEL, "smart_format": "true", "punctuate": "true", "paragraphs": "true"}
        params["language"] = language or "multi"
        self.log(f"sending {audio.name} to Deepgram to transcribe")
        body = self._post(f"{self.URL}", headers={"Authorization": f"Token {self.key}",
                                                  "Content-Type": _media_type(audio)},
                          params=params, content=audio.read_bytes())
        if check:
            check()
        alternative = (((body.get("results") or {}).get("channels") or [{}])[0].get("alternatives") or [{}])[0]
        words = alternative.get("words") or []
        # **Lines come from the word timings, never from the sentences** (§9, slice 45). Their sentences are
        # punctuation, and a song's lines are pauses: the user's first real draft came back as eleven
        # lines for a 3:25 song, one of them a whole verse, because a full stop was the only break
        # the vendor offered. The sentences are still asked for — they are the fallback when a model
        # returns no word timings at all — but they can never hold a line together across a pause.
        sentences = [s for p in ((alternative.get("paragraphs") or {}).get("paragraphs") or [])
                     for s in (p.get("sentences") or [])]
        if words:
            lines = lines_from_words(words)
        else:
            lines = [TimedLine(text=str(s.get("text", "")).strip(),
                               start=None if s.get("start") is None else float(s["start"]),
                               end=None if s.get("end") is None else float(s["end"]))
                     for s in sentences if str(s.get("text", "")).strip()]
        return Timed(lines=lines, provider=self.name, model=self.MODEL,
                     parameters={"words": str(len(words)),
                                 "grouped_by": "words" if words else "sentences",
                                 "language": str(language or "multi")})


def cloud_provider(kind: str, cfg: Any):
    key = getattr(cfg, f"timing_{kind}_key", "") or ""
    if kind == "elevenlabs":
        return ElevenLabsTiming(key)
    if kind == "deepgram":
        return DeepgramTiming(key)
    raise TimingUnavailable(f"unknown timing provider {kind!r}")


__all__ = ["DeepgramTiming", "ElevenLabsTiming", "cloud_provider"]
