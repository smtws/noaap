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


def stamp(seconds_in: float) -> str:
    """`[mm:ss.xx]` — **rounded to hundredths first, and then split**.

    Splitting first and rounding after invents a stamp the clock has no name for: 59.996 s came out
    as `[00:60.00]` and 3599.9951 as `[59:60.00]` (R-258, defect 3). Rounding to the unit that is
    actually written, before dividing, cannot do that.
    """
    hundredths = round(max(seconds_in, 0.0) * 100)
    minutes, rest = divmod(hundredths, 6000)
    return f"[{minutes:02d}:{rest / 100:05.2f}]"


def as_lrc(cues: list[tuple[float, str]]) -> str:
    """The lines as an `.lrc`: `[mm:ss.xx] words`, which is what everything else in noaap reads."""
    return "\n".join(f"{stamp(start)} {said}" for start, said in cues)


# -- captions served as a playlist (§9, slice 76) ----------------------------------------------------
#
# What a live post really offers is not a file: `ext: vtt` with `protocol: m3u8_native` and an address
# ending `subtitles.m3u8`. The *segments* are WebVTT; the address is an HLS media playlist listing
# them. That is the shape this half reads. **It still knows no site**: it is handed text and gives
# back what is in it, and every request is the provider's to make and to check.

_SEGMENT_CAP_REASON = "its caption playlist lists more segments than this program will fetch"
MASTER = "#EXT-X-STREAM-INF"     # a playlist of playlists: not the one we were promised
KEY = "#EXT-X-KEY"               # encrypted segments. No key is ever fetched, so this is refused
ENDLIST = "#EXT-X-ENDLIST"       # without it the list is still being written: a live stream
_MAP = re.compile(r"X-TIMESTAMP-MAP\s*=\s*(?P<body>\S+)", re.I)
_LOCAL = re.compile(r"LOCAL\s*:\s*((?:\d{1,3}:)?\d{1,2}:\d{2}[.,]\d{1,3})", re.I)
_MPEGTS = re.compile(r"MPEGTS\s*:\s*(\d+)", re.I)
MPEG_CLOCK = 90000.0             # the 90 kHz clock an MPEG-TS timestamp counts in


def is_playlist(data: bytes | str) -> bool:
    """Whether this is an HLS playlist rather than a caption file, by its own required first line."""
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else (data or "")
    return text.lstrip("﻿ \r\n\t").startswith("#EXTM3U")


def playlist(data: bytes | str) -> tuple[list[str], str | None]:
    """`(segment addresses in order, refusal)` — the addresses as written, relative or absolute.

    Resolving and checking them is the caller's, because only a provider knows which hosts are its
    own. Three shapes are refused outright, each with its own sentence: a master playlist is not the
    list we asked for, an encrypted one would need a key this program will never fetch, and one with
    no `#EXT-X-ENDLIST` is still being written.
    """
    text = data.decode("utf-8-sig", "replace") if isinstance(data, bytes) else (data or "")
    if not is_playlist(text):
        return [], "its caption playlist is not a playlist"
    lines = [one.strip() for one in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    if any(one.startswith(MASTER) for one in lines):
        return [], "its captions point at a playlist of playlists, not at caption segments"
    if any(one.startswith(KEY) and "METHOD=NONE" not in one.upper() for one in lines):
        return [], "its caption segments are encrypted, and this program fetches no keys"
    if not any(one.startswith(ENDLIST) for one in lines):
        return [], "its caption playlist is still being written (no end marker), so it is not read"
    return [one for one in lines if one and not one.startswith("#")], None


def timestamp_base(text: str) -> tuple[float | None, bool]:
    """`(the segment's own zero in seconds, whether it says)` from `X-TIMESTAMP-MAP`.

    `LOCAL:00:00:00.000,MPEGTS:900000` means *this segment's cue stamp `LOCAL` is that MPEG-TS
    instant*. The base is `MPEGTS/90000 - LOCAL`: add it to a cue and you have the time on the
    stream's own clock. A map that is there but cannot be read gives `(None, True)` — present and
    unusable, which is a refusal and not a zero.
    """
    if not (m := _MAP.search(text or "")):
        return None, False
    body = m["body"]
    local, mpegts = _LOCAL.search(body), _MPEGTS.search(body)
    if not local or not mpegts:
        return None, True
    return int(mpegts[1]) / MPEG_CLOCK - seconds(local[1]), True


def read_segments(texts: list[str]) -> tuple[list[tuple[float, str]], str | None]:
    """Every segment's cues as one list on the audio's clock — or a refusal, and nothing partial.

    **What the timestamp map is used for, and what it is not.** The stamps have to land on the audio
    this program produces, which starts at zero (that is measured before any of this). So the *first*
    segment's base is taken as zero and every later segment is shifted by its own base relative to
    it: segments then agree with each other and with an asset that begins where the audio begins.
    The absolute MPEG-TS origin is deliberately **not** trusted — it is the packager's, and the audio
    was copied out of an mp4 that has no such stamps, so treating it as an offset would be a guess.

    Refused, because a wrong constant offset looks right and is not: a map on some segments and not
    others (no common base), a map that cannot be read, and any cue that lands before zero.
    """
    bases: list[float | None] = []
    seen: list[tuple[float, str]] = []
    for text in texts:
        base, present = timestamp_base(text)
        if present and base is None:
            return [], "its captions carry a timestamp map this program cannot read"
        bases.append(base if present else None)
    said = [b is not None for b in bases]
    if any(said) and not all(said):
        return [], "some of its caption segments carry a timestamp map and some do not"
    zero = bases[0] if bases and bases[0] is not None else 0.0
    for text, base in zip(texts, bases, strict=True):
        shift = (base - zero) if base is not None else 0.0
        for start, said_words in read(text):
            if start + shift < -0.001:
                return [], "its caption stamps land before the start of the audio"
            seen.append((round(max(start + shift, 0.0), 3), said_words))
    # a cue that spans a segment border is written in both of them, and is one cue
    return sorted(dict.fromkeys(seen), key=lambda c: c[0]), None
