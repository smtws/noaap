"""Patreon's own title conventions (DESIGN §9, slice 70).

A post title is not a song title. What a creator writes there is *post* language — who it is for,
what kind of thing it is, sometimes an episode number — and none of that belongs in a track's title.

**Every rule here is one I can point at in yt-dlp's own test data or in the shape of the API**, and
nothing here guesses at an artist. The hard case is the one this provider will meet most: a solo
musician whose campaign is their own name, where the creator *is* the artist and the post title
carries only the song. That is why `creator_is_artist` is deliberately narrow.
"""

from __future__ import annotations

import re

from .text import key as text_key

# What a creator writes around the thing itself. Each of these is a *post* convention: it says what
# kind of post this is or who may see it, never what the song is called.
_MARKERS = (
    r"patron[- ]only", r"patrons? only", r"early access", r"exclusive",
    r"free for all", r"public", r"announcement", r"update",
)
_ANY_MARKER = "|".join(_MARKERS)
_BRACKETED = re.compile(rf"^\s*[\[(]\s*(?:{_ANY_MARKER})\s*[\])]\s*[-–—:]?\s*", re.I)
_TRAILING = re.compile(rf"\s*[\[(]\s*(?:{_ANY_MARKER})\s*[\])]\s*$", re.I)
# `Episode 166: David Smalley of Dogma Debate` — yt-dlp's own first Patreon test case. The number is
# the *post's*, not a track's, and slice 70 invents no track numbers, so it is not kept as one.
_EPISODE = re.compile(r"^\s*(?:episode|ep\.?|part|pt\.?|#)\s*\d+\s*[-–—:.]\s*", re.I)
# a file's own extension, when a media's `alt_title` is its file name
_EXTENSION = re.compile(r"\.(?:mp3|wav|flac|m4a|aac|ogg|opus|aiff?)$", re.I)
# `winter-light-take-1` — a file name a person never typed as a title
_SEPARATORS = re.compile(r"[_]+")


def clean_title(title: str, channel: str | None = None) -> tuple[str | None, str]:
    """`(artist, title)` as Patreon's conventions read them.

    The artist is **almost always `None`** here, and that is the point: a post title does not carry
    "Artist - Song" the way a YouTube video title does, and pretending it might would put the creator's
    words where a musician's name belongs. The one exception is the shape a creator uses deliberately —
    `Artist — Song` with a real dash — and only when the left side is not the creator's own name.
    """
    text = (title or "").strip()
    text = _EXTENSION.sub("", text)
    text = _SEPARATORS.sub(" ", text)
    for pattern in (_BRACKETED, _TRAILING, _EPISODE):
        text = pattern.sub("", text).strip()
    text = re.sub(r"\s{2,}", " ", text).strip(" -–—:")
    artist = None
    if m := re.match(r"^(.{2,60}?)\s+[–—]\s+(.{2,})$", text):   # an em or en dash, never a hyphen
        left, right = m[1].strip(), m[2].strip()
        if channel is None or text_key(left) != text_key(channel):
            artist, text = left, right
    return artist, text or (title or "").strip()


def creator_is_artist(creator: str | None) -> str | None:
    """The artist a creator's name stands for, if it stands for one.

    **Narrow on purpose.** A campaign is a person's page, not a release's credit: *Cognitive Dissonance
    Podcast* is not an artist and neither is *A Creator's Music Corner*. So a creator's name is taken as
    an artist only when it is a plain name — no word that says "this is a page about music" rather than
    a musician.
    """
    name = (creator or "").strip()
    if not name or len(name) > 60:
        return None
    if re.search(r"\b(podcast|show|channel|studio|press|records?|media|patreon|page|corner"
                 r"|zone|hq|official|community|club|team|productions?)\b", name, re.I):
        return None
    if re.search(r"[|/•]|&&|\bmusic\b.*\b(corner|zone|page)\b", name, re.I):
        return None
    return name
