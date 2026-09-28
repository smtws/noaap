"""Text the whole program shares: keys for comparing names, and feat. credits.

Split out of `titles.py` in P49. What stayed there is YouTube's *title conventions* — "(Official
Video)", a channel's name standing in for an artist, reversed "Song - Artist" — which belong to the
provider that invented them. What is here has nothing to do with where a track came from, and is
used by MusicBrainz matching, the lyrics lookup, search and the web page alike.
"""
from __future__ import annotations

import re
import unicodedata

_FEAT_WORD = re.compile(r"\b(?:feat\.?|ft\.?|featuring)\s", re.I)
_FEAT_TAIL = re.compile(r"\s*[(\[]?\s*\b(feat\.?|ft\.?|featuring)\s+(?P<guests>[^)\]]+?)\s*[)\]]?\s*$", re.I)
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")  # bidi/zero-width marks

# shared with the YouTube title conventions in `titles.py`, which is why these are not private
BRACKETS = re.compile(r"\s*[(\[\u3010]([^()\[\]\u3010\u3011]*)[)\]\u3011]")
SEPARATOR = re.compile(r"(?:\s+[-\u2013\u2014~]{1,2}\s+|[-\u2013\u2014~]{1,2}\s+(?=[A-Z\u00c0-\u00d6\u00d8-\u00de])|\s*:\s+)")


def clean_text(text: str) -> str:
    """NFC, and without the invisible marks YouTube titles carry ('In The Nursery \u200e- …')."""
    return _INVISIBLE.sub("", unicodedata.normalize("NFC", text))


def natural_key(text: str) -> list[object]:
    """Sort key that reads digit runs as numbers: Vol. 2 before Vol. 10.

    Punctuation and spacing inside the words are ignored, so "Vol.9" and "Vol. 10" are
    ordered by their number, not by the dot.
    """
    parts = re.split(r"(\d+)", text or "")
    return [(1, int(p), "") if p.isdigit() else (0, 0, re.sub(r"\W+", " ", p).strip().casefold()) for p in parts]


def words_of(text: str) -> set[str]:
    return {w.casefold() for w in re.findall(r"\w+", text or "")}


def strip_leading_artist(artist: str, title: str) -> str:
    """'Metallica: Nothing Else Matters' with artist Metallica -> 'Nothing Else Matters'."""
    if not artist or not title:
        return title
    parts = SEPARATOR.split(title, maxsplit=1)
    if len(parts) == 2 and parts[1].strip() and key(parts[0]).startswith(key(artist)):
        return parts[1].strip()
    return title


def split_feat(artist: str) -> tuple[str, str | None]:
    """'Feuerschwanz ft. Melissa Bonny' -> ('Feuerschwanz', 'ft. Melissa Bonny').

    Guest credits belong in the title; the artist field stays the performer, so the library
    does not grow an entry per collaboration.
    """
    m = _FEAT_TAIL.search(artist)
    if not m or not m["guests"].strip():
        return artist, None
    main = artist[: m.start()].strip(" -–—,&")
    return (main or artist), (None if not main else f"{m[1]} {m['guests'].strip()}")


def move_feat(artist: str, title: str) -> tuple[str, str]:
    """Take a guest credit out of the artist and append it to the title, once."""
    main, guests = split_feat(artist)
    if not guests:
        return artist, title
    if _FEAT_WORD.search(title):  # the title already names them, anywhere in it
        return main, title
    return main, f"{title} {guests}"


def strip_self_feat(artist: str, title: str) -> str:
    """'Gary Jules' - 'Mad World (feat. Gary Jules)': the guest is the artist. Drop the credit."""
    if not artist:
        return title

    def drop(m: re.Match[str]) -> str:
        guests = _FEAT_WORD.split(m[1], maxsplit=1)
        return "" if len(guests) == 2 and key(guests[1]) == key(artist) else m[0]

    out = BRACKETS.sub(drop, title)
    if (m := _FEAT_TAIL.search(out)) and key(m["guests"]) == key(artist):
        out = out[: m.start()]
    return re.sub(r"\s+", " ", out).strip(" -–—~")


def key(s: str) -> str:
    """Comparison key: case- and punctuation-insensitive."""
    return re.sub(r"\W+", "", s.casefold())
