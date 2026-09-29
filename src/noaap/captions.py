"""WebVTT as words beside a track (DESIGN §9, slice 74).

Captions are not lyrics and this module does not pretend they are. They are the text of what is being
said, cut into reading-width fragments by whoever made them: a cue is a start stamp, an end stamp and
one or two lines broken where a caption box ends — not where a sentence does. What noaap keeps is the
start stamp and the words, because `.lrc` holds one stamp per line and inventing the rest would be
inventing.

**Nothing here knows which site the file came from.** A provider hands over bytes it has checked; this
turns them into lines. That is the whole boundary: WebVTT is a format, not a source.
"""

from __future__ import annotations

import html
import re

# `00:00:07.120 --> 00:00:11.040 line:0%,end position:50%` — the settings after the stamps are the
# caption box's, not the words', and are dropped with everything else that is presentation.
_CUE = re.compile(r"^(?P<start>(?:\d{1,3}:)?\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*"
                  r"(?P<end>(?:\d{1,3}:)?\d{1,2}:\d{2}[.,]\d{1,3})(?P<settings>\s+\S.*)?$")
# `<v Narrator>`, `<i>`, `<c.yellow>`, `<00:00:07.120>` — all of it is how it should look, not what is said
_TAGS = re.compile(r"</?[^>]{0,120}>")
_BLOCK = re.compile(r"^(NOTE|STYLE|REGION)\b")
_HEADER = re.compile(r"^﻿?WEBVTT(\s|$)")


def is_webvtt(data: bytes | str) -> bool:
    """Whether this is a WebVTT file at all — by its own first line, which the format requires."""
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else (data or "")
    return bool(_HEADER.match(text.lstrip("\r\n \t")))


def seconds(stamp: str) -> float:
    """`01:02:03.400` or `02:03.400` -> seconds. The two shapes the format allows, and no others."""
    parts = stamp.replace(",", ".").split(":")
    if len(parts) == 2:
        parts = ["0", *parts]
    hours, minutes, rest = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def read(data: bytes | str) -> list[tuple[float, str]]:
    """Every cue as `(start seconds, words)`, in start order.

    Overlapping cues are kept — two people talking at once is two cues and dropping either would lose
    words — and they are ordered by their start, because that is the only order a one-stamp-per-line
    format can express. A cue's own line breaks are reading-width, so they become one line of words.
    """
    text = data.decode("utf-8-sig", "replace") if isinstance(data, bytes) else (data or "")
    if not is_webvtt(text):
        return []
    cues: list[tuple[float, str]] = []
    start: float | None = None
    words: list[str] = []

    def keep() -> None:
        if start is not None and (said := " ".join(words).strip()):
            cues.append((start, said))

    skipping = False
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if m := _CUE.match(line):
            keep()
            start, words, skipping = seconds(m["start"]), [], False
            continue
        if not line:                      # a blank line ends whatever block we are in
            keep()
            start, words, skipping = None, [], False
            continue
        if _BLOCK.match(line):            # NOTE / STYLE / REGION: everything until the blank line
            keep()
            start, words, skipping = None, [], True
            continue
        if skipping or start is None:     # a cue identifier, the header, a block's body
            continue
        words.append(html.unescape(_TAGS.sub("", line)).strip())
    keep()
    return sorted(cues, key=lambda c: c[0])


def as_lrc(cues: list[tuple[float, str]]) -> str:
    """The lines as an `.lrc`: `[mm:ss.xx] words`, which is what everything else in noaap reads."""
    out = []
    for start, said in cues:
        minutes, rest = divmod(max(start, 0.0), 60)
        out.append(f"[{int(minutes):02d}:{rest:05.2f}] {said}")
    return "\n".join(out)
